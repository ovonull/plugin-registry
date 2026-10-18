"""
凭证类指令：查看、添加、删除全局凭证。

凭证全局共享（与 Diana 的做法一致），仓库通过 ``credential`` 字段选一条；
没选或选中的凭证被删掉时回落到插件配置里的全局令牌。
"""

from __future__ import annotations

from typing import Any

from ghub import commands as gh_commands
from ghub.auth import admin_ids, is_any_manager
from ghub.poller import GitHubPoller
from ghub.support import mask_token, reply, split_head


async def cmd_creds(
    host: Any, event: dict[str, Any], command: Any, poller: GitHubPoller
) -> None:
    """列出全局凭证（不显示明文令牌）。"""
    items = await poller.mutate_state(credential_rows)
    if not items:
        await reply(
            host,
            event,
            "还没有添加任何凭证，所有仓库都走插件配置里的全局令牌。\n"
            "添加：gh 添加凭证 公共 ghp_xxx",
        )
        return
    lines = [f"凭证（共 {len(items)} 条）："]
    for name, masked, count in items:
        lines.append(f"· {name}（{masked}，{count} 个仓库在用）")
    lines.append("未指定凭证的仓库走插件配置里的全局令牌。")
    await reply(host, event, "\n".join(lines))


async def cmd_add_cred(
    host: Any, event: dict[str, Any], command: Any, poller: GitHubPoller
) -> None:
    """新增或覆盖一条全局凭证。"""
    head, tail = split_head(command.rest)
    name = gh_commands.parse_credential(head)
    token = tail.strip()
    if not name:
        await reply(host, event, "用法：gh 添加凭证 名字 令牌，名字最长 20 字")
        return
    if len(token) < 10:
        await reply(
            host, event, "令牌看起来不对：GitHub 令牌通常 40 位以上，请检查是否截断。"
        )
        return
    user_id = str(event.get("user_id") or "")
    admins = admin_ids(host)

    def mutate(
        state: dict[str, Any],
        name: str = name,
        token: str = token,
        user_id: str = user_id,
        admins: set[str] = admins,
    ) -> str:
        """锁内写凭证，返回 ok/denied/updated。"""
        if not is_any_manager(state, user_id, admins):
            return "denied"
        credentials = state.setdefault("credentials", {})
        existed = name in credentials
        credentials[name] = token
        return "updated" if existed else "ok"

    result = await poller.mutate_state(mutate)
    if result == "denied":
        await reply(host, event, "只有仓库管理员或全局管理员可以管理凭证。")
        return
    verb = "已更新" if result == "updated" else "已添加"
    await reply(
        host,
        event,
        f"{verb}凭证「{name}」（{mask_token(token)}）。\n"
        f"让仓库用它：gh 设置 owner/repo 凭证={name}",
    )


async def cmd_remove_cred(
    host: Any, event: dict[str, Any], command: Any, poller: GitHubPoller
) -> None:
    """删除一条全局凭证，并把引用它的仓库改回全局令牌。"""
    head, _ = split_head(command.rest)
    name = gh_commands.parse_credential(head)
    if not name:
        await reply(host, event, "用法：gh 删除凭证 名字")
        return
    user_id = str(event.get("user_id") or "")
    admins = admin_ids(host)

    def mutate(
        state: dict[str, Any],
        name: str = name,
        user_id: str = user_id,
        admins: set[str] = admins,
    ) -> tuple[str, list[str]]:
        """锁内删除凭证并清理引用。"""
        if not is_any_manager(state, user_id, admins):
            return "denied", []
        credentials = state.get("credentials") or {}
        if name not in credentials:
            return "missing", []
        credentials.pop(name, None)
        affected: list[str] = []
        for key, entry in (state.get("repos") or {}).items():
            if not isinstance(entry, dict):
                continue
            if str(entry.get("credential") or "").strip() == name:
                entry["credential"] = ""
                affected.append(str(entry.get("repo") or key))
        return "ok", affected

    result, affected = await poller.mutate_state(mutate)
    if result == "denied":
        await reply(host, event, "只有仓库管理员或全局管理员可以删除凭证。")
        return
    if result == "missing":
        await reply(host, event, f"没有叫「{name}」的凭证。")
        return
    text = f"已删除凭证「{name}」。"
    if affected:
        text += f"\n这些仓库已改回全局令牌：{'、'.join(affected)}"
    await reply(host, event, text)


def credential_rows(
    state: dict[str, Any],
) -> list[tuple[str, str, int]]:
    """锁内整理凭证列表：(名字, 打码令牌, 使用该凭证的仓库数)。"""
    credentials = state.get("credentials") or {}
    usage: dict[str, int] = {}
    for entry in (state.get("repos") or {}).values():
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("credential") or "").strip()
        if name:
            usage[name] = usage.get(name, 0) + 1
    rows: list[tuple[str, str, int]] = []
    for name in sorted(credentials):
        token = credentials.get(name)
        rows.append((str(name), mask_token(str(token or "")), usage.get(str(name), 0)))
    return rows
