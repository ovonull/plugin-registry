"""自动回复规则 CRUD、分页查询和 OneBot 消息匹配服务。"""

import re
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.permissions import ensure_rule_access, is_admin, owner_scope
from app.common.responses import BusinessError
from app.common.time_utils import beijing_now, format_beijing
from app.models.entities import AutoReplyRule, BotAccount, User
from app.schemas import AutoReplyCreateRequest, AutoReplyUpdateRequest
from app.services.bot_service import BotService
from app.services.onebot_reply import is_poke_event


class AutoReplyService:
    """管理规则数据并为真实 OneBot 消息选择回复。"""

    @staticmethod
    def _scope_query(query: Select, user: User) -> Select:
        """为普通用户追加 owner_id 数据隔离条件。"""
        owner_id = owner_scope(user)
        if owner_id is not None:
            query = query.where(AutoReplyRule.owner_id == owner_id)
        return query

    async def list_rules(
        self,
        session: AsyncSession,
        user: User,
        page: int,
        page_size: int,
        keyword: str = "",
        bot_qq: str = "",
        enabled: bool | None = None,
    ) -> dict:
        """按权限和查询条件返回后端分页规则列表。"""
        conditions = [AutoReplyRule.is_deleted.is_(False)]
        if keyword:
            conditions.append(AutoReplyRule.keyword.contains(keyword, autoescape=True))
        if bot_qq:
            conditions.append(AutoReplyRule.bot_qq == bot_qq)
        if enabled is not None:
            conditions.append(AutoReplyRule.is_enabled.is_(enabled))

        base_query = self._scope_query(
            select(AutoReplyRule).where(*conditions), user
        )
        count_query = self._scope_query(
            select(func.count(AutoReplyRule.id)).where(*conditions), user
        )
        total = int(await session.scalar(count_query) or 0)
        rules = (
            await session.scalars(
                base_query.order_by(AutoReplyRule.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        owner_names = await self._owner_names(session, {rule.owner_id for rule in rules})
        return {
            "items": [self.serialize_rule(rule, owner_names) for rule in rules],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    async def create_rule(
        self, session: AsyncSession, user: User, payload: AutoReplyCreateRequest
    ) -> dict:
        """校验全局机器人关键字冲突后新增规则。"""
        await self._ensure_bot_access(session, user, payload.bot_qq)
        await self._ensure_unique(session, payload.bot_qq, payload.keyword)
        rule = AutoReplyRule(
            owner_id=user.id,
            keyword=payload.keyword,
            bot_qq=payload.bot_qq,
            reply_content=payload.reply_content,
            is_enabled=payload.is_enabled,
        )
        session.add(rule)
        await session.commit()
        await session.refresh(rule)
        return self.serialize_rule(rule, {user.id: user.username})

    async def update_rule(
        self,
        session: AsyncSession,
        user: User,
        rule_id: int,
        payload: AutoReplyUpdateRequest,
    ) -> dict:
        """检查权限和冲突后修改规则。"""
        rule = await self._get_active_rule(session, rule_id)
        ensure_rule_access(user, rule)
        await self._ensure_bot_access(session, user, payload.bot_qq)
        await self._ensure_unique(
            session, payload.bot_qq, payload.keyword, exclude_id=rule.id
        )
        rule.keyword = payload.keyword
        rule.bot_qq = payload.bot_qq
        rule.reply_content = payload.reply_content
        rule.is_enabled = payload.is_enabled
        rule.updated_at = beijing_now()
        await session.commit()
        owner = await session.scalar(select(User.username).where(User.id == rule.owner_id))
        return self.serialize_rule(rule, {rule.owner_id: owner or "未知用户"})

    async def soft_delete_rule(
        self, session: AsyncSession, user: User, rule_id: int
    ) -> None:
        """检查权限后软删除规则，不移除真实数据。"""
        rule = await self._get_active_rule(session, rule_id)
        ensure_rule_access(user, rule)
        rule.is_deleted = True
        rule.updated_at = beijing_now()
        await session.commit()

    async def dashboard_summary(self, session: AsyncSession, user: User) -> dict:
        """返回当前权限数据范围内的规则统计和最近记录。"""
        base_conditions = [AutoReplyRule.is_deleted.is_(False)]
        owner_id = owner_scope(user)
        if owner_id is not None:
            base_conditions.append(AutoReplyRule.owner_id == owner_id)
        total = int(
            await session.scalar(
                select(func.count(AutoReplyRule.id)).where(*base_conditions)
            )
            or 0
        )
        enabled = int(
            await session.scalar(
                select(func.count(AutoReplyRule.id)).where(
                    *base_conditions, AutoReplyRule.is_enabled.is_(True)
                )
            )
            or 0
        )
        bot_count = int(
            await session.scalar(
                select(func.count(func.distinct(AutoReplyRule.bot_qq))).where(
                    *base_conditions
                )
            )
            or 0
        )
        recent_rules = (
            await session.scalars(
                select(AutoReplyRule)
                .where(*base_conditions)
                .order_by(AutoReplyRule.id.desc())
                .limit(5)
            )
        ).all()
        owner_names = await self._owner_names(
            session, {rule.owner_id for rule in recent_rules}
        )
        return {
            "total_rules": total,
            "enabled_rules": enabled,
            "disabled_rules": total - enabled,
            "bot_count": bot_count,
            "recent_rules": [
                self.serialize_rule(rule, owner_names) for rule in recent_rules
            ],
        }

    async def match_event(
        self, session: AsyncSession, event: dict[str, Any]
    ) -> AutoReplyRule | None:
        """按机器人 QQ 和消息文本匹配最长的启用关键字规则。"""
        if event.get("post_type") != "message" and not is_poke_event(event):
            return None
        self_id = str(event.get("self_id", ""))
        user_id = str(event.get("user_id", ""))
        if not self_id or self_id == user_id:
            return None
        message_text = self.extract_message_text(event)
        if not message_text:
            return None
        if not await BotService.is_event_allowed(session, event):
            return None
        rules = (
            await session.scalars(
                select(AutoReplyRule).where(
                    AutoReplyRule.bot_qq == self_id,
                    AutoReplyRule.is_enabled.is_(True),
                    AutoReplyRule.is_deleted.is_(False),
                )
            )
        ).all()
        matching = [rule for rule in rules if rule.keyword in message_text]
        if not matching:
            return None
        return sorted(matching, key=lambda item: (-len(item.keyword), item.id))[0]

    @staticmethod
    async def _is_event_allowed(
        session: AsyncSession, bot_qq: str, event: dict[str, Any]
    ) -> bool:
        """Apply bot enabled state and group policy to incoming messages."""
        return await BotService.is_event_allowed(session, event)

    @staticmethod
    def extract_message_text(event: dict[str, Any]) -> str:
        """兼容字符串和消息段数组格式，提取可匹配文本。"""
        resolved_forward_text = event.get("_forward_message_text")
        resolved_forward_text = (
            str(resolved_forward_text).strip()
            if resolved_forward_text is not None
            else ""
        )
        raw_message = event.get("raw_message")
        if isinstance(raw_message, str) and raw_message:
            if (
                resolved_forward_text
                or "_forward_message_contents" in event
                or "_forward_message_unavailable" in event
            ):
                raw_message = re.sub(
                    r"\[CQ:forward(?:,[^\]]+)?\]",
                    "",
                    raw_message,
                    flags=re.IGNORECASE,
                ).strip()
                return "\n".join(
                    value for value in (raw_message, resolved_forward_text) if value
                )
            return raw_message
        if is_poke_event(event):
            return "戳一戳"
        message = event.get("message")
        if isinstance(message, str):
            if (
                resolved_forward_text
                or "_forward_message_contents" in event
                or "_forward_message_unavailable" in event
            ):
                message = re.sub(
                    r"\[CQ:forward(?:,[^\]]+)?\]",
                    "",
                    message,
                    flags=re.IGNORECASE,
                ).strip()
            return "\n".join(
                value for value in (message.strip(), resolved_forward_text) if value
            )
        if not isinstance(message, list):
            return resolved_forward_text
        text_parts: list[str] = []
        for segment in message:
            if not isinstance(segment, dict) or segment.get("type") != "text":
                continue
            data = segment.get("data")
            if isinstance(data, dict) and isinstance(data.get("text"), str):
                text_parts.append(data["text"])
        return "\n".join(
            value for value in ("".join(text_parts), resolved_forward_text) if value
        )

    async def _ensure_unique(
        self,
        session: AsyncSession,
        bot_qq: str,
        keyword: str,
        exclude_id: int | None = None,
    ) -> None:
        """避免相同机器人和关键字由多个用户配置出冲突回复。"""
        query = select(AutoReplyRule.id).where(
            AutoReplyRule.bot_qq == bot_qq,
            AutoReplyRule.keyword == keyword,
            AutoReplyRule.is_deleted.is_(False),
        )
        if exclude_id is not None:
            query = query.where(AutoReplyRule.id != exclude_id)
        if await session.scalar(query) is not None:
            raise BusinessError("该机器人已存在相同关键字规则", 40020)

    @staticmethod
    async def _ensure_bot_access(
        session: AsyncSession, user: User, bot_qq: str
    ) -> None:
        """Prevent a regular user from creating rules for another user's bot."""
        bot = await session.scalar(
            select(BotAccount).where(
                BotAccount.bot_qq == bot_qq,
                BotAccount.is_deleted.is_(False),
            )
        )
        if bot is None:
            raise BusinessError("请先在机器人管理中添加该 QQ", 40430)
        if bot is not None and not is_admin(user) and bot.owner_id != user.id:
            raise BusinessError("无权为该机器人配置自动回复", 40332)

    @staticmethod
    async def _get_active_rule(
        session: AsyncSession, rule_id: int
    ) -> AutoReplyRule:
        """获取未删除规则，不存在时返回明确业务错误。"""
        rule = await session.scalar(
            select(AutoReplyRule).where(
                AutoReplyRule.id == rule_id, AutoReplyRule.is_deleted.is_(False)
            )
        )
        if rule is None:
            raise BusinessError("自动回复规则不存在", 40401)
        return rule

    @staticmethod
    async def _owner_names(
        session: AsyncSession, owner_ids: set[int]
    ) -> dict[int, str]:
        """批量查询规则所属用户名，避免逐行查询。"""
        if not owner_ids:
            return {}
        rows = await session.execute(
            select(User.id, User.username).where(User.id.in_(owner_ids))
        )
        return {row.id: row.username for row in rows}

    @staticmethod
    def serialize_rule(rule: AutoReplyRule, owner_names: dict[int, str]) -> dict:
        """将规则模型转换为前端响应结构。"""
        return {
            "id": rule.id,
            "owner_id": rule.owner_id,
            "owner_username": owner_names.get(rule.owner_id, "未知用户"),
            "keyword": rule.keyword,
            "bot_qq": rule.bot_qq,
            "reply_content": rule.reply_content,
            "is_enabled": rule.is_enabled,
            "created_at": format_beijing(rule.created_at),
            "updated_at": format_beijing(rule.updated_at),
        }
