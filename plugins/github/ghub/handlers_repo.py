"""
仓库管理类指令：列表、添加、删除、管理员、令牌、配置查看与修改。

指令统一签名 ``async def cmd_xxx(host, event, command, poller)``，
状态修改一律走 ``poller.mutate_state``，保证与后台轮询互不覆盖。
"""

from __future__ import annotations

from typing import Any

from ghub import commands as gh_commands
from ghub import store as gh_store
from ghub.auth import admin_ids, is_manager
from ghub.client import GitHubClient, GitHubError
from ghub.poller import GitHubPoller
from ghub.settings import apply_settings, settings_lines
from ghub.support import (
    DEFAULT_PREFIX,
    DEFAULT_TIMEOUT,
    chat_name,
    current_target,
    mask_token,
    reply,
    repo_summary,
    split_head,
    text_config,
)


async def cmd_help(
    host: Any, event: dict[str, Any], command: Any, poller: GitHubPoller
) -> None:
    """列出全部指令。"""
    prefix = text_config(host, "command_prefix", DEFAULT_PREFIX) or DEFAULT_PREFIX
    lines = [
        "GitHub 仓库动态指令：",
        f"【监控】{prefix} 添加仓库 owner/repo · {prefix} 删除仓库 owner/repo · {prefix} 仓库列表",
        f"【配置】{prefix} 配置 owner/repo 看全部设置",
        f"{prefix} 设置 owner/repo 键=值 ...（可改：分支 显示名 打码 周期 凭证 监控 通知 issue管理 星标阈值）",
        f"【凭证】{prefix} 添加凭证 名字 令牌 · {prefix} 删除凭证 名字 · {prefix} 凭证列表",
        f"【Issue】{prefix} issue owner/repo 标题 | 正文 → {prefix} 确认 码 / {prefix} 撤销 码",
        f"{prefix} 修改 码 标题 | 正文 · {prefix} 还原 码 · {prefix} 删除草稿 码 · {prefix} 草稿列表",
        f"【其他】{prefix} 检查 · {prefix} 帮助",
    ]
    await reply(host, event, "\n".join(lines))


async def cmd_list(
    host: Any, event: dict[str, Any], command: Any, poller: GitHubPoller
) -> None:
    """列出已监控的仓库。"""
    repos = await poller.mutate_state(repo_summary)
    if not repos:
        await reply(
            host,
            event,
            "还没有监控任何仓库，发送「gh 添加仓库 owner/repo」开始，"
            "例如「gh 添加仓库 SuInk/Diana」。",
        )
        return
    lines = [f"已监控 {len(repos)} 个仓库："]
    for display, entry in repos:
        targets = len(entry.get("targets") or [])
        managers = len(entry.get("managers") or [])
        token = "专属" if str(entry.get("token") or "").strip() else "全局"
        lines.append(
            f"· {display}（推送 {targets} 个会话 / 管理员 {managers} 人 / 令牌：{token}）"
        )
    await reply(host, event, "\n".join(lines))


async def cmd_add(
    host: Any, event: dict[str, Any], command: Any, poller: GitHubPoller
) -> None:
    """添加监控仓库，当前会话成为推送目标。"""
    head, _ = split_head(command.rest)
    repo = gh_commands.parse_repo(head)
    if repo is None:
        await reply(
            host, event, "用法：gh 添加仓库 owner/repo，例如 gh 添加仓库 SuInk/Diana"
        )
        return

    display = repo
    # 能出网就先验证仓库真实存在，顺便拿到 GitHub 的规范大小写。
    if host.has("http.outbound"):
        client = GitHubClient(host, timeout=DEFAULT_TIMEOUT)
        try:
            display = await client.check_repo(repo, text_config(host, "github_token"))
        except GitHubError as error:
            await reply(host, event, f"添加失败：{error}")
            return
        repo = display

    key = gh_commands.repo_key(repo)
    kind, target_id = current_target(event)
    user_id = str(event.get("user_id") or "")
    chat = chat_name(event)

    def mutate(
        state: dict[str, Any],
        key: str = key,
        display: str = display,
        kind: str = kind,
        target_id: str = target_id,
        user_id: str = user_id,
    ) -> tuple[bool, bool, bool]:
        """锁内写入仓库条目，返回（是否新建, 是否新增推送目标, 是否首任管理员）。"""
        entry, created = gh_store.repo_entry(state, key, display)
        added_target = gh_store.add_target(entry, kind, target_id)
        first_manager = False
        if not (entry.get("managers") or []) and user_id:
            gh_store.add_manager(entry, user_id)
            first_manager = True
        return created, added_target, first_manager

    created, added_target, first_manager = await poller.mutate_state(mutate)
    if created:
        text = f"已添加仓库 {display}，新动态会推送到{chat}。"
        if first_manager:
            text += f"\n你已成为该仓库管理员（{user_id}）。"
    elif added_target:
        text = f"仓库 {display} 已在监控中，已把{chat}加为推送目标。"
    else:
        text = f"仓库 {display} 已在监控中，{chat}已是推送目标。"
    await reply(host, event, text)


async def cmd_remove(
    host: Any, event: dict[str, Any], command: Any, poller: GitHubPoller
) -> None:
    """停止监控仓库，仅管理员可操作。"""
    head, _ = split_head(command.rest)
    repo = gh_commands.parse_repo(head)
    if repo is None:
        await reply(host, event, "用法：gh 删除仓库 owner/repo")
        return
    key = gh_commands.repo_key(repo)
    user_id = str(event.get("user_id") or "")
    admins = admin_ids(host)

    def mutate(
        state: dict[str, Any],
        key: str = key,
        user_id: str = user_id,
        admins: set[str] = admins,
    ) -> str:
        """锁内删除仓库，返回 missing/denied/ok。"""
        entry = (state.get("repos") or {}).get(key)
        if not isinstance(entry, dict):
            return "missing"
        if not is_manager(state, key, user_id, admins):
            return "denied"
        state["repos"].pop(key, None)
        # 顺手清掉这个仓库的草稿，避免留着指向已放弃监控的仓库。
        for code, draft in list((state.get("drafts") or {}).items()):
            if isinstance(draft, dict) and str(draft.get("repo")) == key:
                state["drafts"].pop(code, None)
        return "ok"

    result = await poller.mutate_state(mutate)
    if result == "ok":
        await reply(host, event, f"已停止监控 {repo}。")
    elif result == "denied":
        await reply(host, event, "只有该仓库的管理员可以删除监控。")
    else:
        await reply(host, event, f"仓库 {repo} 本来就不在监控列表里。")


async def cmd_add_manager(
    host: Any, event: dict[str, Any], command: Any, poller: GitHubPoller
) -> None:
    """添加仓库管理员。"""
    head, tail = split_head(command.rest)
    repo = gh_commands.parse_repo(head)
    if repo is None:
        await reply(host, event, "用法：gh 添加管理 owner/repo QQ号")
        return
    qqs = gh_commands.parse_qq_list(tail)
    if not qqs:
        await reply(
            host, event, "请至少填写一个 QQ 号，例如 gh 添加管理 SuInk/Diana 123456"
        )
        return
    key = gh_commands.repo_key(repo)
    user_id = str(event.get("user_id") or "")
    admins = admin_ids(host)

    def mutate(
        state: dict[str, Any],
        key: str = key,
        qqs: list[str] = qqs,
        user_id: str = user_id,
        admins: set[str] = admins,
    ) -> tuple[str, list[str]]:
        """锁内加管理员，返回（missing/denied/ok, 新增的 QQ 列表）。"""
        entry = (state.get("repos") or {}).get(key)
        if not isinstance(entry, dict):
            return "missing", []
        if not is_manager(state, key, user_id, admins):
            return "denied", []
        added = [qq for qq in qqs if gh_store.add_manager(entry, qq)]
        return "ok", added

    result, added = await poller.mutate_state(mutate)
    if result == "ok":
        if added:
            await reply(
                host, event, f"已添加 {repo} 的管理员：{'、'.join(added)}"
            )
        else:
            await reply(host, event, "这些 QQ 已经是管理员了。")
    elif result == "denied":
        await reply(host, event, "只有该仓库的管理员可以添加管理。")
    else:
        await reply(host, event, f"仓库 {repo} 还未监控，先「gh 添加仓库 {repo}」。")


async def cmd_remove_manager(
    host: Any, event: dict[str, Any], command: Any, poller: GitHubPoller
) -> None:
    """移除仓库管理员，至少保留一名。"""
    head, tail = split_head(command.rest)
    repo = gh_commands.parse_repo(head)
    if repo is None:
        await reply(host, event, "用法：gh 删除管理 owner/repo QQ号")
        return
    qqs = gh_commands.parse_qq_list(tail)
    if not qqs:
        await reply(host, event, "请至少填写一个 QQ 号，例如 gh 删除管理 SuInk/Diana 123456")
        return
    key = gh_commands.repo_key(repo)
    user_id = str(event.get("user_id") or "")
    admins = admin_ids(host)

    def mutate(
        state: dict[str, Any],
        key: str = key,
        qqs: list[str] = qqs,
        user_id: str = user_id,
        admins: set[str] = admins,
    ) -> tuple[str, list[str]]:
        """锁内移除管理员，返回（missing/denied/keep/ok, 移除的 QQ 列表）。"""
        entry = (state.get("repos") or {}).get(key)
        if not isinstance(entry, dict):
            return "missing", []
        if not is_manager(state, key, user_id, admins):
            return "denied", []
        removed = [qq for qq in qqs if gh_store.remove_manager(entry, qq)]
        if not (entry.get("managers") or []):
            return "keep", removed
        return "ok", removed

    result, removed = await poller.mutate_state(mutate)
    if result == "ok":
        if removed:
            await reply(host, event, f"已移除 {repo} 的管理员：{'、'.join(removed)}")
        else:
            await reply(host, event, "这些 QQ 不是该仓库的管理员。")
    elif result == "keep":
        await reply(host, event, "每个仓库至少要保留一名管理员。")
    elif result == "denied":
        await reply(host, event, "只有该仓库的管理员可以移除管理。")
    else:
        await reply(host, event, f"仓库 {repo} 还未监控。")


async def cmd_set_token(
    host: Any, event: dict[str, Any], command: Any, poller: GitHubPoller
) -> None:
    """给仓库配专属令牌（私有仓库或需要提 Issue 时用）。"""
    head, tail = split_head(command.rest)
    repo = gh_commands.parse_repo(head)
    if repo is None:
        await reply(host, event, "用法：gh 设置令牌 owner/repo 令牌")
        return
    token = tail.strip()
    if not token:
        await reply(
            host,
            event,
            "用法：gh 设置令牌 owner/repo 令牌，令牌可在 GitHub 的 "
            "Settings -> Developer settings 里生成。",
        )
        return
    key = gh_commands.repo_key(repo)
    user_id = str(event.get("user_id") or "")
    admins = admin_ids(host)

    def mutate(
        state: dict[str, Any],
        key: str = key,
        token: str = token,
        user_id: str = user_id,
        admins: set[str] = admins,
    ) -> str:
        """锁内写令牌，返回 missing/denied/ok。"""
        entry = (state.get("repos") or {}).get(key)
        if not isinstance(entry, dict):
            return "missing"
        if not is_manager(state, key, user_id, admins):
            return "denied"
        entry["token"] = token
        return "ok"

    result = await poller.mutate_state(mutate)
    if result == "ok":
        await reply(
            host, event, f"已更新 {repo} 的令牌（{mask_token(token)}）。"
        )
    elif result == "denied":
        await reply(host, event, "只有该仓库的管理员可以设置令牌。")
    else:
        await reply(host, event, f"仓库 {repo} 还未监控，先「gh 添加仓库 {repo}」。")


async def cmd_show(
    host: Any, event: dict[str, Any], command: Any, poller: GitHubPoller
) -> None:
    """查看单个仓库的全部配置（替代 Diana 的仓库管理面板）。"""
    head, _ = split_head(command.rest)
    repo = gh_commands.parse_repo(head)
    if repo is None:
        await reply(host, event, "用法：gh 配置 owner/repo")
        return
    key = gh_commands.repo_key(repo)

    def mutate(state: dict[str, Any], key: str = key) -> tuple[list[str], str] | None:
        """锁内取该仓库的配置展示行。"""
        entry = (state.get("repos") or {}).get(key)
        if not isinstance(entry, dict):
            return None
        gh_store.ensure_entry_defaults(entry)
        return settings_lines(entry), str(entry.get("repo") or key)

    result = await poller.mutate_state(mutate)
    if result is None:
        await reply(host, event, f"仓库 {repo} 还未监控，先「gh 添加仓库 {repo}」。")
        return
    lines, display = result
    prefix = text_config(host, "command_prefix", DEFAULT_PREFIX) or DEFAULT_PREFIX
    await reply(
        host,
        event,
        f"{display} 当前设置：\n"
        + "\n".join(lines)
        + f"\n改法：{prefix} 设置 {display} 分支=main 打码=on",
    )


async def cmd_set(
    host: Any, event: dict[str, Any], command: Any, poller: GitHubPoller
) -> None:
    """按 键=值 修改单个仓库的配置。"""
    head, tail = split_head(command.rest)
    repo = gh_commands.parse_repo(head)
    if repo is None:
        await reply(
            host, event, "用法：gh 设置 owner/repo 分支=main 打码=on 监控=issue,pr"
        )
        return
    settings, unknown = gh_commands.parse_settings(tail)
    if not settings and not unknown:
        await reply(
            host,
            event,
            "要改什么？可改：分支、显示名、打码、周期、凭证、监控、通知、issue管理、星标阈值。\n"
            "例如：gh 设置 SuInk/Diana 分支=main 打码=on 监控=issue,pr,star",
        )
        return
    key = gh_commands.repo_key(repo)
    user_id = str(event.get("user_id") or "")
    admins = admin_ids(host)
    prefix = text_config(host, "command_prefix", DEFAULT_PREFIX) or DEFAULT_PREFIX

    def mutate(
        state: dict[str, Any],
        key: str = key,
        settings: dict[str, str] = settings,
        unknown: list[str] = unknown,
        user_id: str = user_id,
        admins: set[str] = admins,
        prefix: str = prefix,
    ) -> tuple[str, list[str], list[str]]:
        """锁内校验权限并应用设置。"""
        entry = (state.get("repos") or {}).get(key)
        if not isinstance(entry, dict):
            return "missing", [], []
        if not is_manager(state, key, user_id, admins):
            return "denied", [], []
        gh_store.ensure_entry_defaults(entry)
        applied, errors = apply_settings(entry, state, settings, prefix)
        errors.extend(f"无法识别：{item}" for item in unknown)
        return "ok", applied, errors

    result, applied, errors = await poller.mutate_state(mutate)
    if result == "denied":
        await reply(host, event, "只有该仓库的管理员可以改设置。")
        return
    if result == "missing":
        await reply(host, event, f"仓库 {repo} 还未监控，先「gh 添加仓库 {repo}」。")
        return
    lines = [f"{repo} 已更新：" if applied else f"{repo} 未做修改："]
    lines.extend(f"· {item}" for item in applied)
    if errors:
        lines.append("未生效：")
        lines.extend(f"· {item}" for item in errors)
    await reply(host, event, "\n".join(lines))
