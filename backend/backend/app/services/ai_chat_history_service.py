"""查询 AI 聊天上下文，统一会话范围、发送者角色和当前消息边界。"""

import json
import re
from typing import Any

from sqlalchemy import and_, func, or_, select

from app.common.ai_message_context import (
    format_message_with_sender,
    sender_fields_from_event,
    sender_fields_from_record,
)
from app.common.database import Database
from app.models.entities import ChatMessage


def strip_history_images(text: str) -> str:
    """移除历史图片 CQ 码；参数为历史正文，返回合并空白后的纯文字。"""
    return re.sub(r"\s+", " ", re.sub(r"\[CQ:image,[^\]]*\]", "", text)).strip()


def _session_conditions(event: dict[str, Any], bot_qq: str) -> list[Any]:
    """按事件确定群或私聊范围；参数为事件和机器人 QQ，返回 ORM 条件。"""
    if event.get("group_id") not in (None, "") and event.get("message_type") != "private":
        return [
            ChatMessage.group_id == str(event["group_id"]),
            ChatMessage.message_type != "private",
        ]
    peer_qq = sender_fields_from_event(event)["user_id"]
    if not peer_qq or peer_qq == bot_qq:
        return [ChatMessage.id < 0]
    sender = func.coalesce(
        func.nullif(ChatMessage.sender_user_id, ""), ChatMessage.user_id
    )
    is_bot = or_(ChatMessage.post_type == "message_sent", sender == bot_qq)
    return [
        or_(
            ChatMessage.message_type == "private",
            and_(
                ChatMessage.message_type != "group",
                or_(ChatMessage.group_id.is_(None), ChatMessage.group_id == ""),
            ),
        ),
        or_(
            and_(~is_bot, sender == peer_qq),
            and_(
                is_bot,
                or_(
                    ChatMessage.target_id == peer_qq,
                    and_(
                        or_(
                            ChatMessage.target_id.is_(None), ChatMessage.target_id == ""
                        ),
                        ChatMessage.user_id == peer_qq,
                    ),
                ),
            ),
        ),
    ]


async def load_ai_history(
    database: Database,
    event: dict[str, Any],
    bot_qq: str,
    limit: int = 10,
    current_record_id: int | None = None,
) -> list[dict[str, str]]:
    """查询当前会话中早于触发消息的历史，并保留实际发送者身份。

    Args:
        database: 项目已有的数据库会话工厂。
        event: 当前消息或通知事件。
        bot_qq: 当前机器人 QQ。
        limit: 历史条数上限，零表示关闭历史。
        current_record_id: 当前消息入库主键，兼容没有消息 ID 的事件。
    Returns:
        时间正序的 user/assistant 消息，正文包含 QQ、昵称和群名片。
    """
    if limit <= 0 or not bot_qq:
        return []
    scope = [
        ChatMessage.bot_qq == str(bot_qq),
        *_session_conditions(event, str(bot_qq)),
    ]
    conditions = [
        *scope,
        ChatMessage.post_type.in_(("message", "message_sent")),
        ChatMessage.is_deleted.is_(False),
        ChatMessage.message_text != "",
    ]
    raw_message_id = event.get("message_id")
    message_id = "" if raw_message_id is None else str(raw_message_id)
    record_id = current_record_id or event.get("_chat_message_record_id")
    event_time = func.coalesce(ChatMessage.event_time, ChatMessage.created_at)
    async with database.session_factory() as session:
        current = None
        if record_id is not None or message_id:
            identity = (
                ChatMessage.id == record_id
                if record_id is not None
                else ChatMessage.message_id == message_id
            )
            current = await session.scalar(
                select(ChatMessage)
                .where(*scope, identity)
                .order_by(ChatMessage.id)
                .limit(1)
            )
        if current is not None:
            timestamp = current.event_time or current.created_at
            conditions.append(
                or_(
                    event_time < timestamp,
                    and_(event_time == timestamp, ChatMessage.id < current.id),
                )
            )
        if message_id:
            conditions.append(
                or_(
                    ChatMessage.message_id.is_(None),
                    ChatMessage.message_id != message_id,
                )
            )
        elif current is None:
            original = {
                key: value for key, value in event.items() if not key.startswith("_")
            }
            conditions.append(
                ChatMessage.event_data
                != json.dumps(
                    original, ensure_ascii=False, separators=(",", ":"), default=str
                )
            )
        rows = (
            await session.scalars(
                select(ChatMessage)
                .where(*conditions)
                .order_by(event_time.desc(), ChatMessage.id.desc())
                .limit(limit)
            )
        ).all()
    history = []
    for item in reversed(rows):
        content = strip_history_images(item.message_text)
        if not content:
            continue
        sender = sender_fields_from_record(item)
        history.append(
            {
                "role": "assistant" if sender["user_id"] == str(bot_qq) else "user",
                "content": format_message_with_sender(content, **sender),
            }
        )
    return history
