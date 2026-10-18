"""Bot account and group allowlist management."""

from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.permissions import is_admin, owner_scope
from app.common.responses import BusinessError
from app.common.time_utils import beijing_now, format_beijing
from app.models.entities import (
    AiProactiveReplyRule, AutoReplyRule, BotAccount, BotAiConfig,
    BotGroup, AiVoiceCharacterRule, ForbiddenWordRule, User, WelcomeMessageRule,
)
from app.schemas import BotCreateRequest, BotGroupRequest, BotUpdateRequest


class BotService:
    """Keeps NapCat actions scoped to a configured bot QQ account."""

    @staticmethod
    def _scope(query: Select, user: User) -> Select:
        owner_id = owner_scope(user)
        if owner_id is not None:
            query = query.where(BotAccount.owner_id == owner_id)
        return query

    async def list_bots(
        self, session: AsyncSession, user: User, connected: set[str] | None = None
    ) -> list[dict]:
        rows = (
            await session.scalars(
                self._scope(
                    select(BotAccount).where(BotAccount.is_deleted.is_(False)), user
                ).order_by(BotAccount.id.desc())
            )
        ).all()
        connected = connected or set()
        owner_rows = await session.execute(
            select(User.id, User.username).where(
                User.id.in_({item.owner_id for item in rows})
            )
        ) if rows else None
        owner_names = (
            {row.id: row.username for row in owner_rows} if owner_rows else {}
        )
        return [
            self.serialize_bot(
                item, str(item.bot_qq) in connected, owner_names
            )
            for item in rows
        ]

    async def create_bot(
        self, session: AsyncSession, user: User, payload: BotCreateRequest
    ) -> dict:
        self._validate_connection(payload)
        existing = await session.scalar(
            select(BotAccount).where(BotAccount.bot_qq == payload.bot_qq)
        )
        if existing and not existing.is_deleted:
            if not is_admin(user) or existing.owner_id != user.id:
                raise BusinessError("该机器人 QQ 已被配置", 40030)
            raise BusinessError("该机器人 QQ 已存在", 40031)
        if existing:
            if not is_admin(user) and existing.owner_id != user.id:
                raise BusinessError("该机器人 QQ 已被其他用户配置", 40333)
            existing.owner_id = user.id
            existing.nickname = payload.nickname
            existing.admin_qq = payload.admin_qq
            existing.connection_mode = payload.connection_mode
            existing.http_url = payload.http_url
            if payload.access_token:
                existing.access_token = payload.access_token
            existing.group_mode = payload.group_mode
            existing.is_enabled = payload.is_enabled
            if payload.welcome_enabled is not None:
                existing.welcome_enabled = payload.welcome_enabled
            existing.auto_approve_friend = payload.auto_approve_friend
            existing.auto_approve_group_request = payload.auto_approve_group_request
            existing.is_deleted = False
            existing.updated_at = beijing_now()
            await session.execute(
                WelcomeMessageRule.__table__.update()
                .where(WelcomeMessageRule.bot_qq == payload.bot_qq)
                .values(owner_id=user.id)
            )
            await session.execute(
                AiVoiceCharacterRule.__table__.update()
                .where(AiVoiceCharacterRule.bot_qq == payload.bot_qq)
                .values(owner_id=user.id)
            )
            await session.commit()
            await session.refresh(existing)
            return self.serialize_bot(existing, False, {user.id: user.username})
        bot = BotAccount(
            owner_id=user.id,
            bot_qq=payload.bot_qq,
            nickname=payload.nickname,
            admin_qq=payload.admin_qq,
            connection_mode=payload.connection_mode,
            http_url=payload.http_url,
            access_token=payload.access_token,
            group_mode=payload.group_mode,
            is_enabled=payload.is_enabled,
            welcome_enabled=payload.welcome_enabled,
            auto_approve_friend=payload.auto_approve_friend,
            auto_approve_group_request=payload.auto_approve_group_request,
        )
        session.add(bot)
        await session.commit()
        await session.refresh(bot)
        return self.serialize_bot(bot, False, {user.id: user.username})

    async def update_bot(
        self,
        session: AsyncSession,
        user: User,
        bot_qq: str,
        payload: BotUpdateRequest,
    ) -> dict:
        self._validate_connection(payload)
        bot = await self.get_bot(session, user, bot_qq)
        if payload.bot_qq != bot_qq:
            conflict = await session.scalar(
                select(BotAccount.id).where(
                    BotAccount.bot_qq == payload.bot_qq,
                    BotAccount.id != bot.id,
                )
            )
            if conflict:
                raise BusinessError("目标机器人 QQ 已存在", 40032)
            bot.bot_qq = payload.bot_qq
            await session.execute(
                BotGroup.__table__.update()
                .where(BotGroup.bot_qq == bot_qq)
                .values(bot_qq=payload.bot_qq)
            )
            await session.execute(
                AutoReplyRule.__table__.update()
                .where(AutoReplyRule.bot_qq == bot_qq)
                .values(bot_qq=payload.bot_qq)
            )
            await session.execute(
                BotAiConfig.__table__.update()
                .where(BotAiConfig.bot_qq == bot_qq)
                .values(bot_qq=payload.bot_qq)
            )
            await session.execute(
                AiProactiveReplyRule.__table__.update()
                .where(AiProactiveReplyRule.bot_qq == bot_qq)
                .values(bot_qq=payload.bot_qq)
            )
            await session.execute(
                WelcomeMessageRule.__table__.update()
                .where(WelcomeMessageRule.bot_qq == bot_qq)
                .values(bot_qq=payload.bot_qq)
            )
            await session.execute(
                ForbiddenWordRule.__table__.update()
                .where(ForbiddenWordRule.bot_qq == bot_qq)
                .values(bot_qq=payload.bot_qq)
            )
            await session.execute(
                AiVoiceCharacterRule.__table__.update()
                .where(AiVoiceCharacterRule.bot_qq == bot_qq)
                .values(bot_qq=payload.bot_qq)
            )
        bot.nickname = payload.nickname
        bot.admin_qq = payload.admin_qq
        bot.connection_mode = payload.connection_mode
        bot.http_url = payload.http_url
        if payload.access_token:
            bot.access_token = payload.access_token
        bot.group_mode = payload.group_mode
        bot.is_enabled = payload.is_enabled
        if payload.welcome_enabled is not None:
            bot.welcome_enabled = payload.welcome_enabled
        bot.auto_approve_friend = payload.auto_approve_friend
        bot.auto_approve_group_request = payload.auto_approve_group_request
        bot.updated_at = beijing_now()
        await session.commit()
        await session.refresh(bot)
        return self.serialize_bot(bot, False)

    async def delete_bot(self, session: AsyncSession, user: User, bot_qq: str) -> None:
        bot = await self.get_bot(session, user, bot_qq)
        bot.is_deleted = True
        bot.is_enabled = False
        bot.updated_at = beijing_now()
        await session.execute(
            AutoReplyRule.__table__.update()
            .where(
                AutoReplyRule.bot_qq == bot_qq,
                AutoReplyRule.is_deleted.is_(False),
            )
            .values(is_enabled=False)
        )
        await session.execute(
            BotAiConfig.__table__.update()
            .where(
                BotAiConfig.bot_qq == bot_qq,
                BotAiConfig.is_deleted.is_(False),
            )
            .values(
                is_enabled=False,
                is_deleted=True,
                updated_at=beijing_now(),
            )
        )
        await session.execute(
            AiProactiveReplyRule.__table__.update()
            .where(
                AiProactiveReplyRule.bot_qq == bot_qq,
                AiProactiveReplyRule.is_deleted.is_(False),
            )
            .values(is_enabled=False, updated_at=beijing_now())
        )
        await session.execute(
            ForbiddenWordRule.__table__.update()
            .where(
                ForbiddenWordRule.bot_qq == bot_qq,
                ForbiddenWordRule.is_deleted.is_(False),
            )
            .values(is_enabled=False, updated_at=beijing_now())
        )
        await session.execute(
            AiVoiceCharacterRule.__table__.update()
            .where(
                AiVoiceCharacterRule.bot_qq == bot_qq,
                AiVoiceCharacterRule.is_deleted.is_(False),
            )
            .values(is_enabled=False, updated_at=beijing_now())
        )
        await session.commit()

    async def list_groups(
        self, session: AsyncSession, user: User, bot_qq: str
    ) -> list[dict]:
        await self.get_bot(session, user, bot_qq)
        rows = (
            await session.scalars(
                select(BotGroup)
                .where(
                    BotGroup.bot_qq == bot_qq,
                    BotGroup.is_deleted.is_(False),
                )
                .order_by(BotGroup.group_id)
            )
        ).all()
        return [self.serialize_group(item) for item in rows]

    async def upsert_group(
        self,
        session: AsyncSession,
        user: User,
        bot_qq: str,
        payload: BotGroupRequest,
    ) -> dict:
        bot = await self.get_bot(session, user, bot_qq)
        group = await session.scalar(
            select(BotGroup).where(
                BotGroup.bot_qq == bot.bot_qq,
                BotGroup.group_id == payload.group_id,
            )
        )
        if group is None:
            group = BotGroup(
                owner_id=bot.owner_id,
                bot_qq=bot.bot_qq,
                group_id=payload.group_id,
                group_name=payload.group_name,
                is_enabled=payload.is_enabled,
            )
            session.add(group)
        else:
            group.group_name = payload.group_name
            group.is_enabled = payload.is_enabled
            group.is_deleted = False
            group.updated_at = beijing_now()
        await session.commit()
        await session.refresh(group)
        return self.serialize_group(group)

    async def delete_group(
        self, session: AsyncSession, user: User, bot_qq: str, group_id: str
    ) -> None:
        await self.get_bot(session, user, bot_qq)
        group = await session.scalar(
            select(BotGroup).where(
                BotGroup.bot_qq == bot_qq,
                BotGroup.group_id == group_id,
                BotGroup.is_deleted.is_(False),
            )
        )
        if group is None:
            raise BusinessError("群配置不存在", 40431)
        group.is_deleted = True
        group.updated_at = beijing_now()
        await session.commit()

    async def get_bot(
        self, session: AsyncSession, user: User, bot_qq: str
    ) -> BotAccount:
        bot = await session.scalar(
            self._scope(
                select(BotAccount).where(
                    BotAccount.bot_qq == bot_qq,
                    BotAccount.is_deleted.is_(False),
                ),
                user,
            )
        )
        if bot is None:
            raise BusinessError("机器人账号不存在或无权访问", 40430)
        return bot

    @staticmethod
    async def get_configured_admin_qq(
        session: AsyncSession, bot_qq: str
    ) -> str:
        """读取机器人配置中的群管理命令管理员 QQ。"""
        bot = await session.scalar(
            select(BotAccount).where(
                BotAccount.bot_qq == str(bot_qq or ""),
                BotAccount.is_deleted.is_(False),
            )
        )
        return str(bot.admin_qq or "") if bot is not None else ""

    @staticmethod
    async def is_group_allowed(
        session: AsyncSession, bot_qq: str, group_id: str
    ) -> bool:
        """检查群开关；未配置群记录时按默认开启处理。"""
        bot = await session.scalar(
            select(BotAccount).where(
                BotAccount.bot_qq == bot_qq,
                BotAccount.is_deleted.is_(False),
            )
        )
        if bot is None:
            return True
        if not bot.is_enabled:
            return False
        if bot.group_mode != "allowlist":
            return True
        configured = await BotService._configured_group_enabled(
            session, bot_qq, group_id
        )
        return configured is None or configured

    @staticmethod
    async def _configured_group_enabled(
        session: AsyncSession, bot_qq: str, group_id: str
    ) -> bool | None:
        """返回群记录的开关；不存在记录时返回 None。"""
        return await session.scalar(
            select(BotGroup.is_enabled).where(
                BotGroup.bot_qq == bot_qq,
                BotGroup.group_id == str(group_id),
                BotGroup.is_deleted.is_(False),
            )
        )

    @staticmethod
    async def event_permission(
        session: AsyncSession, event: dict[str, Any]
    ) -> dict[str, Any]:
        """返回入站事件的机器人开关、群开关和最终放行状态。"""
        bot_qq = str(event.get("self_id") or "")
        if not bot_qq:
            return {
                "allowed": True,
                "bot_qq": "",
                "bot_exists": False,
                "bot_enabled": None,
                "group_id": None,
                "group_mode": None,
                "group_checked": False,
                "group_enabled": None,
                "group_configured": False,
            }
        bot = await session.scalar(
            select(BotAccount).where(
                BotAccount.bot_qq == bot_qq,
                BotAccount.is_deleted.is_(False),
            )
        )
        if bot is None:
            return {
                "allowed": True,
                "bot_qq": bot_qq,
                "bot_exists": False,
                "bot_enabled": None,
                "group_id": event.get("group_id"),
                "group_mode": None,
                "group_checked": False,
                "group_enabled": None,
                "group_configured": False,
            }
        group_id = event.get("group_id")
        group_mode = bot.group_mode or "all"
        if not bot.is_enabled:
            return {
                "allowed": False,
                "bot_qq": bot_qq,
                "bot_exists": True,
                "bot_enabled": False,
                "group_id": group_id,
                "group_mode": group_mode,
                "group_checked": False,
                "group_enabled": None,
                "group_configured": False,
            }
        if group_id is None:
            return {
                "allowed": True,
                "bot_qq": bot_qq,
                "bot_exists": True,
                "bot_enabled": True,
                "group_id": None,
                "group_mode": group_mode,
                "group_checked": False,
                "group_enabled": None,
                "group_configured": False,
            }
        if group_mode != "allowlist":
            return {
                "allowed": True,
                "bot_qq": bot_qq,
                "bot_exists": True,
                "bot_enabled": True,
                "group_id": str(group_id),
                "group_mode": group_mode,
                "group_checked": True,
                "group_enabled": True,
                "group_configured": False,
            }
        configured_group_enabled = await BotService._configured_group_enabled(
            session, bot_qq, str(group_id)
        )
        group_enabled = (
            True if configured_group_enabled is None else configured_group_enabled
        )
        return {
            "allowed": group_enabled,
            "bot_qq": bot_qq,
            "bot_exists": True,
            "bot_enabled": True,
            "group_id": str(group_id),
            "group_mode": group_mode,
            "group_checked": True,
            "group_enabled": group_enabled,
            "group_configured": configured_group_enabled is not None,
        }

    @staticmethod
    async def is_event_allowed(
        session: AsyncSession, event: dict[str, Any]
    ) -> bool:
        """Apply a bot's enabled state and group allowlist to inbound events."""
        permission = await BotService.event_permission(session, event)
        return bool(permission["allowed"])

    @classmethod
    async def ensure_group_allowed(
        cls, session: AsyncSession, bot_qq: str, group_id: str
    ) -> None:
        if not await cls.is_group_allowed(session, bot_qq, str(group_id)):
            raise BusinessError("该群未在机器人白名单中启用", 40331)

    @staticmethod
    def _validate_connection(payload: BotCreateRequest) -> None:
        if payload.connection_mode == "http" and not payload.http_url:
            raise BusinessError("HTTP 模式必须配置 NapCat HTTP 地址", 40037)

    @staticmethod
    def serialize_bot(
        bot: BotAccount,
        connected: bool,
        owner_names: dict[int, str] | None = None,
    ) -> dict:
        owner_names = owner_names or {}
        return {
            "id": bot.id,
            "owner_id": bot.owner_id,
            "owner_username": owner_names.get(bot.owner_id, ""),
            "bot_qq": bot.bot_qq,
            "nickname": bot.nickname,
            "admin_qq": bot.admin_qq or "",
            "connection_mode": bot.connection_mode or "auto",
            "http_url": bot.http_url or "",
            "has_access_token": bool(bot.access_token),
            "group_mode": bot.group_mode,
            "is_enabled": bot.is_enabled,
            "welcome_enabled": bot.welcome_enabled,
            "auto_approve_friend": bool(bot.auto_approve_friend),
            "auto_approve_group_request": bool(bot.auto_approve_group_request),
            "connected": connected,
            "created_at": format_beijing(bot.created_at),
            "updated_at": format_beijing(bot.updated_at),
        }

    @staticmethod
    def serialize_group(group: BotGroup) -> dict:
        return {
            "id": group.id,
            "bot_qq": group.bot_qq,
            "group_id": group.group_id,
            "group_name": group.group_name,
            "is_enabled": group.is_enabled,
            "created_at": format_beijing(group.created_at),
            "updated_at": format_beijing(group.updated_at),
        }
