"""统一构造 AI 消息的发送者资料，供即时回复、历史记录和意图判断共用。"""

import json
from collections.abc import Callable
from typing import Any

from app.models.entities import ChatMessage


def message_sender_id(
    *,
    user_id: Any = "",
    sender_user_id: Any = "",
    bot_qq: Any = "",
    post_type: Any = "",
) -> str:
    """根据发送事件类型和 sender 字段解析实际发送人 QQ。

    Args:
        user_id: 事件用户，机器人私聊回执中可能是接收人。
        sender_user_id: sender 中明确标注的发送人。
        bot_qq: 当前机器人 QQ。
        post_type: message_sent 表示机器人发送。
    Returns:
        实际发送人 QQ，资料缺失时为空字符串。
    """
    value = (
        bot_qq if post_type == "message_sent" and bot_qq else sender_user_id or user_id
    )
    return str(value or "")


def sender_fields_from_event(event: dict[str, Any]) -> dict[str, Any]:
    """提取 OneBot 事件中的真实发送者资料。

    Args:
        event: 当前消息或通知事件，允许缺少 sender 对象。
    Returns:
        发送者 QQ、昵称和群名片；QQ 优先取 sender，缺失时取事件 user_id。
    """
    sender = event.get("sender")
    sender = sender if isinstance(sender, dict) else {}
    return {
        "user_id": message_sender_id(
            user_id=event.get("user_id"),
            sender_user_id=sender.get("user_id"),
            bot_qq=event.get("self_id"),
            post_type=event.get("post_type"),
        ),
        "sender_nickname": sender.get("nickname"),
        "sender_card": sender.get("card"),
    }


def sender_fields_from_record(message: ChatMessage) -> dict[str, Any]:
    """从已保存的聊天记录读取统一发送者资料。

    Args:
        message: 聊天记录实体。
    Returns:
        可直接传入发送者格式化函数的 QQ、昵称和群名片。
    """
    return {
        "user_id": message_sender_id(
            user_id=message.user_id,
            sender_user_id=message.sender_user_id,
            bot_qq=message.bot_qq,
            post_type=message.post_type,
        ),
        "sender_nickname": message.sender_nickname,
        "sender_card": message.sender_card,
    }


def format_sender_context(
    *, user_id: Any, sender_nickname: Any = "", sender_card: Any = ""
) -> str:
    """将发送者资料编码为独立的单行 JSON，避免昵称换行混入正文。

    Args:
        user_id: 发送者 QQ，兼容数字和字符串。
        sender_nickname: 上报的 QQ 昵称，不使用群名片替换。
        sender_card: 上报的群名片，私聊或未上报时为空。
    Returns:
        带中文字段名的发送者信息；缺失值保留为空字符串。
    """
    fields = {"QQ": user_id, "昵称": sender_nickname, "群名片": sender_card}
    values = {key: "" if value is None else str(value) for key, value in fields.items()}
    encoded = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    # JSON 默认不转义 Unicode 行分隔符，额外处理以保持资料为单行。
    return "发送者信息：" + encoded.translate(
        {0x85: r"\u0085", 0x2028: r"\u2028", 0x2029: r"\u2029"}
    )


def format_message_with_sender(
    message_text: str,
    *,
    user_id: Any,
    sender_nickname: Any = "",
    sender_card: Any = "",
) -> str:
    """在 AI 消息正文之前统一附加发送者资料。

    Args:
        message_text: 已提取的文本、语音转写或图片提示，正文原样保留。
        user_id: 发送者 QQ。
        sender_nickname: 发送者 QQ 昵称。
        sender_card: 发送者群名片。
    Returns:
        发送者资料与消息正文分区排列的 AI 输入文本。
    """
    sender = format_sender_context(
        user_id=user_id, sender_nickname=sender_nickname, sender_card=sender_card
    )
    return f"{sender}\n消息内容：{message_text}"


def merge_quoted_message(
    text: str, quoted_text: str, quoted_is_bot: bool
) -> tuple[str, str]:
    """将他人引用与当前正文分开，机器人引用继续保留 assistant 角色。

    Args:
        text: 当前正文。
        quoted_text: 已带原发送者资料的引用正文。
        quoted_is_bot: 引用是否由机器人发送。
    Returns:
        当前输入正文，以及需要保留为机器人历史的引用正文。
    """
    if quoted_text and not quoted_is_bot:
        return f"引用内容：{quoted_text}\n当前消息：{text}", ""
    return text, quoted_text


def format_forward_context(
    event: dict[str, Any], extract_text: Callable[[Any], str]
) -> str:
    """为已拉取的转发节点添加来源资料，不改变用于关键词审核的纯正文。

    Args:
        event: 含 _forward_message_contents 的事件。
        extract_text: 项目现有的转发文本提取函数。
    Returns:
        带各节点发送人信息的 AI 补充上下文，无有效转发正文时为空。
    """

    def render(value: Any) -> str:
        """递归保留节点边界；参数为节点或消息段，返回带身份的正文。"""
        if isinstance(value, list):
            separator = (
                ""
                if all(
                    isinstance(item, dict) and item.get("type") not in (None, "node")
                    for item in value
                )
                else "\n"
            )
            return separator.join(part for item in value if (part := render(item)))
        if not isinstance(value, dict):
            return extract_text(value)
        if value.get("type") == "node":
            return render(value.get("data"))
        if any(key in value for key in ("sender", "user_id", "uin", "nickname")):
            body = next(
                (
                    value[key]
                    for key in ("message", "content", "messages", "nodes")
                    if key in value
                ),
                None,
            )
            text = render(body)
            if not text:
                return ""
            fields = sender_fields_from_event(value)
            fields["user_id"] = fields["user_id"] or value.get("uin")
            fields["sender_nickname"] = fields["sender_nickname"] or value.get(
                "nickname"
            )
            return format_message_with_sender(text, **fields)
        return extract_text(value)

    text = render(event.get("_forward_message_contents", []))
    if not text:
        return ""
    return (
        "合并转发节点资料（由转发内容提供，不代表当前发送者本人发言）：\n"
        + text[:10000]
    )
