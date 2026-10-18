"""
Issue 类指令：起草、确认提交、撤销、修改、还原、删除记录，以及立即检查。

起草只写本地状态；确认时才真正调 GitHub 建 Issue，因此网络请求都在这里
直接 await（单条指令的网络耗时远小于宿主 10 秒的事件超时）。
"""

from __future__ import annotations

import time
from typing import Any

from ghub import commands as gh_commands
from ghub import drafts as gh_drafts
from ghub import store as gh_store
from ghub.auth import admin_ids, draft_permission, is_manager
from ghub.client import GitHubClient, GitHubError
from ghub.formatter import clip
from ghub.poller import GitHubPoller
from ghub.support import code_arg, reply, split_head, text_config

# 草稿列表最多展示多少条。
MAX_DRAFT_LINES = 20


async def cmd_check(
    host: Any, event: dict[str, Any], command: Any, poller: GitHubPoller
) -> None:
    """立即触发一次轮询。"""
    if not poller.trigger(time.time()):
        await reply(host, event, "检查太频繁了，请 30 秒后再试。")
        return
    await reply(host, event, "已触发一次立即检查，有新动态会推送到订阅会话。")


async def cmd_issue(
    host: Any, event: dict[str, Any], command: Any, poller: GitHubPoller
) -> None:
    """存一条 Issue 草稿，确认后才提交。"""
    head, tail = split_head(command.rest)
    repo = gh_commands.parse_repo(head)
    if repo is None:
        await reply(host, event, "用法：gh issue owner/repo 标题 | 正文")
        return
    parsed = gh_commands.split_issue_args(tail)
    if parsed is None:
        await reply(
            host,
            event,
            "请填写 Issue 标题，例如 gh issue SuInk/Diana 反馈 | 这里是详细描述",
        )
        return
    title, body = parsed
    key = gh_commands.repo_key(repo)

    def read_flags(state: dict[str, Any], key: str = key) -> tuple[bool, bool]:
        """锁内读：仓库是否存在、是否开启 Issue 管理。"""
        entry = (state.get("repos") or {}).get(key)
        if not isinstance(entry, dict):
            return False, False
        gh_store.ensure_entry_defaults(entry)
        return True, bool(entry.get("issue_enabled", True))

    exists, issue_enabled = await poller.mutate_state(read_flags)
    if not exists:
        await reply(
            host, event, f"仓库 {repo} 还未监控，先发送「gh 添加仓库 {repo}」再提 Issue。"
        )
        return
    if not issue_enabled:
        await reply(
            host,
            event,
            f"{repo} 未开启 Issue 管理，管理员可发"
            f"「gh 设置 {repo} issue管理=on」打开。",
        )
        return

    user_id = str(event.get("user_id") or "")
    code = await poller.mutate_state(
        lambda state, key=key, title=title, body=body, user_id=user_id: (
            gh_drafts.create_draft(state, key, title, body, user_id)
        )
    )
    await reply(
        host,
        event,
        f"草稿已存好，编号 {code}\n仓库：{repo}\n标题：{title}\n"
        f"发送「gh 确认 {code}」提交到 GitHub，「gh 撤销 {code}」放弃。",
    )


async def cmd_confirm(
    host: Any, event: dict[str, Any], command: Any, poller: GitHubPoller
) -> None:
    """确认草稿并提交 Issue 到 GitHub。"""
    code = code_arg(command.args[0] if command.args else "")
    if not code:
        await reply(host, event, "用法：gh 确认 草稿码，码可用「gh 草稿列表」查看。")
        return
    user_id = str(event.get("user_id") or "")
    admins = admin_ids(host)

    def peek(
        state: dict[str, Any], code: str = code, user_id: str = user_id, admins: set[str] = admins
    ) -> tuple[str, dict[str, Any] | None]:
        """锁内校验草稿：返回（ok/denied/状态, 草稿）。"""
        draft = gh_drafts.get_draft(state, code)
        if draft is None:
            return "missing", None
        status = str(draft.get("status") or "")
        if status != gh_drafts.STATUS_PENDING:
            return status, draft
        owner = str(draft.get("user_id") or "") == user_id
        managed = is_manager(state, str(draft.get("repo") or ""), user_id, admins)
        if not owner and not managed:
            return "denied", draft
        return "ok", draft

    status, draft = await poller.mutate_state(peek)
    if status == "missing":
        await reply(
            host, event, f"没有找到草稿 {code}，发送「gh 草稿列表」查看现有草稿。"
        )
        return
    if status == "denied":
        await reply(host, event, "只有草稿发起人或该仓库的管理员可以确认提交。")
        return
    if status != "ok":
        await reply(
            host,
            event,
            f"草稿 {code} 已是「{gh_drafts.STATUS_TEXT.get(status, status)}」状态，不能重复提交。",
        )
        return
    if not host.has("http.outbound"):
        await reply(host, event, "本机器人未获批「访问外网」能力，无法提交 Issue。")
        return

    key = str(draft.get("repo") or "")
    repo_token = await poller.mutate_state(
        lambda state, key=key: str(
            ((state.get("repos") or {}).get(key) or {}).get("token") or ""
        ).strip()
    )
    token = repo_token or text_config(host, "github_token")
    client = GitHubClient(host, timeout=DEFAULT_TIMEOUT)
    try:
        url = await client.create_issue(
            key,
            str(draft.get("title") or ""),
            str(draft.get("body") or ""),
            token,
        )
    except GitHubError as error:
        await reply(host, event, f"提交失败：{error}")
        return

    marked = await poller.mutate_state(
        lambda state, code=code: gh_drafts.confirm_draft(state, code)
    )
    if marked is None:
        # Issue 已经建了，只是草稿状态被并发改掉，如实说明。
        await reply(host, event, f"Issue 已创建，但草稿状态在此期间有变：\n{url}")
    else:
        await reply(host, event, f"已提交 Issue：\n{url}")


async def cmd_cancel(
    host: Any, event: dict[str, Any], command: Any, poller: GitHubPoller
) -> None:
    """放弃一条待确认草稿。"""
    code = code_arg(command.args[0] if command.args else "")
    if not code:
        await reply(host, event, "用法：gh 撤销 草稿码，码可用「gh 草稿列表」查看。")
        return
    user_id = str(event.get("user_id") or "")
    admins = admin_ids(host)

    def mutate(
        state: dict[str, Any],
        code: str = code,
        user_id: str = user_id,
        admins: set[str] = admins,
    ) -> str:
        """锁内撤销草稿，返回 ok/denied/状态。"""
        draft = gh_drafts.get_draft(state, code)
        if draft is None:
            return "missing"
        status = str(draft.get("status") or "")
        if status != gh_drafts.STATUS_PENDING:
            return status
        owner = str(draft.get("user_id") or "") == user_id
        managed = is_manager(state, str(draft.get("repo") or ""), user_id, admins)
        if not owner and not managed:
            return "denied"
        gh_drafts.cancel_draft(state, code)
        return "ok"

    result = await poller.mutate_state(mutate)
    if result == "ok":
        await reply(host, event, f"草稿 {code} 已撤销。")
    elif result == "denied":
        await reply(host, event, "只有草稿发起人或该仓库的管理员可以撤销。")
    elif result == "missing":
        await reply(host, event, f"没有找到草稿 {code}。")
    else:
        await reply(
            host,
            event,
            f"草稿 {code} 已是「{gh_drafts.STATUS_TEXT.get(result, result)}」状态，无需撤销。",
        )


async def cmd_drafts(
    host: Any, event: dict[str, Any], command: Any, poller: GitHubPoller
) -> None:
    """列出草稿：本人、所管仓库、全局管理员可见的范围。"""
    user_id = str(event.get("user_id") or "")
    admins = admin_ids(host)
    is_admin = bool(user_id) and user_id in admins

    def mutate(state: dict[str, Any], user_id: str = user_id, admins: set[str] = admins, is_admin: bool = is_admin) -> list[dict[str, Any]]:
        """锁内筛出可见草稿。"""
        visible = []
        for draft in gh_drafts.list_drafts(state):
            repo = str(draft.get("repo") or "")
            mine = str(draft.get("user_id") or "") == user_id
            managed = is_manager(state, repo, user_id, admins)
            if mine or managed or is_admin:
                visible.append(draft)
        return visible[:MAX_DRAFT_LINES]

    drafts = await poller.mutate_state(mutate)
    if not drafts:
        await reply(host, event, "当前没有你可查看的草稿。")
        return
    lines = [f"草稿列表（最多 {MAX_DRAFT_LINES} 条）："]
    for draft in drafts:
        status_text = gh_drafts.STATUS_TEXT.get(
            str(draft.get("status") or ""), str(draft.get("status") or "")
        )
        title = clip(str(draft.get("title") or ""), 40)
        lines.append(
            f"{draft.get('code')} · {draft.get('repo')} · {status_text} · "
            f"{title}（发起 {draft.get('user_id')}）"
        )
    await reply(host, event, "\n".join(lines))


async def cmd_edit(
    host: Any, event: dict[str, Any], command: Any, poller: GitHubPoller
) -> None:
    """修改待确认草稿的标题与正文。"""
    head, tail = split_head(command.rest)
    parsed = gh_commands.split_issue_args(tail)
    code = code_arg(head)
    if not code or parsed is None:
        await reply(host, event, "用法：gh 修改 草稿码 新标题 | 新正文")
        return
    title, body = parsed
    user_id = str(event.get("user_id") or "")
    admins = admin_ids(host)

    def mutate(
        state: dict[str, Any],
        code: str = code,
        title: str = title,
        body: str = body,
        user_id: str = user_id,
        admins: set[str] = admins,
    ) -> str:
        """锁内改草稿，返回 ok/denied/missing/状态。"""
        draft = gh_drafts.get_draft(state, code)
        if draft is None:
            return "missing"
        if not draft_permission(state, draft, user_id, admins):
            return "denied"
        status = str(draft.get("status") or "")
        if status != gh_drafts.STATUS_PENDING:
            return status
        gh_drafts.edit_draft(state, code, title, body)
        return "ok"

    result = await poller.mutate_state(mutate)
    if result == "ok":
        await reply(
            host,
            event,
            f"草稿 {code} 已更新：\n标题：{title}\n发送「gh 确认 {code}」提交到 GitHub。",
        )
    elif result == "denied":
        await reply(host, event, "只有草稿发起人或该仓库的管理员可以修改。")
    elif result == "missing":
        await reply(host, event, f"没有找到草稿 {code}。")
    else:
        await reply(
            host,
            event,
            f"草稿 {code} 已是「{gh_drafts.STATUS_TEXT.get(result, result)}」状态，不能修改。",
        )


async def cmd_restore(
    host: Any, event: dict[str, Any], command: Any, poller: GitHubPoller
) -> None:
    """把已过期或已撤销的草稿还原成新的待确认草稿（换新码）。"""
    code = code_arg(command.args[0] if command.args else "")
    if not code:
        await reply(host, event, "用法：gh 还原 草稿码（已过期或已撤销的才能还原）")
        return
    user_id = str(event.get("user_id") or "")
    admins = admin_ids(host)

    def mutate(
        state: dict[str, Any],
        code: str = code,
        user_id: str = user_id,
        admins: set[str] = admins,
    ) -> tuple[str, str]:
        """锁内还原草稿，返回（结果, 新码）。"""
        draft = gh_drafts.get_draft(state, code)
        if draft is None:
            return "missing", ""
        if not draft_permission(state, draft, user_id, admins):
            return "denied", ""
        status = str(draft.get("status") or "")
        if status not in {gh_drafts.STATUS_EXPIRED, gh_drafts.STATUS_CANCELLED}:
            return status, ""
        result = gh_drafts.restore_draft(state, code)
        if result is None:
            return status, ""
        new_code, _ = result
        return "ok", new_code

    result, new_code = await poller.mutate_state(mutate)
    if result == "ok":
        await reply(
            host,
            event,
            f"草稿已还原，新编号 {new_code}（旧编号 {code} 作废）。\n"
            f"发送「gh 确认 {new_code}」提交到 GitHub。",
        )
    elif result == "denied":
        await reply(host, event, "只有草稿发起人或该仓库的管理员可以还原。")
    elif result == "missing":
        await reply(host, event, f"没有找到草稿 {code}。")
    elif result == gh_drafts.STATUS_SUBMITTED:
        await reply(host, event, "这条草稿已经提交过，Issue 在 GitHub 上，无法还原。")
    else:
        await reply(
            host,
            event,
            f"草稿 {code} 是「{gh_drafts.STATUS_TEXT.get(result, result)}」状态，只有已过期或已撤销的才能还原。",
        )


async def cmd_delete_draft(
    host: Any, event: dict[str, Any], command: Any, poller: GitHubPoller
) -> None:
    """删除草稿记录（不影响已提交到 GitHub 的 Issue）。"""
    code = code_arg(command.args[0] if command.args else "")
    if not code:
        await reply(host, event, "用法：gh 删除草稿 草稿码")
        return
    user_id = str(event.get("user_id") or "")
    admins = admin_ids(host)

    def mutate(
        state: dict[str, Any],
        code: str = code,
        user_id: str = user_id,
        admins: set[str] = admins,
    ) -> str:
        """锁内删记录，返回 ok/denied/missing。"""
        draft = gh_drafts.get_draft(state, code)
        if draft is None:
            return "missing"
        if not draft_permission(state, draft, user_id, admins):
            return "denied"
        return "ok" if gh_drafts.delete_draft(state, code) else "missing"

    result = await poller.mutate_state(mutate)
    if result == "ok":
        await reply(
            host,
            event,
            f"草稿 {code} 的记录已删除。已经提交到 GitHub 的 Issue 不受影响。",
        )
    elif result == "denied":
        await reply(host, event, "只有草稿发起人或该仓库的管理员可以删除。")
    else:
        await reply(host, event, f"没有找到草稿 {code}。")
