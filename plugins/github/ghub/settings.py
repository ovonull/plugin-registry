"""
每仓库配置的展示与写入：把「分支=main 打码=on」这类设置落到仓库条目上。

展示部分供「gh 配置」用，写入部分供「gh 设置」用，两边共用同一套字段解释，
避免改了写入不改展示导致面板与实际不一致。
"""

from __future__ import annotations

from typing import Any

from ghub import commands as gh_commands
from ghub import store as gh_store

# 监控类型的中文名，用于配置展示。
WATCH_LABELS = {
    "issue": "Issue",
    "pull": "PR",
    "commit": "提交",
    "release": "Release",
    "star": "Star",
}


def watch_text(entry: dict[str, Any]) -> str:
    """监控内容的中文描述。"""
    kinds = gh_store.watch_of(entry)
    if not kinds:
        return "无（不推送任何动态）"
    return "、".join(WATCH_LABELS.get(kind, kind) for kind in kinds)


def settings_lines(entry: dict[str, Any]) -> list[str]:
    """把仓库配置拼成「配置」指令的展示行。"""
    interval = float(entry.get("check_interval") or 0)
    credential = str(entry.get("credential") or "").strip()
    return [
        f"分支：{entry.get('branch') or '默认'}",
        f"显示名：{entry.get('display_name') or '—'}",
        f"打码：{'开' if entry.get('mask') else '关'}",
        f"检查周期：{'跟随全局' if interval <= 0 else f'{int(interval)} 秒'}",
        f"凭证：{credential or '全局'}",
        f"监控内容：{watch_text(entry)}",
        f"通知：{'开' if entry.get('notify', True) else '关'}",
        f"Issue 管理：{'开' if entry.get('issue_enabled', True) else '关'}",
        f"Star 阈值：{int(entry.get('star_threshold') or 1)}",
        f"推送目标：{len(entry.get('targets') or [])} 个会话",
        f"管理员：{len(entry.get('managers') or [])} 人",
    ]


def apply_settings(
    entry: dict[str, Any], state: dict[str, Any], settings: dict[str, str], prefix: str
) -> tuple[list[str], list[str]]:
    """
    把解析好的设置逐项写进仓库条目。

    Args:
        entry: 仓库条目。
        state: 状态字典（校验凭证是否存在要用）。
        settings: {规范键: 原始值}。
        prefix: 指令前缀，用于错误提示里给示例。
    Returns:
        (已应用的描述列表, 错误列表)。
    """
    applied: list[str] = []
    errors: list[str] = []
    for key, raw in settings.items():
        if key == "branch":
            value = gh_commands.parse_branch(raw)
            if value is None:
                errors.append(f"分支：值不合法（只允许字母、数字和 . _ / -，收到 {raw}）")
                continue
            entry["branch"] = value
            applied.append(f"分支 → {value or '默认分支'}")
        elif key == "display_name":
            value = gh_commands.parse_display_name(raw)
            if value is None:
                errors.append("显示名：最长 40 字")
                continue
            entry["display_name"] = value
            applied.append(f"显示名 → {value or '仓库原名'}")
        elif key == "mask":
            value = gh_commands.parse_bool(raw)
            if value is None:
                errors.append(f"打码：只认 on/off（收到 {raw}）")
                continue
            entry["mask"] = value
            applied.append(f"打码 → {'开' if value else '关'}")
        elif key == "check_interval":
            value = gh_commands.parse_seconds(raw)
            if value is None:
                errors.append("检查周期：填 0（跟随全局）或 30 秒以上，最长 365 天")
                continue
            entry["check_interval"] = float(value)
            applied.append(f"检查周期 → {'跟随全局' if value == 0 else f'{value} 秒'}")
        elif key == "credential":
            value = gh_commands.parse_credential(raw)
            if value is None:
                errors.append("凭证：名字只能用字母、数字、下划线和中文，最长 20 字")
                continue
            if value and value not in (state.get("credentials") or {}):
                errors.append(
                    f"凭证：没有叫「{value}」的凭证，先发「{prefix} 添加凭证 {value} 令牌」"
                )
                continue
            entry["credential"] = value
            applied.append(f"凭证 → {value or '全局'}")
        elif key == "watch":
            value = gh_commands.parse_watch(raw)
            if value is None:
                errors.append("监控：可用 issue/pr/commit/release/star，逗号分隔，全部表示全选")
                continue
            entry["watch"] = value
            applied.append(f"监控内容 → {watch_text(entry)}")
        elif key == "notify":
            value = gh_commands.parse_bool(raw)
            if value is None:
                errors.append(f"通知：只认 on/off（收到 {raw}）")
                continue
            entry["notify"] = value
            applied.append(f"通知 → {'开' if value else '关'}")
        elif key == "issue_enabled":
            value = gh_commands.parse_bool(raw)
            if value is None:
                errors.append(f"Issue 管理：只认 on/off（收到 {raw}）")
                continue
            entry["issue_enabled"] = value
            applied.append(f"Issue 管理 → {'开' if value else '关'}")
        elif key == "star_threshold":
            value = gh_commands.parse_threshold(raw)
            if value is None:
                errors.append("Star 阈值：填 1 到 100000 的整数")
                continue
            entry["star_threshold"] = value
            applied.append(f"Star 阈值 → {value}")
    return applied, errors
