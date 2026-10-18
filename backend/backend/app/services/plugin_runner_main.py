"""
插件子进程入口。

功能：
1. 由主进程以 ``python -m app.services.plugin_runner_main`` 拉起，一个机器人对应一个常驻进程；
2. 按需加载插件包里的入口文件，把事件分发给插件用 SDK 注册的处理函数；
3. 每次事件处理结束回传 done 报文，异常一律捕获并通过 done 的 error 字段上报，不让子进程崩溃。

约定：
- 标准输出只用于协议报文，插件自己 print 的内容会被重定向到标准错误，避免污染报文通道；
- 插件之间共享同一个进程，事件串行处理，单个插件抛异常不影响同一进程里的其他插件；
- 本进程不连数据库、不持有凭据，所有动作都必须回到主进程的闸门。
"""

import asyncio
import importlib.util
import sys
import traceback
from pathlib import Path
from typing import Any

from app.services import plugin_sdk
from app.services.plugin_protocol import (
    MAX_LINE_BYTES,
    TYPE_ACTION_RESULT,
    TYPE_EVENT,
    TYPE_SHUTDOWN,
    build_done,
    build_log,
    build_ready,
    decode_message,
    message_type,
)


# 已加载插件：插件标识 → 包目录，包目录变化说明换了版本，需要重新加载。
_loaded: dict[str, str] = {}
# 事件串行执行，但读取报文的循环必须保持运行，否则插件等动作结果时会与主进程互相等待。
_event_lock = asyncio.Lock()


def _module_name(plugin_id: str) -> str:
    """把插件标识转换成合法的模块名。"""
    safe = "".join(ch if ch.isalnum() else "_" for ch in plugin_id)
    return f"napcat_plugin_{safe}"


def _drop_previous(plugin_id: str, previous_dir: str, root: Path) -> None:
    """
    卸载旧版本插件：清处理函数、清模块缓存、把旧包目录移出 sys.path。

    插件升级后包目录会变。若只重新加载入口文件，会出现两类问题：
    一是旧版本注册的处理函数还在，同一个事件被执行两遍；二是插件自己拆的
    子模块（如 ``vparse``）仍留在 ``sys.modules`` 里，``import`` 会拿到旧实现。

    Args:
        plugin_id: 插件标识。
        previous_dir: 旧版本包目录。
        root: 新版本包目录。
    """
    plugin_sdk.reset_plugin(plugin_id)
    old_root = str(Path(previous_dir).resolve())
    new_root = str(root.resolve())
    for module_name, module in list(sys.modules.items()):
        location = getattr(module, "__file__", "") or ""
        if not location:
            continue
        try:
            resolved = str(Path(location).resolve())
        except OSError:
            continue
        if resolved.startswith(old_root) and not resolved.startswith(new_root):
            sys.modules.pop(module_name, None)
    while old_root in sys.path:
        sys.path.remove(old_root)


def _load_plugin(plugin_id: str, package_dir: str, entry: str) -> None:
    """
    加载插件入口文件并注册处理函数。

    Args:
        plugin_id: 插件标识。
        package_dir: 插件包落盘目录。
        entry: 入口文件相对路径。
    Raises:
        RuntimeError: 包目录或入口文件不存在。
    """
    if not package_dir or not entry:
        raise RuntimeError("插件缺少包目录或入口文件")
    root = Path(package_dir)
    target = root / entry
    if not target.is_file():
        raise RuntimeError(f"插件入口文件不存在：{target}")
    previous = _loaded.get(plugin_id)
    if previous == package_dir:
        return
    name = _module_name(plugin_id)
    if previous:
        # 版本升级：包目录变了，先把旧版本彻底卸载再加载新版本。
        _drop_previous(plugin_id, previous, root)
    spec = importlib.util.spec_from_file_location(name, target)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载插件入口：{target}")
    module = importlib.util.module_from_spec(spec)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    sys.modules[name] = module
    plugin_sdk.begin_loading(plugin_id)
    try:
        spec.loader.exec_module(module)
    finally:
        plugin_sdk.end_loading()
    _loaded[plugin_id] = package_dir


async def _handle_event(payload: dict[str, Any]) -> None:
    """分发一条事件，并把结果回传给主进程。"""
    event_id = str(payload.get("id") or "")
    plugin_id = str(payload.get("plugin_id") or "")
    event = payload.get("event")
    if not isinstance(event, dict):
        plugin_sdk.emit(build_done(event_id, False, "事件内容不是对象"))
        return
    async with _event_lock:
        await _run_event(event_id, plugin_id, event, payload)


async def _run_event(
    event_id: str,
    plugin_id: str,
    event: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    """串行执行单条事件，异常一律转成 done 报文。"""
    view = plugin_sdk.PluginHost(
        plugin_id=plugin_id,
        bot_qq=str(payload.get("bot_qq") or ""),
        config=dict(payload.get("config") or {}),
        granted=list(payload.get("granted") or []),
        domains=list(payload.get("domains") or []),
    )
    try:
        _load_plugin(
            plugin_id,
            str(payload.get("package_dir") or ""),
            str(payload.get("entry") or ""),
        )
        handlers = plugin_sdk.handlers_for(plugin_id, event)
        plugin_sdk.host.bind(view)
        for handler in handlers:
            await handler(event, view)
        plugin_sdk.emit(build_done(event_id, True))
    except Exception as error:  # 插件代码可能抛出任意异常，必须全部兜住
        detail = traceback.format_exc(limit=6)
        plugin_sdk.emit(build_log(plugin_id, detail, "error"))
        plugin_sdk.emit(build_done(event_id, False, f"{type(error).__name__}: {error}"))
    finally:
        plugin_sdk.host.bind(None)


async def _dispatch(payload: dict[str, Any], tasks: set[asyncio.Task]) -> bool:
    """
    处理一帧报文。

    Args:
        payload: 报文内容。
        tasks: 正在执行的事件任务集合。
    Returns:
        False 表示收到退出指令。
    """
    kind = message_type(payload)
    if kind == TYPE_EVENT:
        task = asyncio.create_task(_handle_event(payload))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return True
    if kind == TYPE_ACTION_RESULT:
        plugin_sdk.resolve_action(
            str(payload.get("id") or ""),
            bool(payload.get("ok")),
            dict(payload.get("data") or {}),
            str(payload.get("error") or ""),
        )
        return True
    if kind == TYPE_SHUTDOWN:
        return False
    return True


async def main() -> int:
    """读取主进程报文直到退出。"""
    protocol_out = sys.stdout.buffer
    sys.stdout = sys.stderr
    plugin_sdk.set_output(protocol_out)
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    plugin_sdk.emit(build_ready())
    tasks: set[asyncio.Task] = set()
    while True:
        line = await asyncio.to_thread(sys.stdin.readline, MAX_LINE_BYTES)
        if not line:
            break
        if not line.endswith("\n"):
            # 单行报文超过上限，丢弃这一帧并把剩余内容读干净，避免污染后续解析。
            while line and not line.endswith("\n"):
                line = await asyncio.to_thread(sys.stdin.readline, MAX_LINE_BYTES)
            continue
        payload = decode_message(line)
        if payload is None:
            continue
        if not await _dispatch(payload, tasks):
            break
    for task in list(tasks):
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
