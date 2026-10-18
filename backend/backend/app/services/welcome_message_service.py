"""进群欢迎词的权限隔离、CRUD 和运行时群规则匹配。"""

from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.permissions import is_admin, owner_scope
from app.common.responses import BusinessError
from app.common.time_utils import beijing_now, format_beijing
from app.models.entities import BotAccount, User, WelcomeMessageRule
from app.schemas import WelcomeMessageCreateRequest, WelcomeMessageUpdateRequest


class WelcomeMessageService:
    """管理机器人和群维度的欢迎词配置。"""

    @staticmethod
    def _scope(query: Select, user: User) -> Select:
        owner_id = owner_scope(user)
        if owner_id is not None:
            query = query.where(WelcomeMessageRule.owner_id == owner_id)
        return query

    async def list_rules(
        self,
        session: AsyncSession,
        user: User,
        page: int,
        page_size: int,
        bot_qq: str = "",
        group_id: str = "",
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        """按当前用户权限分页返回欢迎词。"""
        conditions = [WelcomeMessageRule.is_deleted.is_(False)]
        if bot_qq:
            conditions.append(WelcomeMessageRule.bot_qq == bot_qq)
        if group_id:
            conditions.append(WelcomeMessageRule.group_id == group_id)
        if enabled is not None:
            conditions.append(WelcomeMessageRule.is_enabled.is_(enabled))
        query = self._scope(select(WelcomeMessageRule).where(*conditions), user)
        count_query = self._scope(
            select(func.count(WelcomeMessageRule.id)).where(*conditions), user
        )
        total = int(await session.scalar(count_query) or 0)
        rules = (
            await session.scalars(
                query.order_by(WelcomeMessageRule.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        owners = await self._owner_names(session, {item.owner_id for item in rules})
        return {
            "items": [self.serialize(item, owners) for item in rules],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    async def create_rule(
        self,
        session: AsyncSession,
        user: User,
        payload: WelcomeMessageCreateRequest,
    ) -> dict[str, Any]:
        """新增一条机器人默认或指定群欢迎词。"""
        bot = await self._ensure_bot_access(session, user, payload.bot_qq)
        group_id = self.normalize_group_id(payload.group_id)
        existing = await self._find_same(session, payload.bot_qq, group_id)
        if existing is not None:
            if not existing.is_deleted:
                raise BusinessError("该机器人和群已存在进群欢迎词", 40042)
            existing.owner_id = bot.owner_id
            existing.welcome_content = payload.welcome_content
            existing.is_enabled = payload.is_enabled
            existing.is_deleted = False
            existing.updated_at = beijing_now()
            await session.commit()
            await session.refresh(existing)
            return self.serialize(existing, {existing.owner_id: user.username})
        rule = WelcomeMessageRule(
            owner_id=bot.owner_id,
            bot_qq=payload.bot_qq,
            group_id=group_id,
            welcome_content=payload.welcome_content,
            is_enabled=payload.is_enabled,
        )
        session.add(rule)
        await session.commit()
        await session.refresh(rule)
        return self.serialize(rule, {rule.owner_id: user.username})

    async def update_rule(
        self,
        session: AsyncSession,
        user: User,
        rule_id: int,
        payload: WelcomeMessageUpdateRequest,
    ) -> dict[str, Any]:
        """修改有权访问的欢迎词并检查机器人群配置冲突。"""
        rule = await self._get_active(session, user, rule_id)
        bot = await self._ensure_bot_access(session, user, payload.bot_qq)
        group_id = self.normalize_group_id(payload.group_id)
        existing = await self._find_same(session, payload.bot_qq, group_id)
        if existing is not None and existing.id != rule.id:
            if not existing.is_deleted:
                raise BusinessError("该机器人和群已存在进群欢迎词", 40042)
            existing.owner_id = bot.owner_id
            existing.welcome_content = payload.welcome_content
            existing.is_enabled = payload.is_enabled
            existing.is_deleted = False
            rule.is_deleted = True
            rule.updated_at = beijing_now()
            await session.commit()
            await session.refresh(existing)
            return self.serialize(existing, {existing.owner_id: user.username})
        rule.owner_id = bot.owner_id
        rule.bot_qq = payload.bot_qq
        rule.group_id = group_id
        rule.welcome_content = payload.welcome_content
        rule.is_enabled = payload.is_enabled
        rule.updated_at = beijing_now()
        await session.commit()
        owner_name = await session.scalar(select(User.username).where(User.id == rule.owner_id))
        return self.serialize(rule, {rule.owner_id: owner_name or "未知用户"})

    async def soft_delete(
        self, session: AsyncSession, user: User, rule_id: int
    ) -> None:
        """软删除一条欢迎词。"""
        rule = await self._get_active(session, user, rule_id)
        rule.is_deleted = True
        rule.updated_at = beijing_now()
        await session.commit()

    async def resolve_event(
        self, session: AsyncSession, event: dict[str, Any]
    ) -> tuple[bool, WelcomeMessageRule | None]:
        """返回事件机器人是否开启欢迎词及其精确匹配规则。"""
        if (
            event.get("post_type") != "notice"
            or event.get("notice_type") != "group_increase"
            or event.get("group_id") is None
        ):
            return False, None
        bot_qq = str(event.get("self_id") or "")
        if not bot_qq:
            return False, None
        bot = await session.scalar(
            select(BotAccount).where(
                BotAccount.bot_qq == bot_qq,
                BotAccount.is_deleted.is_(False),
            )
        )
        if bot is None or not bot.is_enabled or not bot.welcome_enabled:
            return False, None
        group_id = str(event["group_id"])
        exact = await session.scalar(
            select(WelcomeMessageRule).where(
                WelcomeMessageRule.bot_qq == bot_qq,
                WelcomeMessageRule.group_id == group_id,
                WelcomeMessageRule.is_deleted.is_(False),
            )
        )
        rule = exact
        if rule is None:
            rule = await session.scalar(
                select(WelcomeMessageRule).where(
                    WelcomeMessageRule.bot_qq == bot_qq,
                    WelcomeMessageRule.group_id == "",
                    WelcomeMessageRule.is_deleted.is_(False),
                )
            )
        return True, rule if rule is not None and rule.is_enabled else None

    @staticmethod
    def normalize_group_id(group_id: str | None) -> str:
        """将空群号统一为默认规则的空字符串。"""
        return str(group_id or "").strip()

    async def _ensure_bot_access(
        self, session: AsyncSession, user: User, bot_qq: str
    ) -> BotAccount:
        bot = await session.scalar(
            select(BotAccount).where(
                BotAccount.bot_qq == bot_qq,
                BotAccount.is_deleted.is_(False),
            )
        )
        if bot is None:
            raise BusinessError("请先在机器人管理中添加该 QQ", 40430)
        if not is_admin(user) and bot.owner_id != user.id:
            raise BusinessError("无权为该机器人配置进群欢迎词", 40334)
        return bot

    async def _get_active(
        self, session: AsyncSession, user: User, rule_id: int
    ) -> WelcomeMessageRule:
        rule = await session.scalar(
            self._scope(
                select(WelcomeMessageRule).where(
                    WelcomeMessageRule.id == rule_id,
                    WelcomeMessageRule.is_deleted.is_(False),
                ),
                user,
            )
        )
        if rule is None:
            raise BusinessError("进群欢迎词不存在或无权访问", 40441)
        return rule

    async def _find_same(
        self,
        session: AsyncSession,
        bot_qq: str,
        group_id: str,
    ) -> WelcomeMessageRule | None:
        return await session.scalar(
            select(WelcomeMessageRule).where(
                WelcomeMessageRule.bot_qq == bot_qq,
                WelcomeMessageRule.group_id == group_id,
            ).order_by(
                WelcomeMessageRule.is_deleted.asc(),
                WelcomeMessageRule.id.desc(),
            )
        )

    @staticmethod
    async def _owner_names(
        session: AsyncSession, owner_ids: set[int]
    ) -> dict[int, str]:
        if not owner_ids:
            return {}
        rows = await session.execute(select(User.id, User.username).where(User.id.in_(owner_ids)))
        return {row.id: row.username for row in rows}

    @staticmethod
    def serialize(
        rule: WelcomeMessageRule, owner_names: dict[int, str]
    ) -> dict[str, Any]:
        """序列化欢迎词并将空群号展示为默认规则标识。"""
        return {
            "id": rule.id,
            "owner_id": rule.owner_id,
            "owner_username": owner_names.get(rule.owner_id, "未知用户"),
            "bot_qq": rule.bot_qq,
            "group_id": rule.group_id,
            "welcome_content": rule.welcome_content,
            "is_enabled": rule.is_enabled,
            "created_at": format_beijing(rule.created_at),
            "updated_at": format_beijing(rule.updated_at),
        }
