"""聊天记录和 AI 主动回复记录查询、导出与删除服务。"""

import base64
import csv
import io
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.permissions import owner_scope
from app.common.responses import BusinessError
from app.common.time_utils import beijing_now, format_beijing
from app.models.entities import AiProactiveReplyMessage, ChatMessage, User


# 单次导出的最大行数，超出后要求缩小筛选范围，避免响应体过大。
EXPORT_ROW_LIMIT = 50000
# 导出列定义：表头中文名与序列化字段名一一对应。
EXPORT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("事件时间", "event_time"),
    ("方向", "direction"),
    ("机器人QQ", "bot_qq"),
    ("群号", "group_id"),
    ("发送者QQ", "sender_user_id"),
    ("发送者昵称", "sender_nickname"),
    ("消息内容", "message_text"),
    ("消息ID", "message_id"),
    ("事件类型", "post_type"),
    ("来源", "source"),
    ("AI处理状态", "ai_status"),
    ("所属用户", "owner_username"),
    ("入库时间", "created_at"),
)


class ChatRecordService:
    """按当前用户权限分页查询、导出和软删除聊天及 AI 主动回复记录。"""

    @staticmethod
    def _message_conditions(
        bot_qq: str = "",
        group_id: str = "",
        keyword: str = "",
        post_type: str = "",
        ai_triggered: bool | None = None,
        user_qq: str = "",
        private_only: bool = False,
    ) -> list[Any]:
        """构造聊天记录的查询条件，分页查询和导出共用一套筛选逻辑。"""
        conditions: list[Any] = [ChatMessage.is_deleted.is_(False)]
        if bot_qq:
            conditions.append(ChatMessage.bot_qq == bot_qq)
        if group_id:
            conditions.append(ChatMessage.group_id == group_id)
        if user_qq:
            # 同一个人可能出现在 user_id 或 sender_user_id，两个字段都要匹配。
            conditions.append(
                or_(
                    ChatMessage.user_id == user_qq,
                    ChatMessage.sender_user_id == user_qq,
                    ChatMessage.target_id == user_qq,
                )
            )
        if private_only:
            conditions.append(ChatMessage.group_id.is_(None))
        if keyword:
            conditions.append(
                or_(
                    ChatMessage.message_text.contains(keyword, autoescape=True),
                    ChatMessage.raw_message.contains(keyword, autoescape=True),
                    ChatMessage.sender_nickname.contains(keyword, autoescape=True),
                )
            )
        if post_type:
            conditions.append(ChatMessage.post_type == post_type)
        if ai_triggered is not None:
            if ai_triggered:
                conditions.append(ChatMessage.ai_triggered.is_(True))
            else:
                conditions.append(
                    or_(
                        ChatMessage.ai_triggered.is_(False),
                        ChatMessage.ai_triggered.is_(None),
                    )
                )
        return conditions

    async def list_chat_messages(
        self,
        session: AsyncSession,
        user: User,
        page: int,
        page_size: int,
        bot_qq: str = "",
        group_id: str = "",
        keyword: str = "",
        post_type: str = "",
        ai_triggered: bool | None = None,
        user_qq: str = "",
        private_only: bool = False,
    ) -> dict[str, Any]:
        """分页查询聊天记录，并按事件时间倒序返回。"""
        conditions = self._message_conditions(
            bot_qq, group_id, keyword, post_type, ai_triggered, user_qq, private_only
        )
        query = self._scope_chat(select(ChatMessage).where(*conditions), user)
        count_query = self._scope_chat(
            select(func.count(ChatMessage.id)).where(*conditions), user
        )
        total = int(await session.scalar(count_query) or 0)
        rows = (
            await session.scalars(
                query.order_by(
                    ChatMessage.event_time.is_(None),
                    ChatMessage.event_time.desc(),
                    ChatMessage.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        owners = await self._owner_names(
            session, {item.owner_id for item in rows if item.owner_id is not None}
        )
        return {
            "items": [self._serialize_chat(item, owners) for item in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    async def export_chat_messages(
        self,
        session: AsyncSession,
        user: User,
        bot_qq: str = "",
        group_id: str = "",
        keyword: str = "",
        post_type: str = "",
        ai_triggered: bool | None = None,
        user_qq: str = "",
        private_only: bool = False,
    ) -> dict[str, Any]:
        """按当前筛选条件把聊天记录导出为 CSV。

        导出范围与列表查询完全一致（含用户数据范围），按事件时间倒序输出。
        内容带 UTF-8 BOM，Excel 可直接打开中文不乱码。

        Args:
            session: 数据库会话。
            user: 当前登录用户，用于限定数据范围。
            bot_qq: 机器人 QQ 筛选条件。
            group_id: 群号筛选条件。
            keyword: 关键字筛选条件。
            post_type: 事件类型筛选条件。
            ai_triggered: AI 处理状态筛选条件。
            user_qq: 好友或成员 QQ 筛选条件。
            private_only: 只导出私聊记录。
        Returns:
            含文件名、媒体类型、Base64 内容和行数的导出结果。
        Raises:
            BusinessError: 没有可导出的记录或超出单次导出上限。
        """
        conditions = self._message_conditions(
            bot_qq, group_id, keyword, post_type, ai_triggered, user_qq, private_only
        )
        total = int(
            await session.scalar(
                self._scope_chat(
                    select(func.count(ChatMessage.id)).where(*conditions), user
                )
            )
            or 0
        )
        if not total:
            raise BusinessError("当前筛选条件下没有可导出的聊天记录", 40056)
        if total > EXPORT_ROW_LIMIT:
            raise BusinessError(
                f"导出记录数 {total} 条超过单次上限 {EXPORT_ROW_LIMIT} 条，请缩小筛选范围",
                40057,
            )
        rows = (
            await session.scalars(
                self._scope_chat(select(ChatMessage).where(*conditions), user).order_by(
                    ChatMessage.event_time.is_(None),
                    ChatMessage.event_time.desc(),
                    ChatMessage.id.desc(),
                )
            )
        ).all()
        owners = await self._owner_names(
            session, {item.owner_id for item in rows if item.owner_id is not None}
        )
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow([title for title, _ in EXPORT_COLUMNS])
        for item in rows:
            record = self._serialize_chat(item, owners)
            record["ai_status"] = "已处理" if record["ai_triggered"] else "未处理"
            writer.writerow(
                [self._csv_cell(record.get(field)) for _, field in EXPORT_COLUMNS]
            )
        content = "﻿" + buffer.getvalue()
        file_name = f"聊天记录_{beijing_now().strftime('%Y%m%d_%H%M%S')}.csv"
        return {
            "file_name": file_name,
            "media_type": "text/csv",
            "total": len(rows),
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        }

    async def soft_delete_messages(
        self, session: AsyncSession, user: User, message_ids: list[int]
    ) -> int:
        """按勾选 ID 批量软删除聊天记录，不物理删除数据。

        Args:
            session: 数据库会话。
            user: 当前登录用户，用于校验数据范围。
            message_ids: 勾选的聊天记录主键列表。
        Returns:
            实际标记删除的记录数。
        Raises:
            BusinessError: 未勾选记录，或存在不属于当前用户的记录。
        """
        ids = sorted({int(item) for item in message_ids})
        if not ids:
            raise BusinessError("请至少勾选一条聊天记录", 40058)
        rows = (
            await session.scalars(
                self._scope_chat(
                    select(ChatMessage).where(
                        ChatMessage.id.in_(ids),
                        ChatMessage.is_deleted.is_(False),
                    ),
                    user,
                )
            )
        ).all()
        if len(rows) != len(ids):
            raise BusinessError("部分聊天记录不存在或无权访问", 40451)
        now = beijing_now()
        for item in rows:
            item.is_deleted = True
            item.updated_at = now
        await session.commit()
        return len(rows)

    @staticmethod
    def _csv_cell(value: Any) -> str:
        """把字段值转换为 CSV 单元格文本，并防止公式注入。"""
        text = "" if value is None else str(value)
        text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        if text[:1] in ("=", "+", "-", "@", "\t"):
            # Excel 会把这些前缀当公式执行，导出前统一转成文本。
            text = f"'{text}"
        return text

    async def list_ai_replies(
        self,
        session: AsyncSession,
        user: User,
        page: int,
        page_size: int,
        bot_qq: str = "",
        group_id: str = "",
    ) -> dict[str, Any]:
        """分页查询 AI 主动回复处理记录，并按处理时间倒序返回。"""
        conditions = [AiProactiveReplyMessage.is_deleted.is_(False)]
        if bot_qq:
            conditions.append(AiProactiveReplyMessage.bot_qq == bot_qq)
        if group_id:
            conditions.append(AiProactiveReplyMessage.group_id == group_id)
        query = self._scope_ai(select(AiProactiveReplyMessage).where(*conditions), user)
        count_query = self._scope_ai(
            select(func.count(AiProactiveReplyMessage.id)).where(*conditions), user
        )
        total = int(await session.scalar(count_query) or 0)
        rows = (
            await session.scalars(
                query.order_by(
                    AiProactiveReplyMessage.created_at.desc(),
                    AiProactiveReplyMessage.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        source_messages = await self._source_messages(session, rows, user)
        owners = await self._owner_names(session, {item.owner_id for item in rows})
        return {
            "items": [
                self._serialize_ai_reply(item, source_messages, owners)
                for item in rows
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    @staticmethod
    def _scope_chat(query: Select, user: User) -> Select:
        """为聊天记录查询追加普通用户的数据范围。"""
        owner_id = owner_scope(user)
        if owner_id is not None:
            query = query.where(ChatMessage.owner_id == owner_id)
        return query

    @staticmethod
    def _scope_ai(query: Select, user: User) -> Select:
        """为 AI 记录查询追加普通用户的数据范围。"""
        owner_id = owner_scope(user)
        if owner_id is not None:
            query = query.where(AiProactiveReplyMessage.owner_id == owner_id)
        return query

    @staticmethod
    async def _owner_names(
        session: AsyncSession, owner_ids: set[int]
    ) -> dict[int, str]:
        """批量读取记录所属用户名称。"""
        if not owner_ids:
            return {}
        rows = await session.execute(
            select(User.id, User.username).where(User.id.in_(owner_ids))
        )
        return {row.id: row.username for row in rows}

    async def _source_messages(
        self,
        session: AsyncSession,
        rows: list[AiProactiveReplyMessage],
        user: User,
    ) -> dict[tuple[str, str, str], ChatMessage]:
        """批量读取 AI 记录对应的原始聊天消息。"""
        message_ids = {item.message_id for item in rows if item.message_id}
        bot_ids = {item.bot_qq for item in rows}
        if not message_ids or not bot_ids:
            return {}
        query = self._scope_chat(
            select(ChatMessage).where(
                ChatMessage.is_deleted.is_(False),
                ChatMessage.bot_qq.in_(bot_ids),
                ChatMessage.message_id.in_(message_ids),
            ),
            user,
        )
        source_rows = (
            await session.scalars(
                query.order_by(ChatMessage.id.desc())
            )
        ).all()
        result: dict[tuple[str, str, str], ChatMessage] = {}
        for item in source_rows:
            key = (item.bot_qq, str(item.group_id or ""), str(item.message_id or ""))
            result.setdefault(key, item)
        return result

    @staticmethod
    def _serialize_chat(item: ChatMessage, owners: dict[int, str]) -> dict[str, Any]:
        """序列化聊天记录，区分用户发送和机器人发送。"""
        is_bot = item.post_type == "message_sent" or str(
            item.user_id or item.sender_user_id or ""
        ) == item.bot_qq
        return {
            "id": item.id,
            "owner_id": item.owner_id,
            "owner_username": owners.get(item.owner_id, "未知用户")
            if item.owner_id is not None
            else "未关联用户",
            "bot_qq": item.bot_qq,
            "group_id": item.group_id or "",
            "message_id": item.message_id or "",
            "sender_user_id": item.sender_user_id or item.user_id or "",
            "sender_nickname": item.sender_nickname or item.sender_card or "",
            "message_text": item.message_text or item.raw_message or "",
            "post_type": item.post_type,
            "source": item.source,
            "direction": "机器人发送" if is_bot else "用户发送",
            "ai_triggered": bool(item.ai_triggered),
            "event_time": format_beijing(item.event_time or item.created_at),
            "created_at": format_beijing(item.created_at),
        }

    @staticmethod
    def _serialize_ai_reply(
        item: AiProactiveReplyMessage,
        source_messages: dict[tuple[str, str, str], ChatMessage],
        owners: dict[int, str],
    ) -> dict[str, Any]:
        """序列化 AI 主动回复记录及其原始消息摘要。"""
        source = source_messages.get(
            (item.bot_qq, item.group_id, item.message_id)
        )
        return {
            "id": item.id,
            "owner_id": item.owner_id,
            "owner_username": owners.get(item.owner_id, "未知用户"),
            "rule_id": item.rule_id,
            "bot_qq": item.bot_qq,
            "group_id": item.group_id,
            "message_id": item.message_id,
            "message_key": item.message_key,
            "source_message": source.message_text if source else "",
            "source_sender": (
                source.sender_nickname or source.sender_card if source else ""
            ),
            "processed_at": format_beijing(item.created_at),
            "updated_at": format_beijing(item.updated_at),
        }
