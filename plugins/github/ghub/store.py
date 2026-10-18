"""
插件状态读写：监控仓库、推送目标、已见条目都落在同一个 JSON 文件里。

约定：
- 每个机器人一个状态文件，路径由本文件位置向上推导（找到 packages 目录后取
  其父目录，即 data/plugins），与 plugin.storage 能力描述的目录一致，
  插件升级换包后跨版本稳定；
- 读写都是同步调用且中间没有 await，单协程内天然串行，不需要额外加锁；
- 写文件走「临时文件 + os.replace」原子替换，避免写一半留下坏 JSON；
- 已见条目按「每个仓库每类最多 N 条」淘汰，N 由插件配置 item_limit 决定。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# 状态文件结构版本，将来结构不兼容升级时 bump，旧文件读不出就按新结构起步。
STATE_VERSION = 1

# 由 __file__ 逐级向上找这个名字的目录，它的父目录就是 data/plugins。
PACKAGES_DIR_NAME = "packages"

# 事件类型：diff_items 用它标记一条动态为什么会被推送。
EVENT_NEW = "new"
EVENT_UPDATED = "updated"
EVENT_CLOSED = "closed"
EVENT_REOPENED = "reopened"
EVENT_MERGED = "merged"

# 每个仓库每类条目默认保留多少条已见记录。
DEFAULT_ITEM_LIMIT = 10

# 已见记录里标题最多存多少字，只是用来排查，不需要全量。
SEEN_TITLE_CHARS = 80

# 可监控的动态类型，顺序即「配置」指令里的展示顺序。
WATCH_KINDS = ("issue", "pull", "commit", "release", "star")

# 每仓库配置的默认值。新增字段时改这里，老状态文件由 ensure_entry_defaults 补齐。
ENTRY_DEFAULTS: dict[str, Any] = {
    "credential": "",
    "branch": "",
    "display_name": "",
    "mask": False,
    "check_interval": 0.0,
    "watch": list(WATCH_KINDS),
    "notify": True,
    "issue_enabled": True,
    "baseline_kinds": [],
    "star_threshold": 1,
    "star_last": 0,
    "star_pending": 0,
    "star_baseline_done": False,
    "last_error": "",
}


def state_root() -> Path:
    """推导状态根目录（data/plugins）。"""
    for parent in Path(__file__).resolve().parents:
        if parent.name == PACKAGES_DIR_NAME:
            return parent.parent
    # 兜底：本地直接运行测试时不在 packages 目录树下，用当前目录下的 data/plugins。
    return Path.cwd() / "data" / "plugins"


def state_file(plugin_id: str, bot_qq: str) -> Path:
    """某个机器人的状态文件路径。"""
    safe_bot = "".join(ch for ch in str(bot_qq) if ch.isalnum()) or "unknown"
    return state_root() / plugin_id / f"state-{safe_bot}.json"


def default_state() -> dict[str, Any]:
    """一份空状态。"""
    return {
        "version": STATE_VERSION,
        "repos": {},
        "drafts": {},
        "credentials": {},
    }


def load_state(path: Path) -> dict[str, Any]:
    """
    读状态文件。

    Args:
        path: 状态文件路径。
    Returns:
        状态字典；文件不存在、损坏或结构不对时返回空状态。
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default_state()
    if not isinstance(raw, dict):
        return default_state()
    state = default_state()
    repos = raw.get("repos")
    drafts = raw.get("drafts")
    credentials = raw.get("credentials")
    state["repos"] = repos if isinstance(repos, dict) else {}
    state["drafts"] = drafts if isinstance(drafts, dict) else {}
    state["credentials"] = credentials if isinstance(credentials, dict) else {}
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    """
    原子写状态文件。

    Args:
        path: 状态文件路径。
        state: 要保存的状态字典。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp, path)


def ensure_entry_defaults(entry: dict[str, Any]) -> dict[str, Any]:
    """
    补齐仓库条目缺失的配置字段。

    老版本状态文件里没有 credential/branch/watch 这些字段，读出来后先补齐，
    后面的逻辑就不用到处判空。

    Args:
        entry: 仓库条目。
    Returns:
        同一个条目对象（已补齐）。
    """
    for key, default in ENTRY_DEFAULTS.items():
        if key not in entry:
            entry[key] = list(default) if isinstance(default, list) else default
    if not isinstance(entry.get("watch"), list):
        entry["watch"] = list(WATCH_KINDS)
    return entry


def repo_entry(
    state: dict[str, Any], key: str, display: str = ""
) -> tuple[dict[str, Any], bool]:
    """
    取仓库条目，不存在则按默认结构创建。

    Args:
        state: 状态字典。
        key: 仓库键（小写 owner/repo）。
        display: 展示用的大小写原文，仅在首次创建时记录。
    Returns:
        (仓库条目, 是否新建)。
    """
    repos = state.setdefault("repos", {})
    entry = repos.get(key)
    if isinstance(entry, dict):
        if display and not str(entry.get("repo") or "").strip():
            entry["repo"] = display
        return ensure_entry_defaults(entry), False
    entry = {
        "repo": display or key,
        "targets": [],
        "managers": [],
        "token": "",
        "seen": {},
        "baseline_done": False,
        "failures": 0,
        "alerted": False,
        "last_check": 0.0,
    }
    repos[key] = ensure_entry_defaults(entry)
    return repos[key], True


def watch_of(entry: dict[str, Any]) -> list[str]:
    """取有效的监控类型，过滤掉非法值与重复项。"""
    raw = entry.get("watch")
    if not isinstance(raw, list):
        return list(WATCH_KINDS)
    result: list[str] = []
    for item in raw:
        kind = str(item or "").strip()
        if kind in WATCH_KINDS and kind not in result:
            result.append(kind)
    return result


def display_of(entry: dict[str, Any], key: str = "") -> str:
    """推送首行用的仓库名：优先显示名，其次仓库原名。"""
    return (
        str(entry.get("display_name") or "").strip()
        or str(entry.get("repo") or "").strip()
        or key
    )


def credential_of(entry: dict[str, Any], state: dict[str, Any]) -> str:
    """
    解析仓库该用哪个令牌。

    优先级：仓库指定的凭证名 -> 仓库自带的 token -> 空串（由调用方回落到全局令牌）。

    Args:
        entry: 仓库条目。
        state: 状态字典（凭证表在顶层 credentials）。
    Returns:
        令牌字符串，取不到返回空串。
    """
    name = str(entry.get("credential") or "").strip()
    if name:
        token = (state.get("credentials") or {}).get(name)
        if isinstance(token, str) and token.strip():
            return token.strip()
    token = str(entry.get("token") or "").strip()
    return token


def add_target(entry: dict[str, Any], kind: str, target_id: Any) -> bool:
    """
    给仓库加一个推送目标（群号或 QQ 号）。

    Args:
        entry: 仓库条目。
        kind: group 或 private。
        target_id: 群号或 QQ 号。
    Returns:
        True 表示新增，False 表示已存在或参数非法。
    """
    text = str(target_id or "").strip()
    if kind not in {"group", "private"} or not text.isdigit():
        return False
    for target in entry.setdefault("targets", []):
        if isinstance(target, dict) and target.get("kind") == kind and str(target.get("id")) == text:
            return False
    entry["targets"].append({"kind": kind, "id": text})
    return True


def remove_target(entry: dict[str, Any], kind: str, target_id: Any) -> bool:
    """
    从仓库移除一个推送目标。

    Args:
        entry: 仓库条目。
        kind: group 或 private。
        target_id: 群号或 QQ 号。
    Returns:
        True 表示移除成功，False 表示本来就不存在。
    """
    text = str(target_id or "").strip()
    targets = entry.setdefault("targets", [])
    for index, target in enumerate(targets):
        if isinstance(target, dict) and target.get("kind") == kind and str(target.get("id")) == text:
            targets.pop(index)
            return True
    return False


def add_manager(entry: dict[str, Any], qq: Any) -> bool:
    """给仓库加管理员，已存在返回 False。"""
    text = str(qq or "").strip()
    if not text.isdigit():
        return False
    managers = entry.setdefault("managers", [])
    if text in managers:
        return False
    managers.append(text)
    return True


def remove_manager(entry: dict[str, Any], qq: Any) -> bool:
    """移除仓库管理员，不存在返回 False。"""
    text = str(qq or "").strip()
    managers = entry.setdefault("managers", [])
    if text not in managers:
        return False
    managers.remove(text)
    return True


def _group_by_kind(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """把条目按 kind 分组，保持列表里的时间倒序。"""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "")
        if not kind:
            continue
        grouped.setdefault(kind, []).append(item)
    return grouped


def _seen_record(item: dict[str, Any]) -> dict[str, Any]:
    """一条已见记录只留比对需要的字段。"""
    return {
        "updated_at": str(item.get("updated_at") or ""),
        "state": str(item.get("state") or ""),
        "title": str(item.get("title") or "")[:SEEN_TITLE_CHARS],
    }


def apply_items(
    entry: dict[str, Any],
    items: list[dict[str, Any]],
    limit: int,
    kinds: list[str] | None = None,
) -> None:
    """
    用最新条目重建已见记录。

    只重建本次真正拉取过的类型：临时关掉某一类监控时，它的历史记录要留着，
    否则重新打开会把一堆旧动态当成新的推出去。

    Args:
        entry: 仓库条目。
        items: 客户端返回的条目列表（时间倒序）。
        limit: 每类保留条数，至少 1 条。
        kinds: 本次拉取过的类型；None 表示按 items 里出现的类型重建。
    """
    keep = max(1, int(limit or DEFAULT_ITEM_LIMIT))
    grouped = _group_by_kind(items)
    seen = entry.get("seen")
    if not isinstance(seen, dict):
        seen = {}
    for kind in list(kinds) if kinds is not None else list(grouped):
        seen[str(kind)] = {
            str(item["id"]): _seen_record(item)
            for item in grouped.get(kind, [])[:keep]
        }
    entry["seen"] = seen


def _event_label(old: dict[str, Any], item: dict[str, Any]) -> str:
    """按状态迁移判断这条动态算「更新」还是「关闭/重新打开/合并」。"""
    new_state = str(item.get("state") or "")
    old_state = str(old.get("state") or "")
    if new_state == "merged":
        return EVENT_MERGED
    if new_state == "closed" and old_state != "closed":
        return EVENT_CLOSED
    if new_state == "open" and old_state == "closed":
        return EVENT_REOPENED
    return EVENT_UPDATED


def diff_items(
    entry: dict[str, Any], items: list[dict[str, Any]], limit: int
) -> list[tuple[str, dict[str, Any]]]:
    """
    对比已见记录与最新条目，列出需要推送的事件。

    只对比每类前 limit 条：apply_items 会把更旧的记录淘汰掉，若把范围外的
    条目也算「新」，下一轮就会把早已发生过的事件当成新闻推送。

    Args:
        entry: 仓库条目。
        items: 客户端返回的条目列表（时间倒序）。
        limit: 每类参与对比的条数，至少 1 条。
    Returns:
        [(事件类型, 条目)] 列表，事件类型见 EVENT_* 常量。
    """
    keep = max(1, int(limit or DEFAULT_ITEM_LIMIT))
    seen = entry.get("seen") or {}
    events: list[tuple[str, dict[str, Any]]] = []
    for kind, kind_items in _group_by_kind(items).items():
        seen_kind = seen.get(kind) or {}
        if not isinstance(seen_kind, dict):
            continue
        for item in kind_items[:keep]:
            item_id = str(item.get("id") or "")
            if not item_id:
                continue
            old = seen_kind.get(item_id)
            if not isinstance(old, dict):
                events.append((EVENT_NEW, item))
                continue
            if str(old.get("updated_at") or "") != str(item.get("updated_at") or "") or str(
                old.get("state") or ""
            ) != str(item.get("state") or ""):
                events.append((_event_label(old, item), item))
    return events


def record_success(entry: dict[str, Any], now: float) -> None:
    """轮询成功：清零失败计数，允许下次失败再次告警。"""
    entry["failures"] = 0
    entry["alerted"] = False
    entry["last_check"] = float(now)


def record_failure(entry: dict[str, Any], now: float, threshold: int) -> bool:
    """
    轮询失败：累计失败次数。

    Args:
        entry: 仓库条目。
        now: 当前时间戳。
        threshold: 告警阈值，0 或负数表示不告警。
    Returns:
        True 表示本次失败首次达到阈值，调用方应发一条告警。
    """
    entry["failures"] = int(entry.get("failures") or 0) + 1
    entry["last_check"] = float(now)
    if threshold > 0 and entry["failures"] >= threshold and not entry.get("alerted"):
        entry["alerted"] = True
        return True
    return False
