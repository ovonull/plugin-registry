"""OneBot 消息触发条件与引用回复消息段构造工具。"""

import re
from typing import Any


def is_poke_event(event: dict[str, Any], bot_qq: str = "") -> bool:
    """判断是否为戳中当前机器人的 OneBot notify/poke 事件。

    Args:
        event: OneBot 事件数据。
        bot_qq: 当前机器人 QQ 号；为空时回退使用事件中的 self_id。
    Returns:
        事件类型为 notify/poke 且 target_id 指向当前机器人时返回 True。
    """
    if (
        event.get("post_type") != "notice"
        or event.get("notice_type") != "notify"
        or event.get("sub_type") != "poke"
    ):
        return False
    current_bot_qq = str(bot_qq or event.get("self_id") or "")
    return bool(current_bot_qq) and str(
        event.get("target_id") or ""
    ) == current_bot_qq


def mentions_bot(event: dict[str, Any], bot_qq: str) -> bool:
    """判断事件消息是否 @ 当前机器人，兼容消息段数组和 CQ 字符串。

    Args:
        event: OneBot 事件数据。
        bot_qq: 当前机器人 QQ 号。
    Returns:
        如果消息包含当前机器人对应的 at 消息段则返回 True。
    """
    message = event.get("message")
    if isinstance(message, list):
        for segment in message:
            if not isinstance(segment, dict) or segment.get("type") != "at":
                continue
            data = segment.get("data")
            if isinstance(data, dict) and str(data.get("qq") or "") == bot_qq:
                return True
    value = message if isinstance(message, str) else event.get("raw_message")
    if not isinstance(value, str):
        return False
    return any(
        field.strip() == f"qq={bot_qq}"
        for body in re.findall(r"\[CQ:at,([^\]]+)\]", value)
        for field in body.split(",")
    )


def reply_message_id(event: dict[str, Any]) -> str:
    """从事件中提取用户引用的消息 ID，兼容消息段数组和 CQ 字符串。

    Args:
        event: OneBot 事件数据。
    Returns:
        引用消息 ID；未找到时返回空字符串。
    """
    message = event.get("message")
    if isinstance(message, list):
        for segment in message:
            if not isinstance(segment, dict) or segment.get("type") != "reply":
                continue
            data = segment.get("data")
            if isinstance(data, dict) and data.get("id") is not None:
                return str(data["id"])
    value = message if isinstance(message, str) else event.get("raw_message")
    if not isinstance(value, str):
        return ""
    match = re.search(r"\[CQ:reply,([^\]]+)\]", value)
    if not match:
        return ""
    for field in match.group(1).split(","):
        if field.startswith("id="):
            return field[3:]
    return ""


def build_websocket_reply_content(
    event: dict[str, Any],
    reply_content: str,
    bot_qq: str = "",
    force_quote: bool = False,
    suppress_quote: bool = False,
) -> str | list[dict[str, Any]]:
    """为群聊事件构造带引用消息段的 WebSocket 回复内容。

    事件包含当前机器人 @ 或引用消息段时自动引用当前触发消息；AI 回复
    可通过 ``force_quote`` 要求同样引用当前消息，也可通过 ``suppress_quote``
    完全禁用引用。缺少当前事件
    message_id 时返回原始字符串，避免向 OneBot 发送无效引用。

    Args:
        event: OneBot 消息事件。
        reply_content: 要发送的回复文本。
        bot_qq: 当前机器人 QQ 号。
        force_quote: 是否强制引用当前群消息，供 AI 回复使用。
        suppress_quote: 是否完全禁止引用当前群消息，供 Redis 免触发 AI 回复使用。
    Returns:
        OneBot 消息段数组或原始回复字符串。
    """
    if event.get("message_type") != "group":
        return reply_content
    if suppress_quote:
        return reply_content
    current_bot_qq = str(bot_qq or event.get("self_id") or "")
    if (
        not force_quote
        and not mentions_bot(event, current_bot_qq)
        and not reply_message_id(event)
    ):
        return reply_content
    message_id = event.get("message_id")
    if message_id is None or str(message_id) == "":
        return reply_content
    return [
        {"type": "reply", "data": {"id": str(message_id)}},
        {"type": "text", "data": {"text": reply_content}},
    ]
