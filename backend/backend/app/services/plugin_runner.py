"""
插件子进程宿主。

功能：
1. 每个机器人对应一个常驻子进程，事件按到达顺序逐条下发，插件在同一条通道上回传结果；
2. 子进程上报的动作请求全部转给运行期闸门，闸门放行后才由主进程代调 OneBot；
3. 事件超时、协议违规或子进程崩溃时立即杀掉并重建，下一次事件自动恢复。

约定：
- 子进程异常绝不能影响 OneBot 事件主流程，所有失败都只记日志；
- 同一机器人连续失败达到上限后进入短暂冷却，避免坏插件把 CPU 打满；
- 插件代码、配置与凭据都留在主进程之外，子进程只拿到本次事件需要的上下文。
"""

import asyncio
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from app.common.config import Settings
from app.common.database import Database
from app.services.plugin_gate_runtime import ActionCaller, RuntimeGate
from app.services.plugin_protocol import (
    MAX_LINE_BYTES,
    TYPE_ACTION,
    TYPE_DONE,
    TYPE_LOG,
    TYPE_READY,
    build_action_result,
    build_event_message,
    build_shutdown,
    decode_message,
    encode_message,
    message_type,
)


logger = logging.getLogger("uvicorn.error")

# 后端工程根目录，作为子进程的工作目录与 PYTHONPATH。
BACKEND_ROOT = Path(__file__).resolve().parents[2]
# 子进程退出等待上限：先礼后兵，超时直接 terminate / kill。
STOP_TIMEOUT = 3.0
# 连续失败次数上限与冷却时长，避免坏插件反复拉起。
MAX_FAILURES = 3
COOLDOWN_SECONDS = 60.0


class _BotRunner:
    """单个机器人的插件子进程。"""

    def __init__(self, bot_qq: str, settings: Settings, gate: RuntimeGate) -> None:
        """记录机器人标识、配置与闸门。"""
        self.bot_qq = bot_qq
        self.settings = settings
        self.gate = gate
        self.process: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task | None = None
        self.stderr_reader: asyncio.Task | None = None
        self.events: dict[str, asyncio.Future] = {}
        self.actions: set[asyncio.Task] = set()
        self.installations: dict[str, dict[str, Any]] = {}
        self.write_lock = asyncio.Lock()
        self.dispatch_lock = asyncio.Lock()
        self.failures = 0
        self.retry_at = 0.0

    async def _ensure_started(self) -> bool:
        """确保子进程可用，已退出时重新拉起。"""
        if self.process is not None and self.process.returncode is None:
            return True
        loop = asyncio.get_running_loop()
        if self.failures >= MAX_FAILURES and loop.time() < self.retry_at:
            return False
        env = os.environ.copy()
        env["PYTHONPATH"] = (
            str(BACKEND_ROOT)
            if not env.get("PYTHONPATH")
            else f"{BACKEND_ROOT}{os.pathsep}{env['PYTHONPATH']}"
        )
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        options: dict[str, Any] = {}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "app.services.plugin_runner_main",
                cwd=str(BACKEND_ROOT),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=MAX_LINE_BYTES + 1,
                **options,
            )
        except Exception as error:
            logger.error("插件子进程启动失败，机器人=%s，错误=%s", self.bot_qq, error)
            self._mark_failure()
            return False
        self.process = process
        self.reader = asyncio.create_task(self._read_stdout())
        self.stderr_reader = asyncio.create_task(self._read_stderr())
        logger.info("插件子进程已启动，机器人=%s，pid=%s", self.bot_qq, process.pid)
        return True

    async def _read_stdout(self) -> None:
        """读取子进程上报的报文。"""
        process = self.process
        if process is None or process.stdout is None:
            return
        while True:
            try:
                line = await process.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError):
                logger.warning("插件子进程报文超长，按协议违规重启，机器人=%s", self.bot_qq)
                await self._restart("报文超长")
                return
            except asyncio.CancelledError:
                raise
            if not line:
                break
            payload = decode_message(line)
            if payload is None:
                continue
            kind = message_type(payload)
            if kind == TYPE_DONE:
                self._resolve_event(
                    str(payload.get("id") or ""),
                    bool(payload.get("ok")),
                    str(payload.get("error") or ""),
                )
            elif kind == TYPE_ACTION:
                task = asyncio.create_task(self._run_action(payload))
                self.actions.add(task)
                task.add_done_callback(self.actions.discard)
            elif kind == TYPE_LOG:
                self._log_plugin(payload)
            elif kind == TYPE_READY:
                continue
        self._fail_pending("插件子进程已退出")

    async def _read_stderr(self) -> None:
        """回收子进程标准错误，避免管道写满导致子进程阻塞。"""
        process = self.process
        if process is None or process.stderr is None:
            return
        while True:
            line = await process.stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                logger.info("[插件 stderr] 机器人=%s %s", self.bot_qq, text[:500])

    def _log_plugin(self, payload: dict[str, Any]) -> None:
        """把插件日志转成统一日志。"""
        level = str(payload.get("level") or "info").lower()
        message = str(payload.get("message") or "")[:2000]
        logger.log(
            logging.ERROR if level == "error" else logging.INFO,
            "[插件] 插件=%s 机器人=%s %s",
            payload.get("plugin_id"),
            self.bot_qq,
            message,
        )

    def _resolve_event(self, event_id: str, ok: bool, error: str) -> None:
        """唤醒等待中的事件分发。"""
        future = self.events.pop(event_id, None)
        if future is not None and not future.done():
            future.set_result((ok, error))

    def _fail_pending(self, reason: str) -> None:
        """子进程异常时让所有等待中的事件立即返回。"""
        for future in list(self.events.values()):
            if not future.done():
                future.set_result((False, reason))
        self.events.clear()

    def _mark_failure(self) -> None:
        """记录一次失败，连续失败达到上限后进入冷却。"""
        self.failures += 1
        if self.failures >= MAX_FAILURES:
            self.retry_at = asyncio.get_running_loop().time() + COOLDOWN_SECONDS
            logger.warning(
                "插件子进程连续失败 %s 次，机器人=%s 冷却 %s 秒",
                self.failures,
                self.bot_qq,
                int(COOLDOWN_SECONDS),
            )

    async def _write(self, payload: dict[str, Any]) -> bool:
        """向子进程写一帧报文。"""
        process = self.process
        if process is None or process.stdin is None or process.returncode is not None:
            return False
        async with self.write_lock:
            try:
                process.stdin.write(encode_message(payload))
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, RuntimeError):
                return False
        return True

    async def _run_action(self, payload: dict[str, Any]) -> None:
        """
        处理插件发来的动作请求。
        """
        action_id = str(payload.get("id") or "")
        plugin_id = str(payload.get("plugin_id") or "")
        params = payload.get("params")
        if not isinstance(params, dict):
            params = {}
        try:
            installation = self.installations.get(plugin_id) or {
                "plugin_id": plugin_id,
                "bot_qq": self.bot_qq,
                "granted_capabilities": [],
                "allowed_domains": [],
            }
            ok, data, error = await self.gate.execute(
                installation,
                str(payload.get("action") or ""),
                params,
                source="plugin",
            )
        except Exception as error:  # 兜底：绝不让动作请求悬空
            logger.warning(
                "插件动作请求处理异常，插件=%s 机器人=%s 错误类型=%s 错误=%s",
                plugin_id,
                self.bot_qq,
                type(error).__name__,
                error,
            )
            ok, data, error = False, {}, f"动作请求处理失败：{error}"
        await self._write(build_action_result(action_id, ok, data, error))

    async def dispatch(
        self,
        installation: dict[str, Any],
        event: dict[str, Any],
        source: str,
    ) -> bool:
        """
        把一条事件下发给插件并等待处理结果。

        Returns:
            True 表示插件处理完成且未报错。
        """
        async with self.dispatch_lock:
            if not await self._ensure_started():
                return False
            plugin_id = str(installation.get("plugin_id") or "")
            self.installations[plugin_id] = installation
            event_id = uuid.uuid4().hex
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            self.events[event_id] = future
            if not await self._write(
                build_event_message(event_id, installation, event, source)
            ):
                self.events.pop(event_id, None)
                self._mark_failure()
                await self._restart("事件下发失败")
                return False
            try:
                ok, error = await asyncio.wait_for(
                    future, timeout=float(self.settings.plugin_event_timeout)
                )
            except asyncio.TimeoutError:
                self.events.pop(event_id, None)
                self._mark_failure()
                await self._restart("事件处理超时")
                return False
            if error:
                logger.info(
                    "插件处理事件失败，插件=%s 机器人=%s 原因=%s",
                    plugin_id,
                    self.bot_qq,
                    error[:500],
                )
            else:
                self.failures = 0
            return ok

    async def _restart(self, reason: str) -> None:
        """杀掉子进程，等待下一次事件时重新拉起。"""
        logger.warning("重启插件子进程，机器人=%s，原因=%s", self.bot_qq, reason)
        await self.stop()
        self._fail_pending(reason)
        self.process = None

    async def stop(self) -> None:
        """停止子进程并回收所有后台任务。"""
        process = self.process
        if process is not None and process.returncode is None:
            await self._write(build_shutdown())
            try:
                await asyncio.wait_for(process.wait(), timeout=STOP_TIMEOUT)
            except asyncio.TimeoutError:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=STOP_TIMEOUT)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
        for task in (self.reader, self.stderr_reader):
            if task is not None and not task.done():
                task.cancel()
        for task in list(self.actions):
            task.cancel()
        self._fail_pending("插件子进程已停止")
        self.process = None


class SubprocessPluginRuntime:
    """按机器人管理子进程的插件执行单元。"""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        action_caller: ActionCaller,
    ) -> None:
        """构建闸门并准备机器人进程表。"""
        self.settings = settings
        self.gate = RuntimeGate(settings, database, action_caller)
        self.runners: dict[str, _BotRunner] = {}

    def _runner_for(self, bot_qq: str) -> _BotRunner:
        """取出或创建机器人对应的子进程宿主。"""
        runner = self.runners.get(bot_qq)
        if runner is None:
            runner = _BotRunner(bot_qq, self.settings, self.gate)
            self.runners[bot_qq] = runner
        return runner

    async def handle_event(
        self,
        event: dict[str, Any],
        installations: list[dict[str, Any]],
        source: str,
    ) -> None:
        """把事件依次分发给该机器人上已启用的插件。"""
        if not installations:
            return
        bot_qq = str(
            event.get("_bot_qq")
            or event.get("self_id")
            or installations[0].get("bot_qq")
            or ""
        )
        if not bot_qq:
            return
        runner = self._runner_for(bot_qq)
        for installation in installations:
            await runner.dispatch(installation, event, source)

    async def close(self) -> None:
        """停止所有子进程并释放闸门资源。"""
        for runner in list(self.runners.values()):
            await runner.stop()
        self.runners.clear()
        await self.gate.close()
