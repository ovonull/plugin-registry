"""
AI 触发关键词规则管理与消息匹配服务。

功能：
1. 每个机器人维护一条英文逗号分隔的关键词列表；
2. 支持历史记录复用和批量软删除；
3. 按当前用户权限分页查询，并为 OneBot 消息返回命中的关键词。
"""

import logging
import re
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.permissions import is_admin, owner_scope
from app.common.responses import BusinessError
from app.common.time_utils import beijing_now, format_beijing
from app.models.entities import AiTriggerKeywordRule, BotAccount, User
from app.schemas import AiTriggerKeywordCreateRequest, AiTriggerKeywordUpdateRequest


logger = logging.getLogger("uvicorn.error")


class AiTriggerKeywordService:
    """管理每个机器人唯一的 AI 触发关键词列表。"""

    @staticmethod
    def _scope(query: Select, user: User) -> Select:
        """为普通用户追加规则所属用户过滤。"""
        owner_id = owner_scope(user)
        if owner_id is not None:
            query = query.where(AiTriggerKeywordRule.owner_id == owner_id)
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
    ) -> dict[str, Any]:
        """分页查询当前用户有权访问的 AI 触发关键词规则。"""
        conditions = [AiTriggerKeywordRule.is_deleted.is_(False)]
        if keyword:
            conditions.append(
                or_(
                    AiTriggerKeywordRule.keyword.contains(keyword, autoescape=True),
                    AiTriggerKeywordRule.keywords.contains(keyword, autoescape=True),
                )
            )
        if bot_qq:
            conditions.append(AiTriggerKeywordRule.bot_qq == bot_qq)
        if enabled is not None:
            conditions.append(AiTriggerKeywordRule.is_enabled.is_(enabled))
        base_query = self._scope(select(AiTriggerKeywordRule).where(*conditions), user)
        count_query = self._scope(
            select(func.count(AiTriggerKeywordRule.id)).where(*conditions), user
        )
        total = int(await session.scalar(count_query) or 0)
        rows = (
            await session.scalars(
                base_query.order_by(AiTriggerKeywordRule.id.desc())
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

    async def create_rule(
        self,
        session: AsyncSession,
        user: User,
        payload: AiTriggerKeywordCreateRequest,
    ) -> dict[str, Any]:
        """为机器人保存唯一的一条逗号分隔关键词列表。"""
        bot = await self._ensure_bot_access(session, user, payload.bot_qq)
        keyword_text = self.join_keywords(self.parse_keywords(payload.keywords))
        active = await self._find_by_bot(session, payload.bot_qq, include_deleted=False)
        if active is not None:
            raise BusinessError("该机器人已有 AI 触发关键词列表，请直接编辑原记录", 40064)
        rule = await self._find_by_bot(session, payload.bot_qq, include_deleted=True)
        if rule is None:
            rule = AiTriggerKeywordRule(
                owner_id=bot.owner_id,
                bot_qq=payload.bot_qq,
                keyword=keyword_text.split(",", 1)[0],
                keywords=keyword_text,
                is_enabled=payload.is_enabled,
                reply_all=payload.reply_all,
                random_reply=payload.random_reply,
                random_reply_probability=payload.random_reply_probability,
            )
            session.add(rule)
        else:
            rule.owner_id = bot.owner_id
            rule.keyword = keyword_text.split(",", 1)[0]
            rule.keywords = keyword_text
            rule.is_enabled = payload.is_enabled
            rule.reply_all = payload.reply_all
            rule.random_reply = payload.random_reply
            rule.random_reply_probability = payload.random_reply_probability
            rule.is_deleted = False
            rule.updated_at = beijing_now()
        await session.commit()
        await session.refresh(rule)
        owner_name = await session.scalar(
            select(User.username).where(User.id == bot.owner_id)
        )
        return self.serialize(rule, {bot.owner_id: owner_name or user.username})

    async def update_rule(
        self,
        session: AsyncSession,
        user: User,
        rule_id: int,
        payload: AiTriggerKeywordUpdateRequest,
    ) -> dict[str, Any]:
        """修改一条 AI 触发关键词列表。"""
        rule = await self._get_active(session, user, rule_id)
        bot = await self._ensure_bot_access(session, user, payload.bot_qq)
        keyword_text = self.join_keywords(self.parse_keywords(payload.keyword))
        other = await self._find_by_bot(session, payload.bot_qq, include_deleted=True)
        if other is not None and other.id != rule.id:
            raise BusinessError("该机器人已有 AI 触发关键词列表，请直接编辑原记录", 40060)
        rule.owner_id = bot.owner_id
        rule.bot_qq = payload.bot_qq
        rule.keyword = keyword_text.split(",", 1)[0]
        rule.keywords = keyword_text
        rule.is_enabled = payload.is_enabled
        rule.reply_all = payload.reply_all
        rule.random_reply = payload.random_reply
        rule.random_reply_probability = payload.random_reply_probability
        rule.updated_at = beijing_now()
        await session.commit()
        owner_name = await session.scalar(select(User.username).where(User.id == rule.owner_id))
        return self.serialize(rule, {rule.owner_id: owner_name or user.username})

    async def soft_delete(self, session: AsyncSession, user: User, rule_id: int) -> None:
        """软删除一条 AI 触发关键词规则。"""
        rule = await self._get_active(session, user, rule_id)
        rule.is_deleted = True
        rule.updated_at = beijing_now()
        await session.commit()

    async def soft_delete_many(
        self, session: AsyncSession, user: User, rule_ids: list[int]
    ) -> int:
        """按勾选 ID 批量软删除 AI 触发关键词规则。"""
        ids = sorted({int(item) for item in rule_ids})
        if not ids:
            raise BusinessError("请至少勾选一条 AI 触发关键词规则", 40061)
        rows = (
            await session.scalars(
                self._scope(
                    select(AiTriggerKeywordRule).where(
                        AiTriggerKeywordRule.id.in_(ids),
                        AiTriggerKeywordRule.is_deleted.is_(False),
                    ),
                    user,
                )
            )
        ).all()
        if len(rows) != len(ids):
            raise BusinessError("部分 AI 触发关键词规则不存在或无权访问", 40460)
        now = beijing_now()
        for row in rows:
            row.is_deleted = True
            row.updated_at = now
        await session.commit()
        return len(rows)

    async def match_event(
        self, session: AsyncSession, event: dict[str, Any]
    ) -> str | None:
        """从群消息文本中匹配当前机器人的 AI 触发关键词。"""
        if event.get("post_type") != "message" or event.get("message_type") != "group":
            return None
        bot_qq = str(event.get("self_id") or "")
        if not bot_qq:
            return None
        text = self._extract_text(event)
        if not text:
            return None
        rule = await session.scalar(
            select(AiTriggerKeywordRule).where(
                AiTriggerKeywordRule.bot_qq == bot_qq,
                AiTriggerKeywordRule.is_enabled.is_(True),
                AiTriggerKeywordRule.is_deleted.is_(False),
            )
        )
        if rule is None:
            return None
        for keyword in self.parse_keywords(rule.keywords or rule.keyword):
            if keyword.casefold() in text.casefold():
                return keyword
        return None

    async def is_reply_all_enabled(
        self, session: AsyncSession, event: dict[str, Any]
    ) -> bool:
        """判断当前机器人是否开启群消息全部触发。"""
        if event.get("post_type") != "message" or event.get("message_type") != "group":
            return False
        bot_qq = str(event.get("self_id") or "")
        if not bot_qq:
            return False
        return bool(
            await session.scalar(
                select(AiTriggerKeywordRule.id).where(
                    AiTriggerKeywordRule.bot_qq == bot_qq,
                    AiTriggerKeywordRule.is_enabled.is_(True),
                    AiTriggerKeywordRule.is_deleted.is_(False),
                    AiTriggerKeywordRule.reply_all.is_(True),
                )
            )
        )

    async def get_reply_mode(
        self, session: AsyncSession, event: dict[str, Any]
    ) -> dict[str, Any]:
        """读取当前机器人的 AI 自动回复模式及随机概率。"""
        if event.get("post_type") != "message" or event.get("message_type") != "group":
            return {"reply_all": False, "random_reply": False, "random_reply_probability": 50}
        bot_qq = str(event.get("self_id") or "")
        if not bot_qq:
            return {"reply_all": False, "random_reply": False, "random_reply_probability": 50}
        rule = await session.scalar(
            select(AiTriggerKeywordRule).where(
                AiTriggerKeywordRule.bot_qq == bot_qq,
                AiTriggerKeywordRule.is_enabled.is_(True),
                AiTriggerKeywordRule.is_deleted.is_(False),
            )
        )
        if rule is None:
            return {"reply_all": False, "random_reply": False, "random_reply_probability": 50}
        try:
            probability = max(
                0,
                min(
                    100,
                    int(
                        rule.random_reply_probability
                        if rule.random_reply_probability is not None
                        else 50
                    ),
                ),
            )
        except (TypeError, ValueError):
            probability = 50
        return {
            "reply_all": bool(rule.reply_all),
            "random_reply": bool(rule.random_reply) and not bool(rule.reply_all),
            "random_reply_probability": probability,
        }

    @staticmethod
    def parse_keywords(value: str) -> list[str]:
        """解析英文逗号分隔关键词并去重。"""
        values = [item.strip() for item in re.split(r"[,，]", value) if item.strip()]
        unique: list[str] = []
        seen: set[str] = set()
        for item in values:
            if len(item) > 100:
                raise BusinessError("单个 AI 触发关键词不能超过 100 个字符", 40062)
            key = item.casefold()
            if key not in seen:
                seen.add(key)
                unique.append(item)
        if not unique:
            raise BusinessError("请至少填写一个 AI 触发关键词", 40063)
        return unique

    @staticmethod
    def _extract_text(event: dict[str, Any]) -> str:
        """只提取文本消息段，避免图片、语音 CQ 参数误命中关键词。"""
        message = event.get("message")
        if isinstance(message, list):
            parts: list[str] = []
            for segment in message:
                if not isinstance(segment, dict) or segment.get("type") != "text":
                    continue
                data = segment.get("data")
                if isinstance(data, dict) and isinstance(data.get("text"), str):
                    parts.append(data["text"])
            return "".join(parts).strip()
        value = message if isinstance(message, str) else event.get("raw_message")
        if not isinstance(value, str):
            return ""
        return re.sub(r"\[CQ:[^\]]+\]", "", value).strip()

    @staticmethod
    def join_keywords(keywords: list[str]) -> str:
        """将关键词保存为一条英文逗号分隔字符串。"""
        return ",".join(keywords)

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
            raise BusinessError("无权为该机器人配置 AI 触发关键词", 40360)
        return bot

    async def _get_active(
        self, session: AsyncSession, user: User, rule_id: int
    ) -> AiTriggerKeywordRule:
        """读取当前用户有权访问的未删除规则。"""
        rule = await session.scalar(
            self._scope(
                select(AiTriggerKeywordRule).where(
                    AiTriggerKeywordRule.id == rule_id,
                    AiTriggerKeywordRule.is_deleted.is_(False),
                ),
                user,
            )
        )
        if rule is None:
            raise BusinessError("AI 触发关键词规则不存在或无权访问", 40460)
        return rule

    @staticmethod
    async def _find_by_bot(
        session: AsyncSession, bot_qq: str, include_deleted: bool
    ) -> AiTriggerKeywordRule | None:
        """查询机器人唯一规则，必要时包含历史软删除记录。"""
        conditions = [AiTriggerKeywordRule.bot_qq == bot_qq]
        if not include_deleted:
            conditions.append(AiTriggerKeywordRule.is_deleted.is_(False))
        return await session.scalar(
            select(AiTriggerKeywordRule)
            .where(*conditions)
            .order_by(AiTriggerKeywordRule.is_deleted.asc(), AiTriggerKeywordRule.id.desc())
        )

    @staticmethod
    async def _owner_names(session: AsyncSession, owner_ids: set[int]) -> dict[int, str]:
        """批量读取规则所属用户名。"""
        if not owner_ids:
            return {}
        rows = await session.execute(select(User.id, User.username).where(User.id.in_(owner_ids)))
        return {row.id: row.username for row in rows}

    @staticmethod
    def serialize(rule: AiTriggerKeywordRule, owner_names: dict[int, str]) -> dict[str, Any]:
        """将 AI 触发关键词规则转换为统一接口响应。"""
        return {
            "id": rule.id,
            "owner_id": rule.owner_id,
            "owner_username": owner_names.get(rule.owner_id, "未知用户"),
            "bot_qq": rule.bot_qq,
            "keyword": rule.keywords or rule.keyword,
            "is_enabled": rule.is_enabled,
            "reply_all": bool(rule.reply_all),
            "random_reply": bool(rule.random_reply),
            "random_reply_probability": int(
                rule.random_reply_probability
                if rule.random_reply_probability is not None
                else 50
            ),
            "created_at": format_beijing(rule.created_at),
            "updated_at": format_beijing(rule.updated_at),
        }
