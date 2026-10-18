"""OneBot 聊天消息持久化服务。

功能：
1. 保存 HTTP、反向 WebSocket 接收及动作成功发送的消息事件。
2. 从事件中提取机器人、会话、发送者和消息内容等常用检索字段。
3. 保留完整原始事件，确保复杂消息段和扩展字段不会丢失。
"""

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.database import Database
from app.common.responses import BusinessError
from app.common.time_utils import SHANGHAI_TIMEZONE, beijing_now
from app.models.entities import BotAccount, ChatMessage
from app.services.ai_chat_history_service import (
    load_ai_history,
)
from app.services.ai_chat_history_service import (
    strip_history_images as _strip_history_images,
)

__all__ = ["ChatMessageService", "_strip_history_images"]

logger = logging.getLogger("uvicorn.error")
MESSAGE_POST_TYPES = frozenset({"message", "message_sent"})


def _bounded(value: Any, max_length: int) -> str:
    """转换为字符串并限制索引字段长度，避免脏数据导致数据库写入失败。"""
    if value is None:
        return ""
    return str(value)[:max_length]


def _optional_id(value: Any, max_length: int) -> str | None:
    """将 OneBot 的数字或字符串 ID 转换为可空字符串。"""
    if value is None or value == "":
        return None
    return _bounded(value, max_length)


def _json_text(value: Any) -> str:
    """将事件字段稳定序列化为 UTF-8 JSON 文本。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _event_time(value: Any) -> datetime | None:
    """把 OneBot Unix 时间戳转换为北京时间无时区时间。"""
    if value is None or value == "":
        return None
    try:
        timestamp = float(value)
        if timestamp > 100_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, SHANGHAI_TIMEZONE).replace(
            tzinfo=None
        )
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _message_text(message: Any) -> str:
    """从字符串或 OneBot 消息段数组中提取可搜索的纯文本。"""
    if isinstance(message, str):
        return message
    if not isinstance(message, list):
        return ""
    parts: list[str] = []
    for segment in message:
        if not isinstance(segment, dict) or segment.get("type") != "text":
            continue
        data = segment.get("data")
        if isinstance(data, dict) and data.get("text") is not None:
            parts.append(str(data["text"]))
    return "".join(parts)


class ChatMessageService:
    """保存 OneBot 聊天消息并补充机器人归属信息。"""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def record_event(
        self,
        event: dict[str, Any],
        source: str,
        fallback_bot_qq: str = "",
    ) -> ChatMessage | None:
        """保存单条聊天事件。

        Args:
            event: OneBot 原始事件对象。
            source: 接收来源，当前为 ``http`` 或 ``websocket``。
            fallback_bot_qq: 事件缺少 ``self_id`` 时使用的连接机器人 QQ。
        Returns:
            新建的聊天记录；非聊天事件返回 ``None``。
        Raises:
            BusinessError: 数据库写入失败时阻止事件继续处理。
        """
        post_type = _bounded(event.get("post_type"), 30)
        if post_type not in MESSAGE_POST_TYPES:
            return None

        bot_qq = _bounded(event.get("self_id") or fallback_bot_qq, 20)
        async with self.database.session_factory() as session:
            bot = await self._find_bot(session, bot_qq)
            record = self._build_record(
                event,
                source,
                bot_qq,
                post_type,
                bot.owner_id if bot else None,
                bot.id if bot else None,
            )
            try:
                session.add(record)
                await session.commit()
            except Exception as exc:
                await session.rollback()
                logger.exception(
                    "聊天记录保存失败，source=%s，bot_qq=%s，message_id=%s",
                    source,
                    bot_qq,
                    record.message_id,
                )
                raise BusinessError("聊天记录保存失败，请检查数据库连接", 50011) from exc
            await session.refresh(record)
            return record

    @staticmethod
    def _build_record(
        event: dict[str, Any],
        source: str,
        bot_qq: str,
        post_type: str,
        owner_id: int | None,
        bot_account_id: int | None,
    ) -> ChatMessage:
        """按 OneBot 事件构造聊天记录实体，实时收发和历史消息共用。

        Args:
            event: OneBot 消息事件或历史消息对象。
            source: 接收来源，如 ``http``、``websocket``、``outgoing``、``history``。
            bot_qq: 归属机器人 QQ。
            post_type: 已归一化的事件类型。
            owner_id: 记录归属用户主键，可为空。
            bot_account_id: 机器人配置主键，可为空。
        Returns:
            未入库的聊天记录实体。
        """
        sender = event.get("sender")
        sender = sender if isinstance(sender, dict) else {}
        message = event.get("message")
        if message is None and "raw_message" in event:
            message = event.get("raw_message")
        raw_message = event.get("raw_message")
        if raw_message is None:
            raw_message = message
        return ChatMessage(
            owner_id=owner_id,
            bot_account_id=bot_account_id,
            bot_qq=bot_qq,
            source=_bounded(source, 20),
            post_type=post_type,
            message_type=_bounded(event.get("message_type"), 20),
            sub_type=_bounded(event.get("sub_type"), 30),
            message_id=_optional_id(event.get("message_id"), 100),
            user_id=_optional_id(event.get("user_id"), 30),
            group_id=_optional_id(event.get("group_id"), 30),
            target_id=_optional_id(event.get("target_id"), 30),
            sender_user_id=_optional_id(sender.get("user_id"), 30),
            sender_nickname=_bounded(sender.get("nickname"), 255),
            sender_card=_bounded(sender.get("card"), 255),
            sender_role=_bounded(sender.get("role"), 30),
            raw_message=(
                raw_message
                if isinstance(raw_message, str)
                else _json_text(raw_message)
            ),
            message_text=_message_text(message),
            message_data=_json_text(message),
            sender_data=_json_text(sender),
            event_data=_json_text(event),
            event_time=_event_time(event.get("time")),
        )

    async def record_history_messages(
        self,
        bot_qq: str,
        messages: list[Any],
        owner_id: int | None = None,
        bot_account_id: int | None = None,
    ) -> dict[str, int]:
        """批量写入拉取到的历史消息，按机器人和消息 ID 去重。

        同一条消息重复同步时只会保留最早写入的记录，已软删除的记录也参与
        去重判断，避免历史记录被重复插入。

        Args:
            bot_qq: 归属机器人 QQ。
            messages: NapCat 返回的历史消息数组。
            owner_id: 记录归属用户主键，来自机器人配置。
            bot_account_id: 机器人配置主键。
        Returns:
            本批统计：读取条数、新增条数、已存在条数和忽略条数。
        Raises:
            BusinessError: 数据库写入失败。
        """
        bot_qq = _bounded(bot_qq, 20)
        candidates: list[tuple[str, dict[str, Any]]] = []
        ignored = 0
        for event in messages:
            if not isinstance(event, dict):
                ignored += 1
                continue
            message_id = _optional_id(event.get("message_id"), 100)
            if not message_id:
                # 没有消息 ID 无法可靠去重，跳过并如实计入忽略数量。
                ignored += 1
                continue
            candidates.append((message_id, event))
        created = 0
        if candidates:
            async with self.database.session_factory() as session:
                bot = await self._find_bot(session, bot_qq)
                saved_ids = set(
                    (
                        await session.scalars(
                            select(ChatMessage.message_id).where(
                                ChatMessage.bot_qq == bot_qq,
                                ChatMessage.message_id.in_(
                                    {item[0] for item in candidates}
                                ),
                            )
                        )
                    ).all()
                )
                for message_id, event in candidates:
                    if message_id in saved_ids:
                        continue
                    saved_ids.add(message_id)
                    post_type = _bounded(event.get("post_type"), 30)
                    session.add(
                        self._build_record(
                            event,
                            "history",
                            bot_qq,
                            post_type if post_type in MESSAGE_POST_TYPES else "message",
                            bot.owner_id if bot else owner_id,
                            bot.id if bot else bot_account_id,
                        )
                    )
                    created += 1
                if created:
                    try:
                        await session.commit()
                    except Exception as exc:
                        await session.rollback()
                        logger.exception(
                            "历史聊天记录保存失败，bot_qq=%s，条数=%s", bot_qq, created
                        )
                        raise BusinessError(
                            "历史聊天记录保存失败，请检查数据库连接", 50012
                        ) from exc
        return {
            "fetched": len(messages),
            "created": created,
            "existing": len(candidates) - created,
            "ignored": ignored,
        }

    async def record_outgoing_group_message(
        self,
        bot_qq: str,
        group_id: str,
        message: Any,
        message_id: Any = None,
    ) -> ChatMessage | None:
        """记录本服务主动发送成功的群消息，作为 ``message_sent`` 事件保存。

        NapCat 并非所有连接模式都会回传机器人自己的 ``message_sent`` 事件，
        因此发送动作成功后由调用方补记一条同格式记录，确保主动回复调度器
        能准确判断群里的最后一条消息是否来自机器人。

        Args:
            bot_qq: 发送消息的机器人 QQ。
            group_id: 目标群号。
            message: OneBot 消息正文或消息段数组。
            message_id: 发送动作返回的消息 ID，可为空。
        Returns:
            新建的聊天记录。
        """
        timestamp = beijing_now().replace(tzinfo=SHANGHAI_TIMEZONE).timestamp()
        event = {
            "self_id": str(bot_qq or ""),
            "time": timestamp,
            "post_type": "message_sent",
            "message_type": "group",
            "sub_type": "normal",
            "message_id": message_id,
            "user_id": str(bot_qq or ""),
            "group_id": str(group_id or ""),
            "sender": {"user_id": str(bot_qq or "")},
            "message": message,
            "raw_message": (
                message if isinstance(message, str) else _json_text(message)
            ),
        }
        return await self.record_event(event, "outgoing", str(bot_qq or ""))

    async def get_ai_history(
        self,
        event: dict[str, Any],
        bot_qq: str,
        limit: int = 10,
        current_record_id: int | None = None,
    ) -> list[dict[str, str]]:
        """读取 AI 当前触发消息之前的最近聊天记录。

        Args:
            event: 当前 OneBot 消息事件，用于限定群组或私聊对象并排除当前消息。
            bot_qq: 当前机器人的 QQ 号。
            limit: 最多返回的历史消息条数。
            current_record_id: 已入库当前记录的主键，用于主动回复场景精确排除当前消息。
        Returns:
            按时间正序排列、附带发送者资料的 user/assistant 消息列表。
        """
        return await load_ai_history(
            self.database, event, bot_qq, limit, current_record_id
        )

    async def mark_ai_triggered(
        self,
        event: dict[str, Any],
        bot_qq: str,
    ) -> int:
        """标记已经成功触发即时 AI 的原始消息记录。

        HTTP 和 WebSocket 可能分别保存同一条消息，按机器人和消息 ID
        一次性更新全部副本，确保主动回复不会再次处理该消息。

        Args:
            event: OneBot 原始消息事件。
            bot_qq: 当前处理消息的机器人 QQ。
        Returns:
            被标记的聊天记录数量。
        """
        conditions = [
            ChatMessage.bot_qq == str(bot_qq or ""),
            ChatMessage.post_type == "message",
            ChatMessage.is_deleted.is_(False),
        ]
        message_id = _optional_id(event.get("message_id"), 100)
        if message_id:
            conditions.append(ChatMessage.message_id == message_id)
        elif event.get("_chat_message_record_id") is not None:
            conditions.append(ChatMessage.id == event["_chat_message_record_id"])
        else:
            conditions.append(ChatMessage.event_data == _json_text(event))
        group_id = _optional_id(event.get("group_id"), 30)
        if group_id:
            conditions.append(ChatMessage.group_id == group_id)
        async with self.database.session_factory() as session:
            records = (
                await session.scalars(select(ChatMessage).where(*conditions))
            ).all()
            changed = 0
            for record in records:
                if record.ai_triggered:
                    continue
                record.ai_triggered = True
                changed += 1
            if changed:
                await session.commit()
            logger.info(
                "即时 AI 消息标记完成，机器人=%s，群=%s，消息=%s，更新记录数=%s",
                bot_qq,
                group_id or "",
                message_id or "无消息ID",
                changed,
            )
            return changed

    @staticmethod
    async def _find_bot(
        session: AsyncSession, bot_qq: str
    ) -> BotAccount | None:
        """按机器人 QQ 查询当前有效的机器人配置。"""
        if not bot_qq:
            return None
        return await session.scalar(
            select(BotAccount).where(
                BotAccount.bot_qq == bot_qq,
                BotAccount.is_deleted.is_(False),
            )
        )
