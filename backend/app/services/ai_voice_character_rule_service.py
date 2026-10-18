"""AI 语音角色配置管理与运行时解析服务。

功能：
1. 按机器人和群维护角色配置；
2. 支持分页查询、创建、修改和软删除；
3. 为 OneBot AI 回复按群级、机器人默认级解析角色。
"""

from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.permissions import is_admin, owner_scope
from app.common.responses import BusinessError
from app.common.time_utils import beijing_now, format_beijing
from app.models.entities import AiVoiceCharacterRule, BotAccount, User
from app.schemas import AiVoiceCharacterCreateRequest, AiVoiceCharacterUpdateRequest


DEFAULT_CHARACTERS: tuple[dict[str, str], ...] = (
    {"character_id": "lucy-voice-laibixiaoxin", "character_name": "小新", "preview_url": "https://res.qpt.qq.com/qpilot/tts_sample/group/lucy-voice-laibixiaoxin.wav"},
    {"character_id": "lucy-voice-houge", "character_name": "猴哥", "preview_url": "https://res.qpt.qq.com/qpilot/tts_sample/group/lucy-voice-houge.wav"},
    {"character_id": "lucy-voice-silang", "character_name": "四郎", "preview_url": "https://res.qpt.qq.com/qpilot/tts_sample/group/lucy-voice-silang.wav"},
    {"character_id": "lucy-voice-guangdong-f1", "character_name": "东北老妹儿", "preview_url": "https://res.qpt.qq.com/qpilot/tts_sample/group/lucy-voice-guangdong-f1.wav"},
    {"character_id": "lucy-voice-guangxi-m1", "character_name": "广西大表哥", "preview_url": "https://res.qpt.qq.com/qpilot/tts_sample/group/lucy-voice-guangxi-m1.wav"},
    {"character_id": "lucy-voice-daji", "character_name": "妲己", "preview_url": "https://res.qpt.qq.com/qpilot/tts_sample/group/lucy-voice-daji.wav"},
    {"character_id": "lucy-voice-lizeyan", "character_name": "霸道总裁", "preview_url": "https://res.qpt.qq.com/qpilot/tts_sample/group/lucy-voice-lizeyan-2.wav"},
    {"character_id": "lucy-voice-suxinjiejie", "character_name": "酥心御姐", "preview_url": "https://res.qpt.qq.com/qpilot/tts_sample/group/lucy-voice-suxinjiejie.wav"},
    {"character_id": "lucy-voice-m8", "character_name": "说书先生", "preview_url": "https://res.qpt.qq.com/qpilot/tts_sample/group/lucy-voice-m8.wav"},
    {"character_id": "lucy-voice-male1", "character_name": "憨憨小弟", "preview_url": "https://res.qpt.qq.com/qpilot/tts_sample/group/lucy-voice-male1.wav"},
    {"character_id": "lucy-voice-male3", "character_name": "憨厚老哥", "preview_url": "https://res.qpt.qq.com/qpilot/tts_sample/group/lucy-voice-male3.wav"},
    {"character_id": "lucy-voice-lvbu", "character_name": "吕布", "preview_url": "https://res.qpt.qq.com/qpilot/tts_sample/group/lucy-voice-lvbu.wav"},
    {"character_id": "lucy-voice-xueling", "character_name": "元气少女", "preview_url": "https://res.qpt.qq.com/qpilot/tts_sample/group/lucy-voice-xueling.wav"},
    {"character_id": "lucy-voice-f37", "character_name": "文艺少女", "preview_url": "https://res.qpt.qq.com/qpilot/tts_sample/group/lucy-voice-f37.wav"},
    {"character_id": "lucy-voice-male2", "character_name": "磁性大叔", "preview_url": "https://res.qpt.qq.com/qpilot/tts_sample/group/lucy-voice-male2.wav"},
    {"character_id": "lucy-voice-female1", "character_name": "邻家小妹", "preview_url": "https://res.qpt.qq.com/qpilot/tts_sample/group/lucy-voice-female1.wav"},
    {"character_id": "lucy-voice-m14", "character_name": "低沉男声", "preview_url": "https://res.qpt.qq.com/qpilot/tts_sample/group/lucy-voice-m14.wav"},
    {"character_id": "lucy-voice-f38", "character_name": "傲娇少女", "preview_url": "https://res.qpt.qq.com/qpilot/tts_sample/group/lucy-voice-f38.wav"},
    {"character_id": "lucy-voice-m101", "character_name": "爹系男友", "preview_url": "https://res.qpt.qq.com/qpilot/tts_sample/group/lucy-voice-m101.wav"},
    {"character_id": "lucy-voice-female2", "character_name": "暖心姐姐", "preview_url": "https://res.qpt.qq.com/qpilot/tts_sample/group/lucy-voice-female2.wav"},
    {"character_id": "lucy-voice-f36", "character_name": "温柔妹妹", "preview_url": "https://res.qpt.qq.com/qpilot/tts_sample/group/lucy-voice-f36.wav"},
    {"character_id": "lucy-voice-f34", "character_name": "书香少女", "preview_url": "https://res.qpt.qq.com/qpilot/tts_sample/group/lucy-voice-f34.wav"},
)


class AiVoiceCharacterRuleService:
    """维护 AI 语音角色配置并提供 OneBot 运行时查询。"""

    @staticmethod
    def _scope(query: Select, user: User) -> Select:
        owner_id = owner_scope(user)
        return query if owner_id is None else query.where(AiVoiceCharacterRule.owner_id == owner_id)

    async def list_rules(self, session: AsyncSession, user: User, page: int, page_size: int, bot_qq: str = "", group_id: str = "", enabled: bool | None = None) -> dict[str, Any]:
        """分页查询角色配置。"""
        conditions = [AiVoiceCharacterRule.is_deleted.is_(False)]
        if bot_qq:
            conditions.append(AiVoiceCharacterRule.bot_qq == bot_qq)
        if group_id:
            conditions.append(AiVoiceCharacterRule.group_id == group_id)
        if enabled is not None:
            conditions.append(AiVoiceCharacterRule.is_enabled.is_(enabled))
        query = self._scope(select(AiVoiceCharacterRule).where(*conditions), user)
        total = int(await session.scalar(self._scope(select(func.count(AiVoiceCharacterRule.id)).where(*conditions), user)) or 0)
        rows = (await session.scalars(query.order_by(AiVoiceCharacterRule.id.desc()).offset((page - 1) * page_size).limit(page_size))).all()
        owner_ids = {row.owner_id for row in rows}
        owners = {row.id: row.username for row in (await session.scalars(select(User).where(User.id.in_(owner_ids)))).all()} if owner_ids else {}
        return {"items": [self.serialize(row, owners) for row in rows], "page": page, "page_size": page_size, "total": total, "pages": max(1, (total + page_size - 1) // page_size)}

    async def create_rule(self, session: AsyncSession, user: User, payload: AiVoiceCharacterCreateRequest) -> dict[str, Any]:
        """新增或恢复机器人、群范围的角色配置。"""
        bot = await self._ensure_bot_access(session, user, payload.bot_qq)
        group_id = self.normalize_group_id(payload.group_id)
        existing = await session.scalar(select(AiVoiceCharacterRule).where(AiVoiceCharacterRule.bot_qq == payload.bot_qq, AiVoiceCharacterRule.group_id == group_id))
        if existing and not existing.is_deleted:
            raise BusinessError("该机器人和群范围已存在语音角色配置", 40060)
        if existing:
            self._assign(existing, bot.owner_id, payload)
            existing.is_deleted = False
            existing.updated_at = beijing_now()
            rule = existing
        else:
            rule = AiVoiceCharacterRule(owner_id=bot.owner_id, bot_qq=payload.bot_qq, group_id=group_id, character_id=payload.character_id, character_name=payload.character_name, preview_url=payload.preview_url, is_enabled=payload.is_enabled)
            session.add(rule)
        await session.commit()
        await session.refresh(rule)
        return self.serialize(rule, {bot.owner_id: user.username})

    async def update_rule(self, session: AsyncSession, user: User, rule_id: int, payload: AiVoiceCharacterUpdateRequest) -> dict[str, Any]:
        """修改角色配置。"""
        rule = await self._get_active(session, user, rule_id)
        bot = await self._ensure_bot_access(session, user, payload.bot_qq)
        group_id = self.normalize_group_id(payload.group_id)
        conflict = await session.scalar(select(AiVoiceCharacterRule).where(AiVoiceCharacterRule.bot_qq == payload.bot_qq, AiVoiceCharacterRule.group_id == group_id, AiVoiceCharacterRule.id != rule_id, AiVoiceCharacterRule.is_deleted.is_(False)))
        if conflict:
            raise BusinessError("该机器人和群范围已存在语音角色配置", 40061)
        rule.owner_id = bot.owner_id
        rule.bot_qq = payload.bot_qq
        rule.group_id = group_id
        self._assign(rule, bot.owner_id, payload)
        rule.updated_at = beijing_now()
        await session.commit()
        await session.refresh(rule)
        return self.serialize(rule, {bot.owner_id: user.username})

    async def soft_delete(self, session: AsyncSession, user: User, rule_id: int) -> None:
        """软删除角色配置。"""
        rule = await self._get_active(session, user, rule_id)
        rule.is_deleted = True
        rule.is_enabled = False
        rule.updated_at = beijing_now()
        await session.commit()

    async def resolve_character(self, bot_qq: str, group_id: str) -> str:
        """按群级、机器人默认级返回已启用的配置角色，未命中返回空字符串。"""
        if self._session_factory is None:
            return ""
        async with self._session_factory() as session:
            rows = (await session.scalars(select(AiVoiceCharacterRule).where(AiVoiceCharacterRule.bot_qq == str(bot_qq), AiVoiceCharacterRule.group_id.in_((str(group_id), "")), AiVoiceCharacterRule.is_enabled.is_(True), AiVoiceCharacterRule.is_deleted.is_(False)).order_by(AiVoiceCharacterRule.group_id.desc(), AiVoiceCharacterRule.id.desc()))).all()
        return str(rows[0].character_id).strip() if rows else ""

    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory

    @staticmethod
    def normalize_group_id(group_id: str | None) -> str:
        """将空群号统一为默认配置。"""
        return str(group_id or "").strip()

    @staticmethod
    def _assign(rule: AiVoiceCharacterRule, owner_id: int, payload: AiVoiceCharacterCreateRequest) -> None:
        rule.owner_id = owner_id
        rule.character_id = payload.character_id
        rule.character_name = payload.character_name
        rule.preview_url = payload.preview_url
        rule.is_enabled = payload.is_enabled

    async def _ensure_bot_access(self, session: AsyncSession, user: User, bot_qq: str) -> BotAccount:
        """校验机器人归属。"""
        bot = await session.scalar(select(BotAccount).where(BotAccount.bot_qq == bot_qq, BotAccount.is_deleted.is_(False)))
        if bot is None:
            raise BusinessError("请先在机器人管理中添加该 QQ", 40430)
        if not is_admin(user) and bot.owner_id != user.id:
            raise BusinessError("无权为该机器人配置语音角色", 40360)
        return bot

    async def _get_active(self, session: AsyncSession, user: User, rule_id: int) -> AiVoiceCharacterRule:
        rule = await session.scalar(self._scope(select(AiVoiceCharacterRule).where(AiVoiceCharacterRule.id == rule_id, AiVoiceCharacterRule.is_deleted.is_(False)), user))
        if rule is None:
            raise BusinessError("语音角色配置不存在或无权访问", 40460)
        return rule

    @staticmethod
    def serialize(rule: AiVoiceCharacterRule, owners: dict[int, str]) -> dict[str, Any]:
        """序列化角色规则。"""
        return {"id": rule.id, "owner_id": rule.owner_id, "owner_username": owners.get(rule.owner_id, ""), "bot_qq": rule.bot_qq, "group_id": rule.group_id, "character_id": rule.character_id, "character_name": rule.character_name, "preview_url": rule.preview_url, "is_enabled": rule.is_enabled, "created_at": format_beijing(rule.created_at), "updated_at": format_beijing(rule.updated_at)}
