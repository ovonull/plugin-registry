"""
后台轮询：定时拉取各仓库动态并推送到订阅会话。

为什么放后台：宿主对单个事件的处理有 10 秒超时，超时会把插件子进程杀掉重启，
挨个拉四五个仓库的 GitHub 接口必然超时。所以轮询做成常驻 asyncio 任务，
事件处理函数只负责解析指令和唤醒它。

约定：
- 每个机器人一个轮询任务，随第一条消息懒启动，之后常驻到进程结束；
- 宿主视图（host）随每条消息事件刷新，配置改动在下一条消息后生效；
- 每个仓库单独配置：监控内容、分支、检查周期、凭证、通知开关、显示名、打码、Star 阈值；
- 首次检查只建基线不推送；后开启的监控类型同样先建基线，不会把历史动态当新闻；
- 连续失败达到阈值才告警一次（可关），成功后自动复位；
- 状态文件的一切「读 -> 改 -> 写」都走 mutate_state 在锁内完成：网络请求在锁外，
  所以指令不会被轮询堵住，两边也不会互相覆盖对方的修改。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from app.services.plugin_sdk import PluginCallError

from ghub import client as gh_client
from ghub import formatter as gh_formatter
from ghub import store as gh_store
from ghub.drafts import refresh_drafts

# 相邻仓库之间的请求间隔，避免把 GitHub 的次级限流打满。
REPO_INTERVAL = 0.4

# 每条动态之间的发送间隔，QQ 侧对高频群消息敏感。
SEND_INTERVAL = 0.5

# 单次轮询单个仓库最多推送多少条，防止积压后一次性刷屏。
MAX_PUSH_PER_REPO = 10

# 手动「gh 检查」的冷却时间。
MANUAL_COOLDOWN = 30.0

# 循环等待的兜底时长：间隔配得再短也不至于空转。
MIN_WAIT_SECONDS = 30.0

# 最近错误在状态里最多存多少字，告警消息要用。
LAST_ERROR_CHARS = 120

# mutate_state 的回调类型：拿到状态字典，同步改，返回值原样带回。
Mutator = Callable[[dict[str, Any]], Any]


class GitHubPoller:
    """常驻轮询任务，一个机器人一个实例。"""

    def __init__(self, host: Any, state_path: Any, config: dict[str, Any]) -> None:
        """
        Args:
            host: 首个事件捕获的宿主视图。
            state_path: 本机器人的状态文件路径。
            config: 安装配置快照。
        """
        self._host = host
        self._state_path = state_path
        self._config = dict(config or {})
        self._task: asyncio.Task | None = None
        self._wakeup = asyncio.Event()
        self._last_manual = 0.0
        self._warned: set[str] = set()
        # 保护状态文件的短临界区锁；回调内只做同步操作，绝不 await。
        self._lock = asyncio.Lock()

    # ---- 生命周期 ----

    def refresh(self, host: Any, config: dict[str, Any]) -> None:
        """消息事件里刷新视图与配置快照，让配置改动及时生效。"""
        self._host = host
        self._config = dict(config or {})
        # 能力可能被重新授权，清掉已提醒记录，允许再次提醒。
        self._warned.clear()

    def start(self) -> None:
        """启动后台任务，重复调用只启动一次。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """取消后台任务。"""
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def trigger(self, now: float | None = None) -> bool:
        """
        请求立即轮询一次。

        Args:
            now: 当前时间戳，测试时可注入。
        Returns:
            True 表示已唤醒轮询；False 表示太频繁，被冷却挡下。
        """
        moment = float(now if now is not None else time.time())
        if moment - self._last_manual < MANUAL_COOLDOWN:
            return False
        self._last_manual = moment
        self._wakeup.set()
        return True

    async def _run(self) -> None:
        """主循环：等一个间隔或被唤醒，然后轮询一轮。"""
        while True:
            try:
                await asyncio.wait_for(
                    self._wakeup.wait(),
                    timeout=max(MIN_WAIT_SECONDS, self._interval_seconds()),
                )
            except asyncio.TimeoutError:
                pass
            self._wakeup.clear()
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # 轮询自身出问题也不能让任务死掉
                self._log(f"轮询任务异常：{type(error).__name__}: {error}", "error")

    # ---- 状态读写（对外统一入口） ----

    async def mutate_state(self, mutate: Mutator) -> Any:
        """
        在锁内完成一次「读状态 -> 改 -> 写状态」。

        Args:
            mutate: 同步回调，收到状态字典，直接改它，返回值原样带回。
        Returns:
            回调的返回值。
        """
        async with self._lock:
            state = gh_store.load_state(self._state_path)
            result = mutate(state)
            gh_store.save_state(self._state_path, state)
            return result

    # ---- 配置读取 ----

    def _interval_seconds(self) -> float:
        """轮询间隔秒数，1 分钟到 1 天。"""
        try:
            value = float(str(self._config.get("check_interval") or "").strip())
        except (TypeError, ValueError):
            return 300.0
        if value <= 0:
            return 300.0
        return max(1.0, min(value, 24 * 60)) * 60.0

    def _item_limit(self) -> int:
        """每类条目保留/对比条数，1 到 50。"""
        try:
            value = int(float(str(self._config.get("item_limit") or "").strip()))
        except (TypeError, ValueError):
            return gh_store.DEFAULT_ITEM_LIMIT
        if value <= 0:
            return gh_store.DEFAULT_ITEM_LIMIT
        return min(value, 50)

    def _timeout(self) -> float:
        """单次请求超时，1 到 30 秒。"""
        try:
            value = float(str(self._config.get("request_timeout") or "").strip())
        except (TypeError, ValueError):
            return 20.0
        if value <= 0:
            return 20.0
        return max(1.0, min(value, 30.0))

    def _fail_threshold(self) -> int:
        """失败告警阈值，0 表示不告警。"""
        try:
            value = int(float(str(self._config.get("fail_threshold") or "").strip()))
        except (TypeError, ValueError):
            return 5
        return max(0, min(value, 100))

    def _switch(self, key: str, default: bool) -> bool:
        """读开关型配置。"""
        raw = self._config.get(key)
        if raw is None:
            return default
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in {"1", "true", "on", "yes", "开", "开启", "是"}:
            return True
        if text in {"0", "false", "off", "no", "关", "关闭", "否", ""}:
            return False
        return default

    def _push_enabled(self) -> bool:
        """全局推送开关。"""
        return self._switch("push_enabled", True)

    def _alert_enabled(self) -> bool:
        """失败告警开关。"""
        return self._switch("alert_enabled", True)

    def _global_token(self) -> str:
        """全局默认令牌。"""
        return str(self._config.get("github_token") or "").strip()

    def _push_intro(self) -> str:
        """推送说明：每条例行推送前加一句，留空不加。"""
        return str(self._config.get("push_intro") or "").strip()

    # ---- 轮询 ----

    async def poll_once(self) -> dict[str, int]:
        """
        轮询一轮所有仓库。

        Returns:
            统计信息：仓库数、推送条数、失败仓库数、跳过仓库数。
        """
        stats = {"repos": 0, "pushed": 0, "failed": 0, "skipped": 0}
        host = self._host
        if host is None:
            return stats
        if not host.has("http.outbound"):
            self._warn_capability(host, "http.outbound")
            return stats
        if not host.has("msg.send"):
            self._warn_capability(host, "msg.send")
            return stats

        keys = await self.mutate_state(_list_repo_keys)
        if not keys:
            return stats

        client = gh_client.GitHubClient(host, timeout=self._timeout())
        limit = self._item_limit()
        threshold = self._fail_threshold()
        now = time.time()
        for key in keys:
            stats["repos"] += 1

            def read_entry(
                state: dict[str, Any], key: str = key
            ) -> dict[str, Any] | None:
                """锁内取该仓库的配置快照。"""
                entry = (state.get("repos") or {}).get(key)
                if not isinstance(entry, dict):
                    return None
                gh_store.ensure_entry_defaults(entry)
                return {
                    "repo": str(entry.get("repo") or key),
                    "token": gh_store.credential_of(entry, state) or self._global_token(),
                    "branch": str(entry.get("branch") or ""),
                    "watch": gh_store.watch_of(entry),
                    "interval": float(entry.get("check_interval") or 0),
                    "notify": bool(entry.get("notify", True)),
                    "display": str(entry.get("display_name") or "").strip(),
                    "mask": bool(entry.get("mask")),
                    "threshold": int(entry.get("star_threshold") or 1),
                    "last_check": float(entry.get("last_check") or 0),
                }

            conf = await self.mutate_state(read_entry)
            if conf is None:
                continue
            # 每仓库的检查周期不能小于全局间隔，这里只拦「还没到点」的情况。
            if conf["interval"] and now - conf["last_check"] < conf["interval"]:
                stats["skipped"] += 1
                continue

            watch = [kind for kind in conf["watch"] if kind != "star"]
            want_star = "star" in conf["watch"]
            if not watch and not want_star:
                continue

            items: list[dict[str, Any]] = []
            stars: int | None = None
            try:
                if watch:
                    items = await client.list_items(
                        conf["repo"], conf["token"], conf["branch"], watch
                    )
                if want_star:
                    stats_info = await client.repo_stats(conf["repo"], conf["token"])
                    stars = int(stats_info["stars"])
            except gh_client.GitHubError as error:
                stats["failed"] += 1
                reason = str(error)[:LAST_ERROR_CHARS]

                def on_failure(
                    state: dict[str, Any],
                    key: str = key,
                    reason: str = reason,
                    now: float = now,
                    threshold: int = threshold,
                ) -> str:
                    """锁内累计失败次数，首次达到阈值时返回告警文案。"""
                    entry = (state.get("repos") or {}).get(key)
                    if not isinstance(entry, dict):
                        return ""
                    entry["last_error"] = reason
                    if gh_store.record_failure(entry, now, threshold):
                        return reason
                    return ""

                alert = await self.mutate_state(on_failure)
                if alert and self._alert_enabled():
                    await self._alert(key, conf["display"], conf["repo"], alert)
                self._log(f"仓库 {conf['repo']} 拉取失败：{reason}", "warning")
                continue

            def on_success(
                state: dict[str, Any],
                key: str = key,
                items: list[dict[str, Any]] = items,
                limit: int = limit,
                now: float = now,
            ) -> tuple[list[tuple[str, dict[str, Any]]], bool]:
                """锁内记录成功、算 diff、应用基线，返回（事件列表, 是否首轮）。"""
                entry = (state.get("repos") or {}).get(key)
                if not isinstance(entry, dict):
                    return [], False
                gh_store.ensure_entry_defaults(entry)
                gh_store.record_success(entry, now)
                if not items:
                    # 关掉全部条目类监控、或仓库暂时为空：只把基线标记掉。
                    first_run = not entry.get("baseline_done")
                    entry["baseline_done"] = True
                    return [], first_run
                events = gh_store.diff_items(entry, items, limit)
                kinds = sorted({str(item.get("kind") or "") for item in items} - {""})
                known = set(entry.get("baseline_kinds") or [])
                # 后开启的监控类型先建基线，不把历史动态当新闻推出去。
                fresh = [
                    (event, item) for event, item in events if item.get("kind") in known
                ]
                gh_store.apply_items(entry, items, limit, kinds)
                entry["baseline_kinds"] = sorted(known | set(kinds))
                first_run = not entry.get("baseline_done")
                entry["baseline_done"] = True
                return ([], True) if first_run else (fresh, False)

            events, first_run = await self.mutate_state(on_success)
            if first_run:
                self._log(f"仓库 {conf['repo']} 已建立基线，不推送历史动态")
            elif events and conf["notify"] and self._push_enabled():
                targets = await self.mutate_state(_make_target_reader(key))
                stats["pushed"] += await self._push(
                    conf["repo"], conf["display"], conf["mask"], targets, events
                )

            if stars is not None:

                def on_star(
                    state: dict[str, Any],
                    key: str = key,
                    total: int = stars,
                    threshold: int = conf["threshold"],
                ) -> int:
                    """锁内更新 Star 计数，返回本次要通知的累计新增（0 表示不通知）。"""
                    entry = (state.get("repos") or {}).get(key)
                    if not isinstance(entry, dict):
                        return 0
                    gh_store.ensure_entry_defaults(entry)
                    if not entry.get("star_baseline_done"):
                        entry["star_last"] = total
                        entry["star_pending"] = 0
                        entry["star_baseline_done"] = True
                        return 0
                    gain = total - int(entry.get("star_last") or 0)
                    entry["star_last"] = total
                    pending = int(entry.get("star_pending") or 0)
                    if gain > 0:
                        pending += gain
                    entry["star_pending"] = pending
                    if threshold > 0 and pending >= threshold:
                        entry["star_pending"] = 0
                        return pending
                    return 0

                delta = await self.mutate_state(on_star)
                if delta and conf["notify"] and self._push_enabled():
                    targets = await self.mutate_state(_make_target_reader(key))
                    message = gh_formatter.format_star(
                        conf["repo"], int(stars), delta, conf["display"], conf["mask"]
                    )
                    stats["pushed"] += await self._send(targets, message)

            await asyncio.sleep(REPO_INTERVAL)

        def prune(state: dict[str, Any], now: float = now) -> None:
            """锁内清理过期草稿。"""
            refresh_drafts(state, now)

        await self.mutate_state(prune)
        return stats

    async def _push(
        self,
        repo: str,
        display: str,
        mask: bool,
        targets: list[dict[str, Any]],
        events: list[tuple[str, dict[str, Any]]],
    ) -> int:
        """把事件逐条推到订阅会话，返回发送成功的条数。"""
        intro = self._push_intro()
        pushed = 0
        for event, item in events[:MAX_PUSH_PER_REPO]:
            body = gh_formatter.format_item(repo, item, event, display, mask)
            message = f"{intro}\n{body}" if intro else body
            pushed += await self._send(targets, message)
        return pushed

    async def _send(self, targets: list[dict[str, Any]], message: str) -> int:
        """把一条消息发给所有目标，返回成功次数。"""
        host = self._host
        sent = 0
        for target in targets:
            try:
                if target.get("kind") == "group":
                    await host.send_group_msg(target.get("id"), message)
                else:
                    await host.send_private_msg(target.get("id"), message)
                sent += 1
            except PluginCallError as error:
                self._log(
                    f"推送到 {target.get('kind')}:{target.get('id')} 失败：{error}",
                    "warning",
                )
            await asyncio.sleep(SEND_INTERVAL)
        return sent

    async def _alert(self, key: str, display: str, repo: str, reason: str) -> None:
        """连续失败达到阈值时给订阅会话发一条告警。"""
        targets = await self.mutate_state(_make_target_reader(key))
        message = (
            f"GitHub 动态: {display or repo}\n"
            "已连续多次拉取失败，暂停推送动态。\n"
            f"最近错误：{reason}\n"
            "恢复后会自动继续；若是令牌问题，请用「gh 设置」换凭证或令牌。"
        )
        await self._send(targets, message)

    # ---- 辅助 ----

    def _log(self, message: str, level: str = "info") -> None:
        """写插件日志，日志本身出问题也不影响轮询。"""
        if self._host is None:
            return
        try:
            self._host.log(message, level)
        except Exception:
            pass

    def _warn_capability(self, host: Any, capability: str) -> None:
        """能力缺失只提醒一次，之后每轮静默跳过。"""
        if capability in self._warned:
            return
        self._warned.add(capability)
        host.log(f"缺少 {capability} 能力，GitHub 动态轮询已暂停", "warning")


def _list_repo_keys(state: dict[str, Any]) -> list[str]:
    """列出所有仓库的状态键。"""
    return [
        key
        for key, entry in (state.get("repos") or {}).items()
        if isinstance(entry, dict)
    ]


def _make_target_reader(key: str) -> Mutator:
    """
    生成「锁内读该仓库推送目标」的回调。

    Args:
        key: 仓库状态键。
    Returns:
        可直接交给 mutate_state 的同步回调。
    """

    def read_targets(state: dict[str, Any]) -> list[dict[str, Any]]:
        """锁内读推送目标。"""
        entry = (state.get("repos") or {}).get(key) or {}
        return [
            item for item in (entry.get("targets") or []) if isinstance(item, dict)
        ]

    return read_targets
