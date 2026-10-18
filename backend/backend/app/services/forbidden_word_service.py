"""
违禁词规则管理与消息匹配服务。

功能：
1. 按机器人和群维护一条逗号分隔的违禁词列表；
2. 兼容历史逐词记录并逐词匹配；
3. 为 OneBot 群消息选择最具体、最长的命中规则。
"""

import logging
import re
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.permissions import is_admin, owner_scope
from app.common.responses import BusinessError
from app.common.time_utils import beijing_now, format_beijing
from app.models.entities import BotAccount, ForbiddenWordRule, User
from app.schemas import ForbiddenWordCreateRequest, ForbiddenWordUpdateRequest
from app.services.auto_reply_service import AutoReplyService


logger = logging.getLogger("uvicorn.error")


class ForbiddenWordService:
    """管理违禁词规则并匹配需要禁言的群消息。"""

    @staticmethod
    def _scope(query: Select, user: User) -> Select:
        """为普通用户追加规则所属用户过滤。"""
        owner_id = owner_scope(user)
        if owner_id is not None:
            query = query.where(ForbiddenWordRule.owner_id == owner_id)
        return query

    async def list_rules(
        self,
        session: AsyncSession,
        user: User,
        page: int,
        page_size: int,
        keyword: str = "",
        bot_qq: str = "",
        group_id: str = "",
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        """分页查询当前用户有权访问的违禁词规则。"""
        conditions = [ForbiddenWordRule.is_deleted.is_(False)]
        if keyword:
            conditions.append(
                or_(
                    ForbiddenWordRule.keyword.contains(keyword, autoescape=True),
                    ForbiddenWordRule.keywords.contains(keyword, autoescape=True),
                )
            )
        if bot_qq:
            conditions.append(ForbiddenWordRule.bot_qq == bot_qq)
        if group_id:
            conditions.append(ForbiddenWordRule.group_id == group_id)
        if enabled is not None:
            conditions.append(ForbiddenWordRule.is_enabled.is_(enabled))
        base_query = self._scope(select(ForbiddenWordRule).where(*conditions), user)
        count_query = self._scope(
            select(func.count(ForbiddenWordRule.id)).where(*conditions), user
        )
        total = int(await session.scalar(count_query) or 0)
        rows = (
            await session.scalars(
                base_query.order_by(ForbiddenWordRule.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        owner_names = await self._owner_names(session, {item.owner_id for item in rows})
        return {
            "items": [self.serialize(item, owner_names) for item in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    async def create_rules(
        self,
        session: AsyncSession,
        user: User,
        payload: ForbiddenWordCreateRequest,
    ) -> dict[str, Any]:
        """为一个机器人和群范围保存一条逗号分隔的违禁词列表。"""
        bot = await self._ensure_bot_access(session, user, payload.bot_qq)
        group_id = self.normalize_group_id(payload.group_id)
        keywords = self.parse_keywords(payload.keywords)
        keyword_text = self.join_keywords(keywords)
        active = await self._find_scope(session, payload.bot_qq, group_id, active_only=True)
        if active:
            raise BusinessError("该机器人和群范围已有违禁词列表，请直接编辑原记录", 40050)
        existing = await self._find_scope(
            session, payload.bot_qq, group_id, active_only=False
        )
        if existing is None:
            existing = ForbiddenWordRule(
                owner_id=bot.owner_id,
                bot_qq=payload.bot_qq,
                group_id=group_id,
                keyword=keywords[0],
                keywords=keyword_text,
                mute_prompt=payload.mute_prompt,
                mute_duration=payload.mute_duration,
                mute_enabled=payload.mute_enabled,
                is_enabled=payload.is_enabled,
            )
            session.add(existing)
        else:
            existing.owner_id = bot.owner_id
            existing.keywords = keyword_text
            existing.mute_prompt = payload.mute_prompt
            existing.mute_duration = payload.mute_duration
            existing.mute_enabled = payload.mute_enabled
            existing.is_enabled = payload.is_enabled
            existing.is_deleted = False
            existing.updated_at = beijing_now()
        await session.commit()
        owner_names = {bot.owner_id: user.username}
        await session.refresh(existing)
        return self.serialize(existing, owner_names)

    async def update_rule(
        self,
        session: AsyncSession,
        user: User,
        rule_id: int,
        payload: ForbiddenWordUpdateRequest,
    ) -> dict[str, Any]:
        """修改单条逗号分隔违禁词列表规则。"""
        rule = await self._get_active(session, user, rule_id)
        bot = await self._ensure_bot_access(session, user, payload.bot_qq)
        group_id = self.normalize_group_id(payload.group_id)
        keywords = self.parse_keywords(payload.keyword)
        keyword_text = self.join_keywords(keywords)
        active = await self._find_scope(
            session, payload.bot_qq, group_id, active_only=True, exclude_id=rule.id
        )
        if active:
            raise BusinessError("该机器人和群范围已有其他违禁词列表", 40051)
        rule.owner_id = bot.owner_id
        rule.bot_qq = payload.bot_qq
        rule.group_id = group_id
        rule.keywords = keyword_text
        rule.mute_prompt = payload.mute_prompt
        rule.mute_duration = payload.mute_duration
        rule.mute_enabled = payload.mute_enabled
        rule.is_enabled = payload.is_enabled
        rule.updated_at = beijing_now()
        await session.commit()
        owner_name = await session.scalar(select(User.username).where(User.id == rule.owner_id))
        return self.serialize(rule, {rule.owner_id: owner_name or user.username})

    async def soft_delete(
        self, session: AsyncSession, user: User, rule_id: int
    ) -> None:
        """软删除一条违禁词，不物理删除历史数据。"""
        rule = await self._get_active(session, user, rule_id)
        rule.is_deleted = True
        rule.updated_at = beijing_now()
        await session.commit()

    async def match_event(
        self, session: AsyncSession, event: dict[str, Any]
    ) -> ForbiddenWordRule | None:
        """匹配群消息，群级规则优先于机器人全局规则。"""
        if event.get("post_type") != "message" or event.get("message_type") != "group":
            return None
        bot_qq = str(event.get("self_id") or "")
        sender = event.get("sender")
        sender = sender if isinstance(sender, dict) else {}
        user_id = str(event.get("user_id") or sender.get("user_id") or "")
        group_id = str(event.get("group_id") or "")
        if not bot_qq or not user_id or user_id == bot_qq or not group_id:
            return None
        message_text = AutoReplyService.extract_message_text(event)
        if not message_text:
            return None
        rules = (
            await session.scalars(
                select(ForbiddenWordRule).where(
                    ForbiddenWordRule.bot_qq == bot_qq,
                    ForbiddenWordRule.group_id.in_(("", group_id)),
                    ForbiddenWordRule.is_enabled.is_(True),
                    ForbiddenWordRule.is_deleted.is_(False),
                )
            )
        ).all()
        matching: list[tuple[ForbiddenWordRule, int]] = []
        for rule in rules:
            try:
                rule_keywords = self.parse_keywords(rule.keywords or rule.keyword)
            except BusinessError:
                logger.warning(
                    "违禁词列表格式无效，跳过规则，规则=%s，机器人=%s",
                    rule.id,
                    bot_qq,
                )
                continue
            for keyword in rule_keywords:
                if keyword.casefold() in message_text.casefold():
                    matching.append((rule, len(keyword)))
                    break
        if not matching:
            return None
        return sorted(
            matching,
            key=lambda item: (
                item[0].group_id == group_id,
                item[1],
                item[0].id,
            ),
            reverse=True,
        )[0][0]

    async def prompt_rule_for_event(
        self, session: AsyncSession, event: dict[str, Any]
    ) -> ForbiddenWordRule | None:
        """返回当前消息适用的 AI 禁言提示词规则。"""
        if event.get("post_type") != "message" or event.get("message_type") != "group":
            return None
        bot_qq = str(event.get("self_id") or "")
        sender = event.get("sender")
        sender = sender if isinstance(sender, dict) else {}
        user_id = str(event.get("user_id") or sender.get("user_id") or "")
        group_id = str(event.get("group_id") or "")
        if not bot_qq or not user_id or user_id == bot_qq or not group_id:
            return None
        rules = (
            await session.scalars(
                select(ForbiddenWordRule).where(
                    ForbiddenWordRule.bot_qq == bot_qq,
                    ForbiddenWordRule.group_id.in_(("", group_id)),
                    ForbiddenWordRule.is_enabled.is_(True),
                    ForbiddenWordRule.is_deleted.is_(False),
                )
            )
        ).all()
        candidates = [rule for rule in rules if (rule.mute_prompt or "").strip()]
        if not candidates:
            return None
        return sorted(
            candidates,
            key=lambda rule: (rule.group_id == group_id, rule.id),
            reverse=True,
        )[0]

    async def learn_keyword(
        self, session: AsyncSession, rule_id: int, keyword: str
    ) -> bool:
        """将 AI 语义判定命中的违规关键词并入指定规则的违禁词列表。

        用于「AI 判定禁言 → 落库本地」流程：命中一次后同样的连续违规文字
        将由本地关键词直接匹配，无需再次调用 AI。操作幂等，已存在则跳过。

        Args:
            session: 数据库会话。
            rule_id: 命中并需要学习的违禁词规则 ID。
            keyword: AI 返回且调用方已确认为消息内连续文字的违规关键词。
        Returns:
            True 表示新增成功；关键词为空、超长、规则不存在或已存在时返回 False。
        """
        text = str(keyword or "").strip()
        if not text or len(text) > 100:
            return False
        rule = await session.scalar(
            select(ForbiddenWordRule).where(
                ForbiddenWordRule.id == rule_id,
                ForbiddenWordRule.is_deleted.is_(False),
            )
        )
        if rule is None:
            return False
        try:
            existing = self.parse_keywords(rule.keywords or rule.keyword)
        except BusinessError:
            existing = []
        if any(item.casefold() == text.casefold() for item in existing):
            return False
        existing.append(text)
        rule.keywords = self.join_keywords(existing)
        rule.updated_at = beijing_now()
        await session.commit()
        return True

    @staticmethod
    def parse_keywords(value: str) -> list[str]:
        """解析英文逗号分隔关键词并去重，兼容中文逗号。"""
        values = [item.strip() for item in re.split(r"[,，]", value) if item.strip()]
        unique: list[str] = []
        seen: set[str] = set()
        for item in values:
            if len(item) > 100:
                raise BusinessError("单个违禁词不能超过 100 个字符", 40052)
            key = item.casefold()
            if key not in seen:
                seen.add(key)
                unique.append(item)
        if not unique:
            raise BusinessError("请至少填写一个违禁词", 40053)
        return unique

    @staticmethod
    def join_keywords(keywords: list[str]) -> str:
        """将已校验去重的关键词保存为一条英文逗号分隔字符串。"""
        return ",".join(keywords)

    @staticmethod
    def normalize_group_id(group_id: str | None) -> str:
        """将空群号统一为机器人全局规则。"""
        return str(group_id or "").strip()

    async def _ensure_bot_access(
        self, session: AsyncSession, user: User, bot_qq: str
    ) -> BotAccount:
        """校验机器人存在并属于当前用户或当前用户是管理员。"""
        bot = await session.scalar(
            select(BotAccount).where(
                BotAccount.bot_qq == bot_qq,
                BotAccount.is_deleted.is_(False),
            )
        )
        if bot is None:
            raise BusinessError("请先在机器人管理中添加该 QQ", 40430)
        if not is_admin(user) and bot.owner_id != user.id:
            raise BusinessError("无权为该机器人配置违禁词", 40335)
        return bot

    async def _get_active(
        self, session: AsyncSession, user: User, rule_id: int
    ) -> ForbiddenWordRule:
        """读取当前用户有权访问的未删除规则。"""
        rule = await session.scalar(
            self._scope(
                select(ForbiddenWordRule).where(
                    ForbiddenWordRule.id == rule_id,
                    ForbiddenWordRule.is_deleted.is_(False),
                ),
                user,
            )
        )
        if rule is None:
            raise BusinessError("违禁词规则不存在或无权访问", 40450)
        return rule

    async def soft_delete_many(
        self, session: AsyncSession, user: User, rule_ids: list[int]
    ) -> int:
        """按勾选 ID 批量软删除违禁词规则，不物理删除数据。"""
        ids = sorted({int(item) for item in rule_ids})
        if not ids:
            raise BusinessError("请至少勾选一条违禁词规则", 40055)
        rules = (
            await session.scalars(
                self._scope(
                    select(ForbiddenWordRule).where(
                        ForbiddenWordRule.id.in_(ids),
                        ForbiddenWordRule.is_deleted.is_(False),
                    ),
                    user,
                )
            )
        ).all()
        if len(rules) != len(ids):
            raise BusinessError("部分违禁词规则不存在或无权访问", 40450)
        now = beijing_now()
        for rule in rules:
            rule.is_deleted = True
            rule.updated_at = now
        await session.commit()
        return len(rules)

    @staticmethod
    async def _find_scope(
        session: AsyncSession,
        bot_qq: str,
        group_id: str,
        active_only: bool,
        exclude_id: int | None = None,
    ) -> ForbiddenWordRule | None:
        """查询同一机器人和群范围的一条规则，优先返回活动记录。"""
        conditions = [
            ForbiddenWordRule.bot_qq == bot_qq,
            ForbiddenWordRule.group_id == group_id,
        ]
        if active_only:
            conditions.append(ForbiddenWordRule.is_deleted.is_(False))
        if exclude_id is not None:
            conditions.append(ForbiddenWordRule.id != exclude_id)
        return await session.scalar(
            select(ForbiddenWordRule)
            .where(*conditions)
            .order_by(ForbiddenWordRule.is_deleted.asc(), ForbiddenWordRule.id.desc())
        )

    @staticmethod
    async def _owner_names(
        session: AsyncSession, owner_ids: set[int]
    ) -> dict[int, str]:
        """批量读取规则所属用户名。"""
        if not owner_ids:
            return {}
        rows = await session.execute(select(User.id, User.username).where(User.id.in_(owner_ids)))
        return {row.id: row.username for row in rows}

    @staticmethod
    def serialize(
        rule: ForbiddenWordRule, owner_names: dict[int, str]
    ) -> dict[str, Any]:
        """将违禁词规则转换为统一接口响应。"""
        return {
            "id": rule.id,
            "owner_id": rule.owner_id,
            "owner_username": owner_names.get(rule.owner_id, "未知用户"),
            "bot_qq": rule.bot_qq,
            "group_id": rule.group_id,
            "keyword": rule.keywords or rule.keyword,
            "mute_prompt": rule.mute_prompt or "",
            "mute_duration": rule.mute_duration,
            "mute_enabled": rule.mute_enabled if rule.mute_enabled is not None else True,
            "is_enabled": rule.is_enabled,
            "created_at": format_beijing(rule.created_at),
            "updated_at": format_beijing(rule.updated_at),
        }
