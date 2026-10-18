"""
把 GitHub 条目格式化成群聊/私聊可读的纯文本。

格式参照 Diana 的推送样式：
    GitHub 动态: SuInk/Diana
    Issue #1116（更新）
    fix(rss): 缓存经 JSON 往返后丢掉了配图
    作者：c837835323
    更新于 2026-10-18 09:17:16
    https://github.com/SuInk/Diana/issues/1116

时间一律换算成北京时间（UTC+8），不依赖运行机器的时区设置。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

# 北京时间固定 +8。
BEIJING = timezone(timedelta(hours=8))

# 条目类型的中文/英文行头。
KIND_LABELS = {
    "issue": "Issue",
    "pull": "PR",
    "commit": "Commit",
    "release": "Release",
}

# 事件类型文案。
EVENT_LABELS = {
    "new": "新建",
    "updated": "更新",
    "closed": "关闭",
    "reopened": "重新打开",
    "merged": "合并",
}

# 某些类型用默认文案会别扭，按（类型, 事件）覆盖。
KIND_EVENT_OVERRIDES = {
    ("commit", "new"): "推送",
    ("release", "new"): "发布",
}

# 标题与摘要的展示长度上限。
TITLE_CHARS = 70


def beijing_time(iso_text: Any) -> str:
    """
    把 GitHub 的 UTC 时间字符串换算成北京时间。

    Args:
        iso_text: 形如 2026-10-18T01:17:16Z 的字符串。
    Returns:
        形如 2026-10-18 09:17:16 的字符串；解析失败时原样截断返回。
    """
    text = str(iso_text if iso_text is not None else "").strip()
    if not text:
        return "未知时间"
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:32]
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(BEIJING).strftime("%Y-%m-%d %H:%M:%S")


def clip(text: Any, limit: int = TITLE_CHARS) -> str:
    """
    压掉换行与连续空白，超长时截断加省略号。

    Args:
        text: 原始文本。
        limit: 展示长度上限。
    Returns:
        适合单行展示的文本。
    """
    compact = " ".join(str(text if text is not None else "").split())
    if len(compact) > limit:
        return compact[: limit - 1] + "…"
    return compact


def _event_text(kind: str, event: str) -> str:
    """取事件文案，提交与发布用专属说法。"""
    override = KIND_EVENT_OVERRIDES.get((kind, event))
    if override:
        return override
    return EVENT_LABELS.get(event, "更新")


def mask_name(text: Any) -> str:
    """
    名字打码：保留前 2 后 2 个字符。

    「打码」开关打开时，作者名与仓库地址都不该被群里的人顺手抄走，
    所以只留能认出是谁的形状。

    Args:
        text: 原始名字。
    Returns:
        打码后的名字。
    """
    compact = "".join(str(text if text is not None else "").split())
    if not compact:
        return "***"
    if len(compact) <= 4:
        return f"{compact[:1]}***"
    return f"{compact[:2]}***{compact[-2:]}"


def _header(repo: str, display: str, mask: bool) -> str:
    """推送首行的仓库名：显示名优先；打码且没有显示名时用「某仓库」。"""
    if display:
        return f"GitHub 动态: {display}"
    if mask:
        return "GitHub 动态: 某仓库"
    return f"GitHub 动态: {repo}"


def _headline(item: dict[str, Any], event: str) -> str:
    """拼首行：Issue #12（新建）/ PR #34（合并）/ Commit 1a2b3c4（推送）。"""
    kind = str(item.get("kind") or "")
    label = KIND_LABELS.get(kind, "动态")
    event_label = _event_text(kind, event)
    number = item.get("number")
    if number is not None:
        return f"{label} #{number}（{event_label}）"
    if kind == "commit":
        return f"{label} {str(item.get('id') or '')[:7]}（{event_label}）"
    if kind == "release":
        tag = str(item.get("tag") or item.get("title") or "")
        return f"{label} {tag}（{event_label}）" if tag else f"{label}（{event_label}）"
    return f"{label}（{event_label}）"


def format_item(
    repo: str,
    item: dict[str, Any],
    event: str,
    display: str = "",
    mask: bool = False,
) -> str:
    """
    拼一条动态推送。

    Args:
        repo: 仓库名（owner/repo，保留用户输入的大小写）。
        item: 客户端归一化后的条目。
        event: 事件类型，见 store.EVENT_*。
        display: 推送显示名，留空用仓库名。
        mask: 打码开关，打开后不输出链接、作者名打码。
    Returns:
        多行纯文本消息。
    """
    title = clip(item.get("title")) or "（无标题）"
    lines = [
        _header(repo, display, mask),
        _headline(item, event),
        title,
    ]
    author = str(item.get("author") or "").strip()
    if author:
        lines.append(f"作者：{mask_name(author) if mask else author}")
    lines.append(f"更新于 {beijing_time(item.get('updated_at'))}")
    url = str(item.get("url") or "").strip()
    if url and not mask:
        lines.append(url)
    return "\n".join(lines)


def format_star(
    repo: str, total: int, delta: int, display: str = "", mask: bool = False
) -> str:
    """
    拼一条 Star 增长推送（累计增长样式）。

    Args:
        repo: 仓库名。
        total: 当前 Star 总数。
        delta: 本次通知覆盖的累计新增数。
        display: 推送显示名，留空用仓库名。
        mask: 打码开关。
    Returns:
        多行纯文本消息。
    """
    lines = [
        _header(repo, display, mask),
        f"Star {total}（+{delta}）",
        f"累计新增 {delta} 个 Star，当前共 {total} 个",
    ]
    if not mask:
        lines.append(f"https://github.com/{repo}/stargazers")
    return "\n".join(lines)
