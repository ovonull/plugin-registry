"""AI 随机主动回复配置、调度和 OneBot 发送服务。

功能：
1. 按机器人和群维护主动回复规则，空群号表示该机器人的默认规则。
2. 按规则配置 1 到 5 分钟范围内的随机回复间隔。
3. 从近期群消息中随机选择一条，由 AI 生成不带引用的回复并发送。
4. 使用 Redis 锁保证多进程部署时同一规则不会重复执行。
"""

import asyncio
from datetime import timedelta
import hashlib
import json
import logging
import random
import re
from typing import Any
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ai_message_context import format_message_with_sender, sender_fields_from_record
from app.common.permissions import is_admin, owner_scope
from app.common.responses import BusinessError
from app.common.time_utils import beijing_now, format_beijing
from app.models.entities import (
    AiProactiveReplyMessage,
    AiProactiveReplyRule,
    BotAccount,
    ChatMessage,
    User,
)
from app.schemas import (
    AiProactiveReplyCreateRequest,
    AiProactiveReplyUpdateRequest,
)
from app.services.bot_ai_service import BotAiService
from app.services.bot_service import BotService
from app.services.chat_message_service import ChatMessageService
from app.services.ai_message_intent import should_skip_reply
from app.services.ai_message_reply_guard import AiMessageReplyGuard


logger = logging.getLogger("uvicorn.error")
SCHEDULER_INTERVAL_SECONDS = 15
MESSAGE_WINDOW_SECONDS = 600
LOCK_TTL_SECONDS = 300
RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class AiProactiveReplyService:
    """维护主动回复规则并运行后台随机调度器。"""

    def __init__(
        self,
        database: Any,
        bot_service: BotService,
        bot_ai_service: BotAiService,
        onebot_service: Any,
        redis: Redis | None,
        ai_message_reply_guard: AiMessageReplyGuard | None = None,
    ) -> None:
        self.database = database
        self.bot_service = bot_service
        self.bot_ai_service = bot_ai_service
        self.onebot_service = onebot_service
        self.chat_message_service = ChatMessageService(database)
        self.redis = redis
        self.ai_message_reply_guard = (
            ai_message_reply_guard or AiMessageReplyGuard(redis)
        )
        self._task: asyncio.Task | None = None
        self._local_lock = asyncio.Lock()

    @staticmethod
    def _scope(query: Select, user: User) -> Select:
        """为普通用户追加规则所属用户过滤。"""
        owner_id = owner_scope(user)
        if owner_id is not None:
            query = query.where(AiProactiveReplyRule.owner_id == owner_id)
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
        """按当前用户权限分页返回主动回复规则。"""
        conditions = [AiProactiveReplyRule.is_deleted.is_(False)]
        if bot_qq:
            conditions.append(AiProactiveReplyRule.bot_qq == bot_qq)
        if group_id:
            conditions.append(AiProactiveReplyRule.group_id == group_id)
        if enabled is not None:
            conditions.append(AiProactiveReplyRule.is_enabled.is_(enabled))
        query = self._scope(select(AiProactiveReplyRule).where(*conditions), user)
        count_query = self._scope(
            select(func.count(AiProactiveReplyRule.id)).where(*conditions), user
        )
        total = int(await session.scalar(count_query) or 0)
        rules = (
            await session.scalars(
                query.order_by(AiProactiveReplyRule.id.desc())
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
        payload: AiProactiveReplyCreateRequest,
    ) -> dict[str, Any]:
        """新增一条机器人默认或指定群主动回复规则。"""
        bot = await self._ensure_bot_access(session, user, payload.bot_qq)
        group_id = self.normalize_group_id(payload.group_id)
        existing = await self._find_same(session, payload.bot_qq, group_id)
        if existing is not None and not existing.is_deleted:
            raise BusinessError("该机器人和群已存在 AI 主动回复规则", 40062)
        now = beijing_now()
        if existing is None:
            existing = AiProactiveReplyRule(
                owner_id=bot.owner_id,
                bot_qq=payload.bot_qq,
                group_id=group_id,
            )
            session.add(existing)
        existing.owner_id = bot.owner_id
        existing.bot_qq = payload.bot_qq
        existing.group_id = group_id
        existing.is_enabled = payload.is_enabled
        existing.min_interval_seconds = payload.min_interval_seconds
        existing.max_interval_seconds = payload.max_interval_seconds
        existing.is_deleted = False
        existing.next_run_at = self._next_run(now, payload) if payload.is_enabled else None
        existing.updated_at = now
        await session.commit()
        await session.refresh(existing)
        return self.serialize(existing, {bot.owner_id: user.username})

    async def update_rule(
        self,
        session: AsyncSession,
        user: User,
        rule_id: int,
        payload: AiProactiveReplyUpdateRequest,
    ) -> dict[str, Any]:
        """修改主动回复规则并检查机器人和群范围冲突。"""
        rule = await self._get_active(session, user, rule_id)
        bot = await self._ensure_bot_access(session, user, payload.bot_qq)
        group_id = self.normalize_group_id(payload.group_id)
        existing = await self._find_same(session, payload.bot_qq, group_id)
        if existing is not None and existing.id != rule.id and not existing.is_deleted:
            raise BusinessError("该机器人和群已存在 AI 主动回复规则", 40063)
        now = beijing_now()
        rule.owner_id = bot.owner_id
        rule.bot_qq = payload.bot_qq
        rule.group_id = group_id
        rule.is_enabled = payload.is_enabled
        rule.min_interval_seconds = payload.min_interval_seconds
        rule.max_interval_seconds = payload.max_interval_seconds
        rule.next_run_at = self._next_run(now, payload) if payload.is_enabled else None
        rule.updated_at = now
        await session.commit()
        owner_name = await session.scalar(select(User.username).where(User.id == rule.owner_id))
        return self.serialize(rule, {rule.owner_id: owner_name or user.username})

    async def soft_delete(
        self, session: AsyncSession, user: User, rule_id: int
    ) -> None:
        """软删除一条主动回复规则。"""
        rule = await self._get_active(session, user, rule_id)
        rule.is_deleted = True
        rule.is_enabled = False
        rule.next_run_at = None
        rule.updated_at = beijing_now()
        await session.commit()

    async def start(self) -> None:
        """启动主动回复后台调度任务。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_scheduler())

    async def close(self) -> None:
        """停止主动回复后台调度任务。"""
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _run_scheduler(self) -> None:
        """周期扫描到期规则，异常不会终止后续调度。"""
        while True:
            try:
                await self._run_due_rules()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("AI 主动回复调度执行异常")
            await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)

    async def _run_due_rules(self) -> None:
        """读取到期规则并逐条执行。"""
        now = beijing_now()
        async with self.database.session_factory() as session:
            rules = (
                await session.scalars(
                    select(AiProactiveReplyRule).where(
                        AiProactiveReplyRule.is_enabled.is_(True),
                        AiProactiveReplyRule.is_deleted.is_(False),
                        or_(
                            AiProactiveReplyRule.next_run_at.is_(None),
                            AiProactiveReplyRule.next_run_at <= now,
                        ),
                    )
                )
            ).all()
            rule_ids = [rule.id for rule in rules]
        for rule_id in rule_ids:
            await self._process_rule(rule_id)

    async def _process_rule(self, rule_id: int) -> None:
        """抢占规则锁、选择消息、生成 AI 回复并发送。"""
        lock_token = await self._acquire_lock(rule_id)
        if lock_token is None:
            return
        try:
            async with self._local_lock:
                async with self.database.session_factory() as session:
                    rule = await session.scalar(
                        select(AiProactiveReplyRule).where(
                            AiProactiveReplyRule.id == rule_id,
                            AiProactiveReplyRule.is_enabled.is_(True),
                            AiProactiveReplyRule.is_deleted.is_(False),
                        )
                    )
                    if rule is None:
                        return
                    now = beijing_now()
                    if rule.next_run_at is not None and rule.next_run_at > now:
                        return
                    config = await self.bot_ai_service.active_runtime_config(
                        session, rule.bot_qq
                    )
                    if config is None:
                        rule.next_run_at = self._next_run(now, rule)
                        rule.updated_at = now
                        await session.commit()
                        return
                    message = await self._choose_message(session, rule, now)
                    rule.next_run_at = self._next_run(now, rule)
                    rule.last_run_at = now
                    message_key = ""
                    reply_claim = None
                    if message is not None:
                        reply_claim = await self.ai_message_reply_guard.claim(
                            self.ai_message_reply_guard.record_key(message)
                        )
                        if reply_claim is None:
                            logger.info(
                                "AI 主动回复消息已被其他流程占用，跳过重复触发，机器人=%s，群=%s，消息=%s",
                                rule.bot_qq,
                                message.group_id,
                                message.message_id or message.id,
                            )
                            message = None
                        else:
                            rule.last_message_id = str(message.message_id or message.id)
                            message_key = self._message_key(message)
                            session.add(
                                AiProactiveReplyMessage(
                                    owner_id=rule.owner_id,
                                    rule_id=rule.id,
                                    bot_qq=rule.bot_qq,
                                    group_id=str(message.group_id or ""),
                                    message_key=message_key,
                                    message_id=str(message.message_id or ""),
                                )
                            )
                    rule.updated_at = now
                    try:
                        await session.commit()
                    except IntegrityError:
                        await session.rollback()
                        duplicate = bool(
                            message_key
                            and await session.scalar(
                                select(AiProactiveReplyMessage.id).where(
                                    AiProactiveReplyMessage.rule_id == rule_id,
                                    AiProactiveReplyMessage.message_key == message_key,
                                    AiProactiveReplyMessage.is_deleted.is_(False),
                                )
                            )
                        )
                        if not duplicate:
                            raise
                        logger.info(
                            "AI 主动回复消息已被其他任务占用，跳过重复触发，规则=%s，消息=%s",
                            rule_id,
                            message.message_id or message.id,
                        )
                        return
                if message is None:
                    return
                event = self._message_event(message, rule.bot_qq)
                history = await self.chat_message_service.get_ai_history(
                    event,
                    rule.bot_qq,
                    limit=int(config.get("history_limit", 10) or 0),
                    current_record_id=message.id,
                )
                intent = await self.onebot_service.analyse_message_intent(event)
                logger.info(
                    "[OneBot AI 主动回复] 意图分类完成，机器人=%s，群=%s，消息=%s，回复形式=%s，原因=%s",
                    rule.bot_qq,
                    message.group_id,
                    message.message_id or message.id,
                    intent["reply_type"],
                    intent["reason"],
                )
                if should_skip_reply(intent):
                    # AI 判定这条消息不需要机器人参与，本轮不生成内容也不发送。
                    logger.info(
                        "[OneBot AI 主动回复] 意图判定为不回复，跳过本条消息，机器人=%s，群=%s，消息=%s，原因=%s",
                        rule.bot_qq,
                        message.group_id,
                        message.message_id or message.id,
                        intent["reason"] or "未提供",
                    )
                    return
                reply = await self.bot_ai_service.generate(
                    config,
                    self._build_prompt(message),
                    history=history,
                    flatten_history=True,
                )
                logger.info(
                    "[OneBot AI 主动回复] 机器人=%s，群=%s，历史聊天记录数=%s",
                    rule.bot_qq,
                    message.group_id,
                    len(history),
                )
                reply = re.sub(r"\[CQ:[^\]]+\]", "", str(reply or "")).strip()
                if not reply:
                    logger.warning(
                        "AI 主动回复未生成内容，机器人=%s，群=%s，消息=%s",
                        rule.bot_qq,
                        message.group_id,
                        message.message_id or message.id,
                    )
                    return
                intent_type = intent["reply_type"]
                send_config = self.onebot_service._intent_reply_config(
                    config,
                    intent,
                )
                reply_image = None
                if intent_type in {"emoji", "generate_image"}:
                    reply_image = await self.onebot_service._resolve_intent_reply_image(
                        send_config,
                        intent,
                        rule.bot_qq,
                        event,
                    )
                action, response, used_voice = await self.onebot_service.send_ai_reply(
                    event,
                    reply,
                    rule.bot_qq,
                    config=send_config,
                    reply_image=reply_image,
                    image_only=reply_image is not None,
                    suppress_quote=True,
                )
                logger.info(
                    "[OneBot AI 主动回复] 机器人=%s，群=%s，消息=%s，action=%s，voice=%s，AI 返回=%s，response=%s",
                    rule.bot_qq,
                    message.group_id,
                    message.message_id or message.id,
                    action,
                    used_voice,
                    reply,
                    response,
                )
                if response.get("status") != "ok":
                    logger.error(
                        "AI 主动回复发送失败，机器人=%s，群=%s，action=%s，response=%s",
                        rule.bot_qq,
                        message.group_id,
                        action,
                        response,
                    )
                else:
                    data = response.get("data")
                    sent_message_id = (
                        data.get("message_id")
                        if isinstance(data, dict)
                        else None
                    )
                    try:
                        await self.onebot_service.record_sent_group_message(
                            rule.bot_qq,
                            str(message.group_id),
                            reply,
                            sent_message_id,
                        )
                    except Exception:
                        logger.exception(
                            "AI 主动回复已发送但机器人消息记录写入失败，机器人=%s，群=%s，消息=%s",
                            rule.bot_qq,
                            message.group_id,
                            message.message_id or message.id,
                        )
        except Exception as exc:
            logger.error("AI 主动回复处理失败，规则=%s，错误=%s", rule_id, exc)
        finally:
            await self._release_lock(rule_id, lock_token)

    async def _choose_message(
        self,
        session: AsyncSession,
        rule: AiProactiveReplyRule,
        now,
    ) -> ChatMessage | None:
        """从近期群消息中随机选择一条当前允许的用户消息。"""
        conditions = [
            ChatMessage.bot_qq == rule.bot_qq,
            ChatMessage.post_type == "message",
            ChatMessage.message_type == "group",
            ChatMessage.group_id.is_not(None),
            ChatMessage.message_text != "",
            ChatMessage.is_deleted.is_(False),
            or_(
                ChatMessage.ai_triggered.is_(False),
                ChatMessage.ai_triggered.is_(None),
            ),
            ChatMessage.event_time >= now - timedelta(seconds=MESSAGE_WINDOW_SECONDS),
            or_(ChatMessage.user_id.is_(None), ChatMessage.user_id != rule.bot_qq),
            or_(ChatMessage.sender_user_id.is_(None), ChatMessage.sender_user_id != rule.bot_qq),
        ]
        if rule.group_id:
            conditions.append(ChatMessage.group_id == rule.group_id)
        if rule.last_message_id:
            conditions.append(
                or_(
                    ChatMessage.message_id.is_(None),
                    ChatMessage.message_id != rule.last_message_id,
                )
            )
        processed_keys = set(
            await session.scalars(
                select(AiProactiveReplyMessage.message_key).where(
                    AiProactiveReplyMessage.rule_id == rule.id,
                    AiProactiveReplyMessage.is_deleted.is_(False),
                )
            )
        )
        rows = (
            await session.scalars(
                select(ChatMessage)
                .where(*conditions)
                .order_by(ChatMessage.id.desc())
                .limit(100)
            )
        ).all()
        random.shuffle(rows)
        latest_by_group: dict[str, ChatMessage | None] = {}
        for message in rows:
            if self._message_key(message) in processed_keys:
                continue
            group_id = str(message.group_id or "")
            if not group_id or not await self.bot_service.is_group_allowed(
                session, rule.bot_qq, group_id
            ):
                continue
            if group_id not in latest_by_group:
                latest_by_group[group_id] = await session.scalar(
                    select(ChatMessage)
                    .where(
                        ChatMessage.bot_qq == rule.bot_qq,
                        ChatMessage.group_id == group_id,
                        ChatMessage.is_deleted.is_(False),
                    )
                    .order_by(
                        ChatMessage.event_time.is_(None),
                        ChatMessage.event_time.desc(),
                        ChatMessage.id.desc(),
                    )
                    .limit(1)
                )
            latest_message = latest_by_group[group_id]
            if latest_message is not None and self._is_bot_message(
                latest_message, rule.bot_qq
            ):
                logger.info(
                    "AI 主动回复本轮跳过机器人最后发言的群，机器人=%s，群=%s，消息=%s",
                    rule.bot_qq,
                    group_id,
                    latest_message.message_id or latest_message.id,
                )
                continue
            if not rule.group_id and await self._has_specific_rule(
                session, rule.bot_qq, group_id
            ):
                continue
            return message
        return None

    @staticmethod
    def _is_bot_message(message: ChatMessage, bot_qq: str) -> bool:
        """判断聊天记录是否由当前机器人发送。"""
        return sender_fields_from_record(message)["user_id"] == bot_qq

    @staticmethod
    def _message_key(message: ChatMessage) -> str:
        """生成跨 HTTP/WebSocket 重复事件稳定一致的消息键。"""
        if message.message_id:
            identity = "\x00".join(
                (
                    message.bot_qq,
                    str(message.group_id or ""),
                    message.message_id,
                )
            )
            digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            return f"id:{digest}"
        if message.event_data:
            digest = hashlib.sha256(message.event_data.encode("utf-8")).hexdigest()
            return f"event:{digest}"
        return f"row:{message.id}"

    @staticmethod
    def _message_event(message: ChatMessage, bot_qq: str) -> dict[str, Any]:
        """将聊天记录还原为可供统一意图分类和回复发送使用的事件。"""
        event: dict[str, Any] = {}
        if message.event_data:
            try:
                loaded = json.loads(message.event_data)
                event = loaded if isinstance(loaded, dict) else {}
            except (TypeError, ValueError):
                event = {}
        sender = event.get("sender")
        sender = dict(sender) if isinstance(sender, dict) else {}
        fields = sender_fields_from_record(message)
        sender["user_id"] = fields["user_id"] or sender.get("user_id") or ""
        sender["nickname"] = fields["sender_nickname"] or sender.get("nickname") or ""
        sender["card"] = fields["sender_card"] or sender.get("card") or ""
        event.update(
            {
                "self_id": str(bot_qq),
                "post_type": "message",
                "message_type": "group",
                "group_id": str(message.group_id or ""),
                "user_id": str(sender["user_id"]),
                "message_id": str(message.message_id or ""),
                "sender": sender,
            }
        )
        if "message" not in event:
            event["message"] = message.message_text
        if "raw_message" not in event:
            event["raw_message"] = message.raw_message or message.message_text
        return event

    @staticmethod
    def _build_prompt(message: ChatMessage) -> str:
        """根据聊天记录生成附带发送者资料、不要求引用原消息的主动回复提示词。"""
        content = format_message_with_sender(
            message.message_text,
            **sender_fields_from_record(message),
        )
        return (
            "你正在参与一个 QQ 群聊。请自然、简短地回复下面这条群友消息，"
            "像普通群成员一样参与对话。不要 @任何人，不要引用原消息，"
            "不要输出标题、分析过程或解释，只返回回复正文。\n"
            f"{content}"
        )

    async def _acquire_lock(self, rule_id: int) -> str | None:
        """获取跨进程 Redis 规则锁；未配置 Redis 时允许单进程执行。"""
        if self.redis is None:
            return uuid4().hex
        token = uuid4().hex
        try:
            created = await self.redis.set(
                f"napcat:ai_proactive:lock:{rule_id}",
                token,
                ex=LOCK_TTL_SECONDS,
                nx=True,
            )
            return token if created else None
        except RedisError as exc:
            logger.error("AI 主动回复 Redis 加锁失败，规则=%s，错误=%s", rule_id, exc)
            return None

    async def _release_lock(self, rule_id: int, token: str) -> None:
        """仅释放当前进程持有的 Redis 规则锁。"""
        if self.redis is None:
            return
        try:
            await self.redis.eval(
                RELEASE_LOCK_SCRIPT,
                1,
                f"napcat:ai_proactive:lock:{rule_id}",
                token,
            )
        except RedisError as exc:
            logger.error("AI 主动回复 Redis 解锁失败，规则=%s，错误=%s", rule_id, exc)

    async def _has_specific_rule(
        self, session: AsyncSession, bot_qq: str, group_id: str
    ) -> bool:
        """判断指定群是否已有主动回复规则，以便覆盖机器人默认规则。"""
        return bool(
            await session.scalar(
                select(AiProactiveReplyRule.id).where(
                    AiProactiveReplyRule.bot_qq == bot_qq,
                    AiProactiveReplyRule.group_id == group_id,
                    AiProactiveReplyRule.is_enabled.is_(True),
                    AiProactiveReplyRule.is_deleted.is_(False),
                )
            )
        )

    async def _ensure_bot_access(
        self, session: AsyncSession, user: User, bot_qq: str
    ) -> BotAccount:
        """校验机器人存在且属于当前用户或管理员。"""
        bot = await session.scalar(
            select(BotAccount).where(
                BotAccount.bot_qq == bot_qq,
                BotAccount.is_deleted.is_(False),
            )
        )
        if bot is None:
            raise BusinessError("请先在机器人管理中添加该 QQ", 40430)
        if not is_admin(user) and bot.owner_id != user.id:
            raise BusinessError("无权为该机器人配置 AI 主动回复", 40336)
        return bot

    async def _get_active(
        self, session: AsyncSession, user: User, rule_id: int
    ) -> AiProactiveReplyRule:
        """读取当前用户有权访问的未删除规则。"""
        rule = await session.scalar(
            self._scope(
                select(AiProactiveReplyRule).where(
                    AiProactiveReplyRule.id == rule_id,
                    AiProactiveReplyRule.is_deleted.is_(False),
                ),
                user,
            )
        )
        if rule is None:
            raise BusinessError("AI 主动回复规则不存在或无权访问", 40461)
        return rule

    @staticmethod
    async def _find_same(
        session: AsyncSession, bot_qq: str, group_id: str
    ) -> AiProactiveReplyRule | None:
        """按机器人和群查找现有规则，优先复用软删除记录。"""
        return await session.scalar(
            select(AiProactiveReplyRule)
            .where(
                AiProactiveReplyRule.bot_qq == bot_qq,
                AiProactiveReplyRule.group_id == group_id,
            )
            .order_by(AiProactiveReplyRule.is_deleted.asc(), AiProactiveReplyRule.id.desc())
        )

    @staticmethod
    async def _owner_names(
        session: AsyncSession, owner_ids: set[int]
    ) -> dict[int, str]:
        """批量查询规则所属用户名。"""
        if not owner_ids:
            return {}
        rows = await session.execute(select(User.id, User.username).where(User.id.in_(owner_ids)))
        return {row.id: row.username for row in rows}

    @staticmethod
    def normalize_group_id(group_id: str | None) -> str:
        """将空群号统一为机器人默认规则。"""
        return str(group_id or "").strip()

    @staticmethod
    def _next_run(now, rule: AiProactiveReplyRule | AiProactiveReplyCreateRequest) -> Any:
        """按规则的最小和最大间隔生成下一次执行时间。"""
        seconds = random.randint(rule.min_interval_seconds, rule.max_interval_seconds)
        return now + timedelta(seconds=seconds)

    @staticmethod
    def serialize(
        rule: AiProactiveReplyRule, owner_names: dict[int, str]
    ) -> dict[str, Any]:
        """序列化主动回复规则。"""
        return {
            "id": rule.id,
            "owner_id": rule.owner_id,
            "owner_username": owner_names.get(rule.owner_id, "未知用户"),
            "bot_qq": rule.bot_qq,
            "group_id": rule.group_id,
            "is_enabled": rule.is_enabled,
            "min_interval_seconds": rule.min_interval_seconds,
            "max_interval_seconds": rule.max_interval_seconds,
            "next_run_at": format_beijing(rule.next_run_at) if rule.next_run_at else None,
            "last_run_at": format_beijing(rule.last_run_at) if rule.last_run_at else None,
            "last_message_id": rule.last_message_id,
            "created_at": format_beijing(rule.created_at),
            "updated_at": format_beijing(rule.updated_at),
        }
