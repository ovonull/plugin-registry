"""
GitHub 仓库动态插件（入口）。

功能：
1. 群里或私聊发「gh 添加仓库 owner/repo」把仓库加入监控，之后有新 Issue、
   PR、提交、Release、Star 就按 Diana 的样式推送到订阅会话；
2. 每个仓库单独配置分支、显示名、打码、检查周期、凭证、监控内容、通知开关；
3. 「gh issue owner/repo 标题 | 正文」先存草稿，「gh 确认 码」才真正提交到
   GitHub；草稿可修改、撤销、还原、删除记录；
4. 后台每几分钟轮询一次，连续失败达到阈值只告警一次。

约定：
- 出网一律走 host.http_request，白名单只有 api.github.com；
- 本文件只做「收消息 -> 找到轮询器 -> 分派指令」，具体指令在 ghub.handlers_*；
- 事件处理函数只做指令解析与本地状态修改，重活（轮询、提交 Issue）交给
  后台任务：宿主对单个事件有 10 秒超时，超时会把插件子进程杀掉重启；
- 状态文件的一切「读 -> 改 -> 写」都经轮询器的 mutate_state 在锁内完成；
- 任何失败都回一条中文提示，不向宿主抛裸异常。
"""

from __future__ import annotations

import re
from typing import Any

from app.services.plugin_sdk import PluginCallError, on_message

from ghub import commands as gh_commands
from ghub import store as gh_store
from ghub.handlers_cred import cmd_add_cred, cmd_creds, cmd_remove_cred
from ghub.handlers_issue import (
    cmd_cancel,
    cmd_check,
    cmd_confirm,
    cmd_delete_draft,
    cmd_drafts,
    cmd_edit,
    cmd_issue,
    cmd_restore,
)
from ghub.handlers_repo import (
    cmd_add,
    cmd_add_manager,
    cmd_list,
    cmd_remove,
    cmd_remove_manager,
    cmd_set,
    cmd_set_token,
    cmd_show,
    cmd_help,
)
from ghub.poller import GitHubPoller
from ghub.support import DEFAULT_PREFIX, reply, text_config

# 插件标识，与 manifest.json 一致。
PLUGIN_ID = "cn.ucbk.github"

# NapCat 在纯文本消息里用 CQ 码表示 @，数组消息里是 at 段，两种都要剥掉。
CQ_AT_PATTERN = re.compile(r"\[CQ:at,[^\]]*\]")

# 每个机器人一个轮询任务，随第一条消息懒启动。
_pollers: dict[str, GitHubPoller] = {}

# 指令分发表。
HANDLERS = {
    "help": cmd_help,
    "list": cmd_list,
    "add": cmd_add,
    "remove": cmd_remove,
    "add_manager": cmd_add_manager,
    "remove_manager": cmd_remove_manager,
    "set_token": cmd_set_token,
    "show": cmd_show,
    "set": cmd_set,
    "creds": cmd_creds,
    "add_cred": cmd_add_cred,
    "remove_cred": cmd_remove_cred,
    "check": cmd_check,
    "issue": cmd_issue,
    "confirm": cmd_confirm,
    "cancel": cmd_cancel,
    "edit": cmd_edit,
    "restore": cmd_restore,
    "delete_draft": cmd_delete_draft,
    "drafts": cmd_drafts,
}


def message_text(event: dict[str, Any]) -> str:
    """从事件里取出纯文本：剥掉 @ 段与 CQ 码，兼容 NapCat 的特殊空格。"""
    message = event.get("message")
    if isinstance(message, list):
        parts: list[str] = []
        for segment in message:
            if isinstance(segment, dict) and segment.get("type") == "text":
                data = segment.get("data") or {}
                parts.append(str(data.get("text") or ""))
            # at 等非文本段直接丢弃。
        text = "".join(parts)
    elif isinstance(message, str):
        text = message
    else:
        text = str(event.get("raw_message") or "")
    text = CQ_AT_PATTERN.sub("", text)
    return text.replace("\u2005", " ").replace("\xa0", " ").strip()


def ensure_poller(host: Any) -> GitHubPoller:
    """
    取（或建）当前机器人的轮询任务，并刷新视图与配置。

    每条消息都调一次：既让配置改动及时生效，也保证插件子进程重启后
    收到第一条消息就恢复推送（宿主没有启动钩子，只能这样懒启动）。
    """
    bot_qq = "".join(ch for ch in str(host.bot_qq or "") if ch.isalnum()) or "unknown"
    poller = _pollers.get(bot_qq)
    if poller is None:
        poller = GitHubPoller(
            host, gh_store.state_file(PLUGIN_ID, bot_qq), dict(host.config or {})
        )
        _pollers[bot_qq] = poller
    else:
        poller.refresh(host, dict(host.config or {}))
    poller.start()
    return poller


@on_message
async def handle_github(event: dict[str, Any], host: Any) -> None:
    """收到消息：唤醒轮询任务，命中指令就执行。"""
    if event.get("post_type") != "message":
        return
    if str(event.get("user_id")) == str(event.get("self_id")):
        # 机器人自己发的消息不处理，避免自我循环。
        return

    text = message_text(event)
    if not text:
        return
    # 每条消息都刷新轮询器：推送不依赖指令，机器人重启后靠消息唤醒。
    poller = ensure_poller(host)

    command = gh_commands.parse_command(
        text, text_config(host, "command_prefix", DEFAULT_PREFIX)
    )
    if command is None:
        return
    if not host.has("msg.send"):
        host.log("缺少 msg.send 能力，无法回复指令", "error")
        return

    handler = HANDLERS.get(command.name)
    if handler is None:
        await reply(host, event, "不认识这个指令，发送「gh 帮助」查看用法。")
        return

    try:
        await handler(host, event, command, poller)
    except PluginCallError as error:
        host.log(f"指令执行被拒绝或失败：{error}", "error")
        await reply(host, event, "GitHub 服务暂时不可用，请稍后再试。")
    except Exception as error:  # 插件内部兜底，不让异常冒到宿主
        host.log(f"GitHub 插件异常：{type(error).__name__}: {error}", "error")
        await reply(host, event, "指令执行出错了，请稍后再试。")
