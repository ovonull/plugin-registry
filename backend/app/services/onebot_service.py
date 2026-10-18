"""OneBot HTTP/WS 网关、事件广播与自动回复协调服务。"""

import asyncio
from collections.abc import Awaitable, Callable
import html
import json
import logging
import random
import re
import time
from typing import Any

import httpx
from redis.asyncio import Redis
from sqlalchemy import select

from app.common.ai_message_context import (
    format_forward_context,
    format_message_with_sender,
    merge_quoted_message,
    sender_fields_from_event,
)
from app.common.config import Settings
from app.common.database import Database
from app.common.responses import BusinessError, onebot_error
from app.models.entities import (
    AutoReplyRule,
    BotAccount,
    BotFriend,
    ForbiddenWordRule,
    WelcomeMessageRule,
)
from app.services.auto_reply_service import AutoReplyService
from app.services.ai_followup_service import AiFollowupService
from app.services.ai_image_service import AiImage, AiImageService
from app.services.ai_message_reply_guard import AiMessageReplyGuard
from app.services.ai_message_intent import (
    AiMessageIntent,
    build_message_intent_prompt,
    default_message_intent,
    parse_message_intent,
    should_skip_reply,
)
from app.services.animated_emoji_service import (
    AnimatedEmojiService,
    normalise_ai_category,
)
from app.services.bot_ai_service import DEFAULT_VOICE_CHARACTER, BotAiService
from app.services.chat_message_service import ChatMessageService
from app.services.bot_service import BotService
from app.services.ai_voice_character_service import AiVoiceCharacterService
from app.services.ai_voice_character_rule_service import AiVoiceCharacterRuleService
from app.services.event_broker import EventBroker
from app.services.forbidden_word_service import ForbiddenWordService
from app.services.forbidden_mute_service import ForbiddenMuteService
from app.services.ai_trigger_keyword_service import AiTriggerKeywordService
from app.services.onebot_reply import (
    build_websocket_reply_content,
    is_poke_event,
    mentions_bot,
    reply_message_id,
)
from app.services.plugin_hooks import PluginHooks
from app.services.reverse_websocket import ReverseSession, ReverseWebSocketManager
from app.services.welcome_message_service import WelcomeMessageService


logger = logging.getLogger("uvicorn.error")
ACTION_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


def _format_payload(payload: Any) -> str:
    """将 OneBot 数据完整序列化为易读的中文日志文本。"""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


class OneBotService:
    """统一处理 OneBot 事件、自动回复与动作代理。"""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        auto_reply_service: AutoReplyService,
        bot_service: BotService,
        bot_ai_service: BotAiService,
        ai_followup_service: AiFollowupService,
        welcome_message_service: WelcomeMessageService,
        forbidden_word_service: ForbiddenWordService,
        redis: Redis | None = None,
        ai_voice_character_rule_service: AiVoiceCharacterRuleService | None = None,
        ai_trigger_keyword_service: AiTriggerKeywordService | None = None,
        animated_emoji_service: AnimatedEmojiService | None = None,
        ai_message_reply_guard: AiMessageReplyGuard | None = None,
        plugin_hooks: PluginHooks | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.auto_reply_service = auto_reply_service
        self.bot_service = bot_service
        self.bot_ai_service = bot_ai_service
        self.ai_followup_service = ai_followup_service
        self.welcome_message_service = welcome_message_service
        self.forbidden_word_service = forbidden_word_service
        self.forbidden_mute_service = ForbiddenMuteService(redis)
        self.ai_trigger_keyword_service = ai_trigger_keyword_service
        self.animated_emoji_service = animated_emoji_service
        self.ai_voice_character_service = AiVoiceCharacterService(redis)
        self.ai_voice_character_rule_service = ai_voice_character_rule_service
        self.ai_message_reply_guard = ai_message_reply_guard or AiMessageReplyGuard(redis)
        self.plugin_hooks = plugin_hooks or PluginHooks(settings, database)
        self.chat_message_service = ChatMessageService(database)
        self.broker = EventBroker(settings.event_queue_size)
        self.reverse_manager = ReverseWebSocketManager(settings.onebot_action_timeout)
        self.http_client = httpx.AsyncClient(timeout=settings.onebot_action_timeout)
        self.ai_image_service = AiImageService(self.http_client)
        if self.animated_emoji_service is not None:
            self.animated_emoji_service.set_image_service(self.ai_image_service)
        self._reply_tasks: set[asyncio.Task] = set()

    async def close(self) -> None:
        """取消后台回复任务并关闭 HTTP 连接池。"""
        for task in tuple(self._reply_tasks):
            task.cancel()
        if self._reply_tasks:
            await asyncio.gather(*self._reply_tasks, return_exceptions=True)
        await self.http_client.aclose()

    async def handle_http_event(
        self, event: dict[str, Any], fallback_bot_qq: str = ""
    ) -> dict[str, Any]:
        """广播 HTTP 上报事件并返回 NapCat 快速回复操作。"""
        logger.info("[OneBot HTTP] 收到事件：%s", _format_payload(event))
        bot_qq = str(event.get("self_id") or fallback_bot_qq or "")
        reply_key = self.ai_message_reply_guard.event_key(event, bot_qq)
        preclaim_reply = bool(
            reply_key and self._should_preclaim_message(event, bot_qq)
        )
        reply_claim = None
        chat_record = await self.chat_message_service.record_event(
            event, "http", fallback_bot_qq
        )
        await self._record_animated_emoji_event(event, fallback_bot_qq, "OneBot HTTP")
        processing_event = dict(self._with_bot_qq(event, fallback_bot_qq))
        processing_event["_chat_message_record_id"] = chat_record.id if chat_record else None
        if not await self._event_allowed(processing_event, "OneBot HTTP"):
            logger.info("[OneBot HTTP] 事件被机器人或群白名单拦截")
            return {}
        if await self._is_sender_blocked(processing_event):
            logger.info(
                "[OneBot HTTP] 发送者已被屏蔽，跳过全部处理，机器人=%s，用户=%s",
                bot_qq,
                processing_event.get("user_id") or "未知",
            )
            return {}
        if preclaim_reply:
            reply_claim = await self.ai_message_reply_guard.claim(reply_key)
            if reply_claim is None:
                logger.info(
                    "[OneBot HTTP] 消息已被其他回复流程占用，跳过重复处理，机器人=%s，消息=%s",
                    bot_qq,
                    event.get("message_id") or "无消息ID",
                )
                return {}
        processing_event["_ai_reply_guard_key"] = reply_key
        processing_event["_ai_reply_guard_claimed"] = reply_claim is not None
        await self.broker.publish(event)
        await self.plugin_hooks.dispatch_event(processing_event, "OneBot HTTP")
        if self._is_group_request(processing_event):
            await self._handle_group_request(processing_event)
            return {}
        if self._is_friend_request(processing_event):
            await self._handle_friend_request(processing_event)
            return {}
        moderation_command = self._parse_moderation_command(processing_event)
        if moderation_command is not None:
            if not await self._ensure_reply_claim(
                processing_event, bot_qq, "OneBot HTTP"
            ):
                return {}
            await self._handle_http_moderation_command(
                processing_event, moderation_command
            )
            return {}
        if self._is_title_command(processing_event):
            if not await self._ensure_reply_claim(
                processing_event, bot_qq, "OneBot HTTP"
            ):
                return {}
            await self._handle_http_special_title(processing_event)
            return {}
        processing_event = await self._resolve_forward_content(
            processing_event, fallback_bot_qq
        )
        forbidden_rule, ai_intent = await self._check_message_policy(
            processing_event
        )
        if forbidden_rule is not None:
            if not await self._ensure_reply_claim(
                processing_event, bot_qq, "OneBot HTTP"
            ):
                return {}
            await self._handle_http_forbidden(processing_event, forbidden_rule)
            return {}
        welcome_enabled, welcome_rule = await self._welcome_decision(processing_event)
        if welcome_enabled:
            content = welcome_rule.welcome_content if welcome_rule else None
            if not content:
                content = await self._generate_welcome_ai_reply(processing_event)
            response = self._build_http_welcome_response(processing_event, content)
            if response is not None:
                logger.info(
                    "[OneBot HTTP] 进群欢迎回复：%s；返回：%s",
                    content or "未生成内容",
                    _format_payload(response),
                )
                return response
            logger.info("[OneBot HTTP] 进群欢迎事件缺少 user_id，未发送回复")
            return {}
        rule = await self._match_rule(processing_event)
        if rule is None:
            ai_result = await self._generate_ai_reply(
                processing_event,
                fallback_bot_qq,
                intent=ai_intent,
            )
            if ai_result is None:
                logger.info("[OneBot HTTP] 未命中自动回复且未触发 AI，返回：{}")
                return {}
            ai_reply, _ = ai_result
            async with self.database.session_factory() as db_session:
                config = await self.bot_ai_service.active_runtime_config(
                    db_session, bot_qq
                )
            resolved_intent = (
                processing_event.pop("_ai_message_intent", None)
                or ai_intent
                or default_message_intent()
            )
            intent_type = resolved_intent["reply_type"]
            if intent_type in {"voice", "emoji", "generate_image"}:
                intent_config = self._intent_reply_config(config, resolved_intent)
                reply_image = await self._resolve_intent_reply_image(
                    intent_config,
                    resolved_intent,
                    bot_qq,
                    event,
                )
                action, action_response, used_voice = await self.send_ai_reply(
                    event,
                    ai_reply,
                    bot_qq,
                    config=intent_config,
                    reply_image=reply_image,
                    image_only=(
                        intent_type in {"emoji", "generate_image"}
                        and reply_image is not None
                    ),
                    suppress_quote=intent_type != "voice",
                )
                logger.info(
                    "[OneBot HTTP] AI 意图回复完成，机器人=%s，意图=%s，action=%s，voice=%s，response=%s",
                    bot_qq,
                    intent_type,
                    action,
                    used_voice,
                    _format_payload(action_response),
                )
                if action_response.get("status") == "ok":
                    return {}
            if self._should_use_voice(config, event):
                voice_config = dict(config or {})
                voice_config["voice_reply_probability"] = 100
                action, action_response, used_voice = await self.send_ai_reply(
                    event,
                    ai_reply,
                    bot_qq,
                    config=voice_config,
                )
                logger.info(
                    "[OneBot HTTP] AI 随机语音回复，机器人=%s，action=%s，voice=%s，response=%s",
                    bot_qq,
                    action,
                    used_voice,
                    _format_payload(action_response),
                )
                if action_response.get("status") == "ok":
                    return {}
            reply_image = (
                await self._resolve_reply_image(config, ai_reply, bot_qq, event)
                if self._should_use_image(config, event)
                else None
            )
            if reply_image is not None:
                text_config = dict(config or {})
                text_config["voice_reply_enabled"] = False
                text_config["voice_reply_probability"] = 0
                action, action_response, _ = await self.send_ai_reply(
                    event,
                    ai_reply,
                    bot_qq,
                    config=text_config,
                    reply_image=reply_image,
                    suppress_quote=True,
                )
                logger.info(
                    "[OneBot HTTP] AI 图片回复动作返回，机器人=%s，action=%s，response=%s",
                    bot_qq,
                    action,
                    _format_payload(action_response),
                )
                if action_response.get("status") == "ok":
                    return {}
            response = {"reply": ai_reply}
            logger.info(
                "[OneBot HTTP] AI 回复内容：%s；返回：%s",
                ai_reply,
                _format_payload(response),
            )
            return response
        if not await self._ensure_reply_claim(
            processing_event,
            bot_qq,
            "OneBot HTTP",
        ):
            return {}
        response = {"reply": rule.reply_content}
        logger.info(
            "[OneBot HTTP] 命中规则 id=%s，回复内容：%s；返回：%s",
            rule.id,
            rule.reply_content,
            _format_payload(response),
        )
        return response

    @staticmethod
    def _trace(event: dict[str, Any], bot_qq: str | None = None) -> str:
        """构造带 message_id 的日志追踪串，便于按单条消息 grep 出完整处理链路。

        Args:
            event: 当前处理的事件字典。
            bot_qq: 机器人 QQ，缺省时依次回退到事件里的 self_id/_bot_qq。
        Returns:
            形如「机器人=X，消息=Y，群Z/私聊，用户=U」的追踪串。
        """
        bot = str(bot_qq or event.get("self_id") or event.get("_bot_qq") or "未知")
        message_id = event.get("message_id")
        message_id = str(message_id) if message_id not in (None, "") else "无"
        if event.get("message_type") == "group":
            scene = f"群{event.get('group_id') or '未知'}"
        elif event.get("message_type") == "private":
            scene = "私聊"
        else:
            scene = str(event.get("post_type") or "事件")
        sender = event.get("sender")
        sender = sender if isinstance(sender, dict) else {}
        user_id = str(event.get("user_id") or sender.get("user_id") or "未知")
        return f"机器人={bot}，消息={message_id}，{scene}，用户={user_id}"

    async def handle_websocket_event(
        self, event: dict[str, Any], session: ReverseSession
    ) -> None:
        """广播 WebSocket 事件并异步执行自动回复，避免阻塞收包循环。"""
        logger.info("[OneBot WebSocket] 收到事件：%s", _format_payload(event))
        reply_key = self.ai_message_reply_guard.event_key(event, session.self_id)
        preclaim_reply = bool(
            reply_key and self._should_preclaim_message(event, session.self_id)
        )
        reply_claim = None
        chat_record = await self.chat_message_service.record_event(
            event, "websocket", session.self_id
        )
        await self._record_animated_emoji_event(event, session.self_id, "OneBot WebSocket")
        processing_event = dict(self._with_bot_qq(event, session.self_id))
        processing_event["_chat_message_record_id"] = chat_record.id if chat_record else None
        if not await self._event_allowed(processing_event, "OneBot WebSocket"):
            logger.info(
                "[OneBot WebSocket] 事件被机器人或群白名单拦截，%s",
                self._trace(processing_event, session.self_id),
            )
            return
        if await self._is_sender_blocked(processing_event):
            logger.info(
                "[OneBot WebSocket] 发送者已被屏蔽，跳过全部处理，%s",
                self._trace(processing_event, session.self_id),
            )
            return
        if preclaim_reply:
            reply_claim = await self.ai_message_reply_guard.claim(reply_key)
            if reply_claim is None:
                logger.info(
                    "[OneBot WebSocket] 消息已被其他回复流程占用，跳过重复处理，机器人=%s，消息=%s",
                    session.self_id,
                    event.get("message_id") or "无消息ID",
                )
                return
        processing_event["_ai_reply_guard_key"] = reply_key
        processing_event["_ai_reply_guard_claimed"] = reply_claim is not None
        await self.broker.publish(event)
        await self.plugin_hooks.dispatch_event(processing_event, "OneBot WebSocket")
        if self._is_group_request(processing_event):
            task = asyncio.create_task(
                self._handle_group_request(processing_event, session)
            )
            self._reply_tasks.add(task)
            task.add_done_callback(self._reply_tasks.discard)
            return
        if self._is_friend_request(processing_event):
            task = asyncio.create_task(
                self._handle_friend_request(processing_event, session)
            )
            self._reply_tasks.add(task)
            task.add_done_callback(self._reply_tasks.discard)
            return
        moderation_command = self._parse_moderation_command(processing_event)
        if moderation_command is not None:
            if not await self._ensure_reply_claim(
                processing_event, session.self_id, "OneBot WebSocket"
            ):
                return
            task = asyncio.create_task(
                self._handle_websocket_moderation_command(
                    processing_event, session, moderation_command
                )
            )
            self._reply_tasks.add(task)
            task.add_done_callback(self._reply_tasks.discard)
            return
        if self._is_title_command(processing_event):
            if not await self._ensure_reply_claim(
                processing_event, session.self_id, "OneBot WebSocket"
            ):
                return
            task = asyncio.create_task(
                self._handle_websocket_special_title(processing_event, session)
            )
            self._reply_tasks.add(task)
            task.add_done_callback(self._reply_tasks.discard)
            return
        processing_event = await self._resolve_forward_content(
            processing_event, session.self_id, session
        )
        forbidden_rule, ai_intent = await self._check_message_policy(
            processing_event, session
        )
        if forbidden_rule is not None:
            logger.info(
                "[OneBot WebSocket] 命中违禁词规则 id=%s，进入撤回禁言流程，%s",
                forbidden_rule.id,
                self._trace(processing_event, session.self_id),
            )
            if not await self._ensure_reply_claim(
                processing_event, session.self_id, "OneBot WebSocket"
            ):
                return
            task = asyncio.create_task(
                self._handle_websocket_forbidden(
                    processing_event, session, forbidden_rule
                )
            )
            self._reply_tasks.add(task)
            task.add_done_callback(self._reply_tasks.discard)
            return
        welcome_enabled, welcome_rule = await self._welcome_decision(processing_event)
        if welcome_enabled:
            logger.info(
                "[OneBot WebSocket] 命中欢迎语规则，进入欢迎流程，%s",
                self._trace(processing_event, session.self_id),
            )
            task = asyncio.create_task(
                self._handle_websocket_welcome(
                    processing_event, session, welcome_rule
                )
            )
            self._reply_tasks.add(task)
            task.add_done_callback(self._reply_tasks.discard)
            return
        rule = await self._match_rule(processing_event)
        if rule is None:
            logger.info(
                "[OneBot WebSocket] 未命中自动回复规则，进入 AI 回复检查，%s",
                self._trace(processing_event, session.self_id),
            )
            task = asyncio.create_task(
                self._handle_websocket_ai_reply(
                    processing_event,
                    session,
                    ai_intent,
                )
            )
            self._reply_tasks.add(task)
            task.add_done_callback(self._reply_tasks.discard)
            return
        logger.info(
            "[OneBot WebSocket] 命中自动回复规则 id=%s，%s",
            rule.id,
            self._trace(processing_event, session.self_id),
        )
        action_request = self._build_reply_action(
            event, rule.reply_content, session.self_id
        )
        if action_request is None:
            logger.info(
                "[OneBot WebSocket] 命中规则 id=%s，但事件类型不支持自动回复",
                rule.id,
            )
            return
        action, params = action_request
        if not await self._ensure_reply_claim(
            processing_event, session.self_id, "OneBot WebSocket"
        ):
            return
        logger.info(
            "[OneBot WebSocket] 命中规则 id=%s，发送动作 action=%s，参数：%s",
            rule.id,
            action,
            _format_payload(params),
        )
        task = asyncio.create_task(
            self._send_auto_reply(action, params, session, rule.id)
        )
        self._reply_tasks.add(task)
        task.add_done_callback(self._reply_tasks.discard)

    async def call_action(
        self,
        action: str,
        params: dict[str, Any],
        echo: Any = "",
        self_id: str = "",
    ) -> dict[str, Any]:
        """优先通过反向 WebSocket，必要时通过 NapCat HTTP 服务调用动作。"""
        if not ACTION_PATTERN.fullmatch(action):
            return onebot_error("动作名称格式不正确", 1400, echo)
        logger.info(
            "[OneBot Action] 调用机器人=%s，action=%s，参数=%s",
            self_id or "自动选择",
            action,
            _format_payload(params),
        )
        connection_mode, http_url, http_token = await self._connection_settings(
            self_id
        )
        if connection_mode != "http" and self.reverse_manager.connection_count > 0:
            try:
                result = await self.reverse_manager.call(action, params, self_id=self_id)
                result["echo"] = echo
                logger.info(
                    "[OneBot Action] WebSocket 返回 action=%s，结果=%s",
                    action,
                    _format_payload(result),
                )
                return result
            except BusinessError:
                if connection_mode == "websocket" or not http_url:
                    raise
        if connection_mode == "websocket":
            raise BusinessError("该机器人没有可用的 WebSocket 连接", 50304)
        if not http_url:
            raise BusinessError("没有可用的 NapCat HTTP 或 WebSocket 连接", 50303)

        headers = {"Content-Type": "application/json"}
        if http_token:
            headers["Authorization"] = f"Bearer {http_token}"
        if self_id:
            headers["X-Self-ID"] = self_id
        payload = {**params, "echo": echo}
        try:
            response = await self.http_client.post(
                f"{http_url}/{action}",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            result = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.error("NapCat HTTP 动作调用失败，action=%s，错误=%s", action, exc)
            raise BusinessError("NapCat HTTP 动作调用失败", 50201) from exc
        if not isinstance(result, dict):
            raise BusinessError("NapCat HTTP 返回格式不正确", 50202)
        logger.info(
            "[OneBot Action] HTTP 返回 action=%s，结果=%s",
            action,
            _format_payload(result),
        )
        return result

    async def record_sent_group_message(
        self,
        bot_qq: str,
        group_id: str,
        message: Any,
        message_id: Any = None,
    ) -> None:
        """记录动作接口成功发送的机器人群消息。

        该记录用于补足 NapCat 未回传 ``message_sent`` 事件的连接模式，
        不改变动作接口本身的响应结果。
        """
        await self.chat_message_service.record_outgoing_group_message(
            bot_qq,
            group_id,
            message,
            message_id,
        )

    async def _match_rule(self, event: dict[str, Any]) -> AutoReplyRule | None:
        """使用独立数据库会话匹配事件规则。"""
        async with self.database.session_factory() as session:
            return await self.auto_reply_service.match_event(session, event)

    async def _record_animated_emoji_event(
        self,
        event: dict[str, Any],
        fallback_bot_qq: str,
        source: str,
    ) -> None:
        """记录入站动画表情，不影响后续自动回复处理。"""
        if self.animated_emoji_service is None:
            return
        try:
            bot_qq = str(event.get("self_id") or fallback_bot_qq or "").strip()
            _, http_url, http_token = await self._connection_settings(bot_qq)
            added = await self.animated_emoji_service.record_event(
                event, fallback_bot_qq, http_url, http_token
            )
            if added:
                logger.info(
                    "[%s] 动画表情已记录，机器人=%s，群=%s，新增=%s",
                    source,
                    event.get("self_id") or fallback_bot_qq,
                    event.get("group_id") or "",
                    added,
                )
        except Exception as exc:
            logger.error(
                "[%s] 动画表情记录失败，机器人=%s，消息=%s，错误=%s",
                source,
                event.get("self_id") or fallback_bot_qq or "未提供",
                event.get("message_id") or event.get("id") or "未提供",
                exc,
            )

    async def _check_message_policy(
        self,
        event: dict[str, Any],
        reverse_session: ReverseSession | None = None,
    ) -> tuple[ForbiddenWordRule | None, AiMessageIntent | None]:
        """统一执行违禁词匹配、AI 禁言审核和回复形式意图判断。

        接收消息后先查本地违禁词列表：命中即直接撤回并禁言，完全跳过 AI。
        只有未命中任何本地词、且配置了 mute_prompt 时，才交给 AI 语义复核，
        AI 判定禁言时把其返回的连续违规关键词写回列表，实现同样内容下次本地直接命中。
        """
        async with self.database.session_factory() as session:
            keyword_rule = await self.forbidden_word_service.match_event(session, event)
            prompt_rule = None
            if keyword_rule is None:
                prompt_rule = await self.forbidden_word_service.prompt_rule_for_event(
                    session, event
                )
        # 命中本地违禁词列表（含 AI 学到的词）：直接禁言，不再调用 AI。
        if keyword_rule is not None:
            return keyword_rule, None
        if prompt_rule is None:
            return None, None
        intent = await self._analyse_message_intent(
            event,
            prompt_rule,
            reverse_session,
        )
        if intent["mute"]:
            await self._learn_forbidden_keyword(prompt_rule, intent)
            return prompt_rule, intent
        return None, intent

    async def _learn_forbidden_keyword(
        self, rule: ForbiddenWordRule, intent: AiMessageIntent
    ) -> None:
        """把 AI 判定的连续违规关键词写回违禁词列表，实现命中一次后本地复用。"""
        keyword = str(intent.get("mute_keyword") or "").strip()
        if not keyword:
            return
        try:
            async with self.database.session_factory() as session:
                added = await self.forbidden_word_service.learn_keyword(
                    session, rule.id, keyword
                )
            if added:
                logger.info(
                    "[OneBot 违禁词学习] AI 判定命中并落库，机器人=%s，群=%s，规则=%s，关键词=%s",
                    rule.bot_qq,
                    rule.group_id or "全局",
                    rule.id,
                    keyword,
                )
        except Exception as exc:
            logger.error(
                "[OneBot 违禁词学习] 关键词落库失败，机器人=%s，规则=%s，关键词=%s，错误=%s",
                rule.bot_qq,
                rule.id,
                keyword,
                exc,
            )

    async def _match_forbidden_word(
        self, event: dict[str, Any]
    ) -> ForbiddenWordRule | None:
        """兼容原调用方式，仅返回统一消息策略中的禁言规则。"""
        rule, _ = await self._check_message_policy(event)
        return rule

    async def _ai_should_mute(
        self, event: dict[str, Any], rule: ForbiddenWordRule
    ) -> bool:
        """兼容原调用方式，从统一意图审核结果读取是否需要禁言。"""
        return bool((await self._analyse_message_intent(event, rule))["mute"])

    async def _analyse_message_intent(
        self,
        event: dict[str, Any],
        rule: ForbiddenWordRule | None,
        reverse_session: ReverseSession | None = None,
    ) -> AiMessageIntent:
        """结合当前消息和聊天历史生成严格禁言及回复形式意图 JSON。"""
        started_at = time.perf_counter()
        default = default_message_intent()
        bot_qq = str(event.get("self_id") or (rule.bot_qq if rule else "") or "")
        if event.get("post_type") != "message" or event.get("message_type") not in {
            "private",
            "group",
        }:
            return default
        sender = event.get("sender")
        sender = sender if isinstance(sender, dict) else {}
        user_id = str(event.get("user_id") or sender.get("user_id") or "")
        if not bot_qq or user_id == bot_qq:
            return default
        message_text = self._extract_ai_text(event).strip()[:5000]
        has_images = AiImageService.has_candidates(event)
        try:
            async with self.database.session_factory() as session:
                config = await self.bot_ai_service.active_runtime_config(session, bot_qq)
            if config is None:
                logger.info(
                    "[OneBot AI 意图] 机器人=%s 未开启 AI，跳过意图判断，群=%s，用户=%s",
                    bot_qq,
                    event.get("group_id") or "",
                    user_id,
                )
                return default
            if self._has_voice(event):
                if "_ai_voice_text" in event:
                    voice_text = str(event["_ai_voice_text"] or "")
                else:
                    voice_text = await self._fetch_voice_text(
                        event, bot_qq, reverse_session
                    )
                    event["_ai_voice_text"] = voice_text
                message_text = self._merge_voice_text(
                    message_text,
                    voice_text,
                )[:5000]
            images: list[AiImage] = []
            if has_images:
                _, http_url, http_token = await self._connection_settings(bot_qq)
                self.ai_image_service.http_client = self.http_client
                images = await self._collect_ai_images(
                    event, bot_qq, http_url, http_token
                )
                if not images:
                    logger.error(
                        "[OneBot AI 意图] 图片读取失败，将仅根据当前消息文字判断，%s",
                        self._trace(event, bot_qq),
                    )
            if not message_text and not images:
                return default
            emoji_categories: list[str] = []
            emoji_service = getattr(self, "animated_emoji_service", None)
            if event.get("message_type") == "group" and emoji_service is not None:
                async with self.database.session_factory() as session:
                    emoji_categories = await emoji_service.categories_for_bot(
                        session, bot_qq
                    )
            voice_available = bool(
                event.get("message_type") == "group"
                and event.get("group_id") is not None
                and config.get("voice_reply_enabled")
            )
            generation_available = self.bot_ai_service.has_image_generation_config(
                config
            )
            intent_config = (
                self.bot_ai_service.image_model_config(config)
                if images
                else dict(config)
            )
            intent_config["system_prompt"] = ""
            intent_config["_system_prompt_override"] = ""
            intent_config["temperature"] = 0.0
            intent_config["max_tokens"] = 2000
            intent_config["_require_complete_response"] = True
            intent_config["_disable_config_refresh"] = True
            prompt = build_message_intent_prompt(
                bot_qq=bot_qq,
                group_id=str(event.get("group_id") or ""),
                **sender_fields_from_event(event),
                forward_context=format_forward_context(event, self._forward_text),
                message_text=message_text,
                mute_prompt=str(rule.mute_prompt or "") if rule else "",
                emoji_categories=emoji_categories,
                voice_available=voice_available,
                image_generation_available=generation_available,
                has_images=bool(images),
                classification_prompt=str(
                    config.get("message_intent_prompt") or ""
                ),
                character_prompt=str(config.get("system_prompt") or ""),
            )
            reply = await self.bot_ai_service.generate(
                intent_config,
                prompt,
                images=images or None,
            )
            intent = parse_message_intent(
                reply,
                emoji_categories=emoji_categories,
                voice_available=voice_available,
                image_generation_available=generation_available,
            )
            if intent is None:
                logger.warning(
                    "[OneBot AI 意图] AI 首次返回不是严格 JSON，进行一次格式重试，机器人=%s，群=%s，用户=%s，返回=%s",
                    bot_qq,
                    event.get("group_id") or "",
                    user_id,
                    reply,
                )
                retry_prompt = (
                    f"{prompt}\n\n上一次返回无法通过严格 JSON 校验。"
                    "请减少内部分析，只返回一行完整 JSON；不得省略、截断、增加或重命名字段。"
                )
                reply = await self.bot_ai_service.generate(
                    intent_config,
                    retry_prompt,
                    images=images or None,
                )
                intent = parse_message_intent(
                    reply,
                    emoji_categories=emoji_categories,
                    voice_available=voice_available,
                    image_generation_available=generation_available,
                )
            if intent is None:
                logger.error(
                    "[OneBot AI 意图] AI 格式重试后仍未返回严格 JSON，使用默认结果，机器人=%s，群=%s，用户=%s，返回=%s",
                    bot_qq,
                    event.get("group_id") or "",
                    user_id,
                    reply,
                )
                return default
            if not rule or not str(rule.mute_prompt or "").strip():
                intent["mute"] = False
                intent["mute_reason"] = ""
                intent["mute_keyword"] = ""
            elif intent["mute"] and intent["mute_keyword"]:
                # 违规关键词必须是当前消息内的连续原文，否则不予学习，只保留禁言判定。
                if intent["mute_keyword"].casefold() not in message_text.casefold():
                    logger.warning(
                        "[OneBot AI 意图] AI 返回的违规关键词不在消息原文中，忽略学习，机器人=%s，群=%s，用户=%s，关键词=%s",
                        bot_qq,
                        event.get("group_id") or "",
                        user_id,
                        intent["mute_keyword"],
                    )
                    intent["mute_keyword"] = ""
            logger.info(
                "[OneBot AI 意图] 判断完成，机器人=%s，群=%s，用户=%s，是否禁言=%s，回复形式=%s，原因=%s",
                bot_qq,
                event.get("group_id") or "",
                user_id,
                intent["mute"],
                intent["reply_type"],
                intent["reason"],
            )
            logger.info(
                "[OneBot AI 意图] 处理耗时，机器人=%s，群=%s，总耗时=%.3f秒",
                bot_qq,
                event.get("group_id") or "",
                time.perf_counter() - started_at,
            )
            return intent
        except BusinessError as exc:
            logger.error(
                "[OneBot AI 意图] 判断失败，使用默认结果，机器人=%s，群=%s，用户=%s，错误=%s",
                bot_qq,
                event.get("group_id") or "",
                user_id,
                exc.message,
            )
        except Exception as exc:
            logger.exception(
                "[OneBot AI 意图] 判断异常，使用默认结果，机器人=%s，群=%s，用户=%s，错误=%s",
                bot_qq,
                event.get("group_id") or "",
                user_id,
                exc,
            )
        return default

    async def analyse_message_intent(
        self,
        event: dict[str, Any],
        reverse_session: ReverseSession | None = None,
    ) -> AiMessageIntent:
        """供主动回复等其他 AI 流程复用统一的消息意图分类。"""
        return await self._analyse_message_intent(event, None, reverse_session)

    @staticmethod
    def _parse_ai_mute_decision(reply: Any) -> bool | None:
        """解析 AI 的严格 JSON 或兼容性单值结果，无法确认时返回 None。"""
        text = str(reply or "").strip()
        if not text:
            return None
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            value = None
        if isinstance(value, dict):
            for key in ("mute", "need_mute", "should_mute", "需要禁言"):
                if key in value:
                    return OneBotService._parse_ai_boolean(value[key])
            return None
        normalized = text.casefold().strip(" 。.!！,，;；:：\"'`")
        if normalized in {"不需要禁言", "无需禁言", "不禁言", "否", "false", "no", "0"}:
            return False
        if normalized in {"需要禁言", "禁言", "是", "true", "yes", "1"}:
            return True
        return None

    @staticmethod
    def _parse_ai_boolean(value: Any) -> bool | None:
        """将 JSON 中的布尔值或常见字符串转换为三态结果。"""
        if isinstance(value, bool):
            return value
        normalized = str(value or "").casefold().strip()
        if normalized in {"true", "1", "yes", "是", "需要禁言", "禁言"}:
            return True
        if normalized in {"false", "0", "no", "否", "不需要禁言", "无需禁言", "不禁言"}:
            return False
        return None

    @staticmethod
    def _forbidden_ban_params(
        event: dict[str, Any], rule: ForbiddenWordRule
    ) -> dict[str, Any] | None:
        """构造 OneBot set_group_ban 所需的群号、用户和时长参数。"""
        group_id = event.get("group_id")
        user_id = event.get("user_id")
        sender = event.get("sender")
        if not user_id and isinstance(sender, dict):
            user_id = sender.get("user_id")
        if not group_id or not user_id:
            return None
        return {
            "group_id": group_id,
            "user_id": user_id,
            "duration": rule.mute_duration,
        }

    @staticmethod
    def _forbidden_delete_params(event: dict[str, Any]) -> dict[str, Any] | None:
        """构造 OneBot delete_msg 所需的违规消息参数。"""
        message_id = event.get("message_id")
        if message_id in (None, ""):
            return None
        return {"message_id": message_id}

    @staticmethod
    def _forbidden_rule_keywords(rule: ForbiddenWordRule) -> str:
        """读取违禁词规则的完整逗号列表，兼容历史逐词记录。"""
        return str(getattr(rule, "keywords", None) or rule.keyword)

    async def _handle_http_forbidden(
        self, event: dict[str, Any], rule: ForbiddenWordRule
    ) -> None:
        """先撤回 HTTP 上报的违规消息，再按规则开关执行禁言。"""
        delete_params = self._forbidden_delete_params(event)
        if delete_params is None:
            logger.error(
                "违禁词命中但事件缺少违规消息 ID，机器人=%s，群=%s，用户=%s",
                event.get("self_id"),
                event.get("group_id"),
                event.get("user_id"),
            )
        else:
            try:
                delete_response = await self.call_action(
                    "delete_msg",
                    delete_params,
                    self_id=str(event.get("self_id") or ""),
                )
                logger.info(
                    "[OneBot 撤回] HTTP 违规消息已请求撤回，机器人=%s，消息=%s，response=%s",
                    event.get("self_id"),
                    delete_params["message_id"],
                    _format_payload(delete_response),
                )
                if delete_response.get("status") != "ok":
                    logger.error(
                        "OneBot HTTP 撤回违规消息失败，机器人=%s，消息=%s，response=%s",
                        event.get("self_id"),
                        delete_params["message_id"],
                        _format_payload(delete_response),
                    )
            except Exception as exc:
                logger.error(
                    "OneBot HTTP 撤回违规消息异常，机器人=%s，消息=%s，错误=%s",
                    event.get("self_id"),
                    delete_params["message_id"],
                    exc,
                )
        if getattr(rule, "mute_enabled", True) is False:
            logger.info(
                "[OneBot 禁言] HTTP 命中违禁词但规则已关闭同步禁言，机器人=%s，关键词=%s",
                event.get("self_id"),
                self._forbidden_rule_keywords(rule),
            )
            return
        params = self._forbidden_ban_params(event, rule)
        if params is None:
            logger.error(
                "违禁词命中但事件缺少禁言目标，机器人=%s，群=%s，关键词=%s",
                event.get("self_id"),
                event.get("group_id"),
                self._forbidden_rule_keywords(rule),
            )
            return
        duration = await self.forbidden_mute_service.next_duration(
            event, str(event.get("self_id") or ""), rule.mute_duration
        )
        params["duration"] = duration
        try:
            response = await self.call_action(
                "set_group_ban",
                params,
                self_id=str(event.get("self_id") or ""),
            )
            logger.info(
                "[OneBot 禁言] HTTP 事件命中违禁词，机器人=%s，群=%s，用户=%s，关键词=%s，时长=%s秒，response=%s",
                event.get("self_id"),
                params["group_id"],
                params["user_id"],
                self._forbidden_rule_keywords(rule),
                duration,
                _format_payload(response),
            )
            if response.get("status") != "ok":
                logger.error("OneBot HTTP 禁言动作失败，response=%s", _format_payload(response))
                return
        except Exception as exc:
            logger.error(
                "OneBot HTTP 禁言动作异常，机器人=%s，群=%s，用户=%s，关键词=%s，错误=%s",
                event.get("self_id"),
                params["group_id"],
                params["user_id"],
                self._forbidden_rule_keywords(rule),
                exc,
            )

    async def _handle_websocket_forbidden(
        self,
        event: dict[str, Any],
        session: ReverseSession,
        rule: ForbiddenWordRule,
    ) -> None:
        """先撤回 WebSocket 违规消息，再按规则开关执行禁言。"""
        delete_params = self._forbidden_delete_params(event)
        if delete_params is None:
            logger.error(
                "违禁词命中但事件缺少违规消息 ID，机器人=%s，群=%s，用户=%s",
                session.self_id,
                event.get("group_id"),
                event.get("user_id"),
            )
        else:
            try:
                delete_response = await self.reverse_manager.call(
                    "delete_msg", delete_params, session=session
                )
                logger.info(
                    "[OneBot 撤回] WebSocket 违规消息已请求撤回，机器人=%s，消息=%s，response=%s",
                    session.self_id,
                    delete_params["message_id"],
                    _format_payload(delete_response),
                )
                if delete_response.get("status") != "ok":
                    logger.error(
                        "OneBot WebSocket 撤回违规消息失败，机器人=%s，消息=%s，response=%s",
                        session.self_id,
                        delete_params["message_id"],
                        _format_payload(delete_response),
                    )
            except Exception as exc:
                logger.error(
                    "OneBot WebSocket 撤回违规消息异常，机器人=%s，消息=%s，错误=%s",
                    session.self_id,
                    delete_params["message_id"],
                    exc,
                )
        if getattr(rule, "mute_enabled", True) is False:
            logger.info(
                "[OneBot 禁言] WebSocket 命中违禁词但规则已关闭同步禁言，机器人=%s，关键词=%s",
                session.self_id,
                self._forbidden_rule_keywords(rule),
            )
            return
        params = self._forbidden_ban_params(event, rule)
        if params is None:
            logger.error(
                "违禁词命中但事件缺少禁言目标，机器人=%s，群=%s，关键词=%s",
                session.self_id,
                event.get("group_id"),
                self._forbidden_rule_keywords(rule),
            )
            return
        duration = await self.forbidden_mute_service.next_duration(
            event, session.self_id, rule.mute_duration
        )
        params["duration"] = duration
        try:
            response = await self.reverse_manager.call(
                "set_group_ban", params, session=session
            )
            logger.info(
                "[OneBot 禁言] WebSocket 事件命中违禁词，机器人=%s，群=%s，用户=%s，关键词=%s，时长=%s秒，response=%s",
                session.self_id,
                params["group_id"],
                params["user_id"],
                self._forbidden_rule_keywords(rule),
                duration,
                _format_payload(response),
            )
            if response.get("status") != "ok":
                logger.error("OneBot WebSocket 禁言动作失败，response=%s", _format_payload(response))
                return
        except Exception as exc:
            logger.error(
                "OneBot WebSocket 禁言动作异常，机器人=%s，群=%s，用户=%s，关键词=%s，错误=%s",
                session.self_id,
                params["group_id"],
                params["user_id"],
                self._forbidden_rule_keywords(rule),
                exc,
            )

    @staticmethod
    def _is_friend_request(event: dict[str, Any]) -> bool:
        """判断事件是否为 OneBot 好友申请。"""
        return (
            event.get("post_type") == "request"
            and event.get("request_type") == "friend"
        )

    @staticmethod
    def _is_group_request(event: dict[str, Any]) -> bool:
        """判断事件是否为 OneBot 加群申请或邀请请求。"""
        return (
            event.get("post_type") == "request"
            and event.get("request_type") == "group"
        )

    @staticmethod
    def _friend_request_params(event: dict[str, Any]) -> dict[str, Any] | None:
        """构造同意好友申请所需的 flag 和 approve 参数。"""
        flag = str(event.get("flag") or "").strip()
        return {"flag": flag, "approve": True} if flag else None

    @staticmethod
    def _group_request_params(event: dict[str, Any]) -> dict[str, Any] | None:
        """构造同意加群申请所需的 flag、sub_type 和 approve 参数。"""
        flag = str(event.get("flag") or "").strip()
        sub_type = str(event.get("sub_type") or "add").strip().lower()
        if not flag or sub_type not in {"add", "invite"}:
            return None
        return {
            "flag": flag,
            "sub_type": sub_type,
            "approve": True,
            "reason": "",
        }

    async def _friend_auto_approve_enabled(self, bot_qq: str) -> bool:
        """读取机器人好友申请自动同意开关。"""
        if not bot_qq:
            return False
        async with self.database.session_factory() as session:
            value = await session.scalar(
                select(BotAccount.auto_approve_friend).where(
                    BotAccount.bot_qq == bot_qq,
                    BotAccount.is_deleted.is_(False),
                )
            )
        return bool(value)

    async def _group_request_auto_approve_enabled(self, bot_qq: str) -> bool:
        """读取机器人加群申请自动同意开关。"""
        if not bot_qq:
            return False
        async with self.database.session_factory() as session:
            value = await session.scalar(
                select(BotAccount.auto_approve_group_request).where(
                    BotAccount.bot_qq == bot_qq,
                    BotAccount.is_deleted.is_(False),
                )
            )
        return bool(value)

    async def _handle_friend_request(
        self, event: dict[str, Any], session: ReverseSession | None = None
    ) -> None:
        """按接入方式自动同意好友申请。"""
        bot_qq = str(event.get("self_id") or (session.self_id if session else ""))
        source = "OneBot WebSocket" if session else "OneBot HTTP"
        if not await self._friend_auto_approve_enabled(bot_qq):
            logger.info(
                "[%s] 好友申请自动同意未开启，机器人=%s，跳过处理",
                source,
                bot_qq or "未提供",
            )
            return
        params = self._friend_request_params(event)
        if params is None:
            logger.error(
                "[%s] 好友申请缺少 flag，机器人=%s，事件=%s",
                source,
                bot_qq or "未提供",
                _format_payload(event),
            )
            return
        try:
            response = (
                await self.reverse_manager.call(
                    "set_friend_add_request", params, session=session
                )
                if session
                else await self.call_action(
                    "set_friend_add_request", params, self_id=bot_qq
                )
            )
            logger.info(
                "[%s] 自动同意好友申请，机器人=%s，flag=%s，response=%s",
                source,
                bot_qq,
                params["flag"],
                _format_payload(response),
            )
            if response.get("status") != "ok" or response.get("retcode", 0) not in (0, None):
                logger.error(
                    "[%s] 自动同意好友申请失败，机器人=%s，response=%s",
                    source,
                    bot_qq,
                    _format_payload(response),
                )
        except Exception as exc:
            logger.error(
                "[%s] 自动同意好友申请异常，机器人=%s，错误=%s",
                source,
                bot_qq or "未提供",
                exc,
            )

    async def _handle_group_request(
        self, event: dict[str, Any], session: ReverseSession | None = None
    ) -> None:
        """按接入方式自动同意机器人收到的加群申请。"""
        bot_qq = str(event.get("self_id") or (session.self_id if session else ""))
        source = "OneBot WebSocket" if session else "OneBot HTTP"
        if not await self._group_request_auto_approve_enabled(bot_qq):
            logger.info(
                "[%s] 加群申请自动同意未开启，机器人=%s，跳过处理",
                source,
                bot_qq or "未提供",
            )
            return
        params = self._group_request_params(event)
        if params is None:
            logger.error(
                "[%s] 加群申请缺少 flag 或 sub_type 无效，机器人=%s，事件=%s",
                source,
                bot_qq or "未提供",
                _format_payload(event),
            )
            return
        try:
            response = (
                await self.reverse_manager.call(
                    "set_group_add_request", params, session=session
                )
                if session
                else await self.call_action(
                    "set_group_add_request", params, self_id=bot_qq
                )
            )
            logger.info(
                "[%s] 自动同意加群申请，机器人=%s，群=%s，用户=%s，flag=%s，sub_type=%s，response=%s",
                source,
                bot_qq,
                event.get("group_id") or "未提供",
                event.get("user_id") or "未提供",
                params["flag"],
                params["sub_type"],
                _format_payload(response),
            )
            if response.get("status") != "ok" or response.get("retcode", 0) not in (0, None):
                logger.error(
                    "[%s] 自动同意加群申请失败，机器人=%s，群=%s，response=%s",
                    source,
                    bot_qq,
                    event.get("group_id") or "未提供",
                    _format_payload(response),
                )
        except Exception as exc:
            logger.error(
                "[%s] 自动同意加群申请异常，机器人=%s，群=%s，错误=%s",
                source,
                bot_qq or "未提供",
                event.get("group_id") or "未提供",
                exc,
            )

    async def _welcome_decision(
        self, event: dict[str, Any]
    ) -> tuple[bool, WelcomeMessageRule | None]:
        """读取进群欢迎开关及当前群的精确/默认欢迎词。"""
        async with self.database.session_factory() as session:
            return await self.welcome_message_service.resolve_event(session, event)

    @staticmethod
    def _with_bot_qq(event: dict[str, Any], fallback_bot_qq: str) -> dict[str, Any]:
        """为 HTTP 缺少 self_id 的事件补上连接机器人 QQ。"""
        if event.get("self_id") or not fallback_bot_qq:
            return event
        return {**event, "self_id": fallback_bot_qq}

    async def _ensure_reply_claim(
        self,
        event: dict[str, Any],
        bot_qq: str,
        source: str,
    ) -> bool:
        """在确认当前流程需要回复时抢占消息级处理权。"""
        if event.get("_ai_reply_guard_claimed"):
            return True
        key = str(
            event.get("_ai_reply_guard_key")
            or self.ai_message_reply_guard.event_key(event, bot_qq)
            or ""
        )
        if not key:
            return True
        claim = await self.ai_message_reply_guard.claim(key)
        if claim is None:
            logger.info(
                "[%s] 消息已被其他回复流程占用，跳过重复回复，机器人=%s，消息=%s",
                source,
                bot_qq,
                event.get("message_id") or "无消息ID",
            )
            return False
        event["_ai_reply_guard_key"] = key
        event["_ai_reply_guard_claimed"] = True
        return True

    @staticmethod
    def _should_preclaim_message(event: dict[str, Any], bot_qq: str) -> bool:
        """判断是否能在耗时意图判断前确认实时回复优先级。"""
        if event.get("message_type") == "private":
            return True
        return bool(
            event.get("message_type") == "group"
            and mentions_bot(event, bot_qq)
        )

    async def _resolve_forward_content(
        self,
        event: dict[str, Any],
        bot_qq: str,
        session: ReverseSession | None = None,
    ) -> dict[str, Any]:
        """按需调用 get_forward_msg，将合并转发内容附加到事件副本。

        Args:
            event: OneBot 原始事件。
            bot_qq: 当前机器人 QQ，用于选择 HTTP 或 WebSocket 连接。
            session: 当前反向 WebSocket 会话；HTTP 事件传入 None。
        Returns:
            包含已成功读取转发内容的事件副本；读取失败时保留原事件。
        """
        forward_ids = self._forward_message_ids(event)
        if not forward_ids:
            return event

        contents: list[Any] = []
        text_parts: list[str] = []
        for message_id in forward_ids:
            try:
                response = (
                    await self.reverse_manager.call(
                        "get_forward_msg",
                        {"message_id": message_id},
                        session=session,
                    )
                    if session is not None
                    else await self.call_action(
                        "get_forward_msg",
                        {"message_id": message_id},
                        self_id=bot_qq,
                    )
                )
            except Exception as exc:
                logger.error(
                    "[OneBot 转发消息] 获取内容失败，机器人=%s，forward_id=%s，错误=%s",
                    bot_qq,
                    message_id,
                    exc,
                )
                continue

            if not isinstance(response, dict) or (
                response.get("status") not in (None, "ok")
                or response.get("retcode", 0) not in (0, None)
            ):
                logger.error(
                    "[OneBot 转发消息] 获取内容返回失败，机器人=%s，forward_id=%s，返回=%s",
                    bot_qq,
                    message_id,
                    _format_payload(response),
                )
                continue
            payload = self._forward_response_payload(response)
            if not payload:
                logger.error(
                    "[OneBot 转发消息] 获取内容为空，机器人=%s，forward_id=%s，返回=%s",
                    bot_qq,
                    message_id,
                    _format_payload(response),
                )
                continue
            contents.extend(payload)
            text = "\n".join(
                part
                for part in (self._forward_text(item) for item in payload)
                if part
            )
            if text:
                text_parts.append(text)

        if not contents:
            return {**event, "_forward_message_unavailable": True}
        resolved = {**event, "_forward_message_contents": contents}
        if text_parts:
            resolved["_forward_message_text"] = "\n".join(text_parts)[:5000]
        logger.info(
            "[OneBot 转发消息] 已读取内容，机器人=%s，forward_id=%s，文本长度=%s，节点数=%s",
            bot_qq,
            ",".join(forward_ids),
            len(resolved.get("_forward_message_text", "")),
            len(contents),
        )
        return resolved

    @staticmethod
    def _forward_message_ids(event: dict[str, Any]) -> list[str]:
        """提取消息段或 CQ 码中的合并转发 ID，并去重保序。"""
        message_ids: list[str] = []
        seen: set[str] = set()

        def add(value: Any) -> None:
            message_id = str(value or "").strip()
            if message_id and message_id not in seen:
                seen.add(message_id)
                message_ids.append(message_id)

        message = event.get("message")
        if isinstance(message, list):
            for segment in message:
                if not isinstance(segment, dict) or segment.get("type") != "forward":
                    continue
                data = segment.get("data")
                if isinstance(data, dict):
                    add(data.get("id") or data.get("message_id"))

        for value in (message, event.get("raw_message")):
            if not isinstance(value, str):
                continue
            for match in re.finditer(r"\[CQ:forward(?:,([^\]]+))?\]", value, re.IGNORECASE):
                for field in (match.group(1) or "").split(","):
                    key, separator, raw_value = field.partition("=")
                    if separator and key.strip().lower() in {"id", "message_id"}:
                        add(html.unescape(raw_value.strip()))
        return message_ids

    @staticmethod
    def _forward_response_payload(response: dict[str, Any]) -> list[Any]:
        """从 get_forward_msg 响应中兼容提取消息节点。"""
        data = response.get("data")
        if isinstance(data, list):
            return data
        if not isinstance(data, dict):
            return []
        for key in ("message", "messages", "nodes", "content"):
            value = data.get(key)
            if value is None:
                continue
            return value if isinstance(value, list) else [value]
        return []

    @classmethod
    def _forward_text(cls, value: Any) -> str:
        """递归提取转发节点中的文本段，不把发送者元数据当作消息内容。"""
        if isinstance(value, str):
            return re.sub(r"\[CQ:forward(?:,[^\]]+)?\]", "", value, flags=re.IGNORECASE).strip()
        if isinstance(value, list):
            return "".join(part for part in (cls._forward_text(item) for item in value) if part)
        if not isinstance(value, dict):
            return ""
        if value.get("type") == "text":
            data = value.get("data")
            if isinstance(data, dict):
                return str(data.get("text") or "")
            return str(data or "")
        parts: list[str] = []
        for key in ("message", "messages", "nodes", "content", "data"):
            if key in value:
                text = cls._forward_text(value[key])
                if text:
                    parts.append(text)
        return "".join(parts)

    async def _is_sender_blocked(self, event: dict[str, Any]) -> bool:
        """判断消息发送者是否已被该机器人屏蔽，屏蔽后应跳过全部处理。

        仅对私聊/群聊消息事件生效，好友请求、群请求等非消息事件不受影响。
        屏蔽维度为「机器人 QQ + 发送者 QQ」，因此同一被屏蔽用户在私聊和
        群聊中的消息都会被跳过。
        """
        if event.get("post_type") != "message":
            return False
        bot_qq = str(event.get("self_id") or "")
        sender = event.get("sender")
        sender = sender if isinstance(sender, dict) else {}
        user_id = str(event.get("user_id") or sender.get("user_id") or "")
        if not bot_qq or not user_id or user_id == bot_qq:
            return False
        # 直接查询好友屏蔽状态：onebot_service 与 friend_service 存在循环依赖，
        # 故此处按机器人 QQ + 发送者 QQ 读取 BotFriend，历史 NULL 视为未屏蔽。
        async with self.database.session_factory() as session:
            blocked = await session.scalar(
                select(BotFriend.id).where(
                    BotFriend.bot_qq == bot_qq,
                    BotFriend.friend_qq == user_id,
                    BotFriend.is_blocked.is_(True),
                    BotFriend.is_deleted.is_(False),
                )
            )
        return blocked is not None

    async def _event_allowed(self, event: dict[str, Any], source: str) -> bool:
        async with self.database.session_factory() as session:
            permission = await self.bot_service.event_permission(session, event)
        bot_enabled = permission["bot_enabled"]
        bot_status = {True: "已开启", False: "已关闭", None: "未配置"}.get(
            bot_enabled, "未知"
        )
        message_id = event.get("message_id")
        message_id = str(message_id) if message_id not in (None, "") else "无"
        logger.info("[%s] 机器人开关检查：机器人=%s，消息=%s，状态=%s，结果=%s", source, permission["bot_qq"] or "未提供", message_id, bot_status, "通过" if permission["allowed"] else "拦截")
        if bot_enabled is True and permission["group_id"] is not None:
            logger.info("[%s] 群开关检查：机器人=%s，消息=%s，群=%s，策略=%s，状态=%s，结果=%s", source, permission["bot_qq"], message_id, permission["group_id"], permission["group_mode"] or "未知", "未配置，默认开启" if not permission["group_configured"] else "已开启" if permission["group_enabled"] else "未开启", "通过" if permission["group_enabled"] else "拦截")
        return bool(permission["allowed"])

    async def _connection_settings(self, self_id: str) -> tuple[str, str, str]:
        """读取机器人自身的传输配置，HTTP 地址不再使用全局环境变量回退。"""
        if not self_id:
            return "auto", "", self.settings.napcat_token
        async with self.database.session_factory() as session:
            bot = await session.scalar(
                select(BotAccount).where(
                    BotAccount.bot_qq == self_id,
                    BotAccount.is_deleted.is_(False),
                )
            )
        if bot is None:
            return "auto", "", self.settings.napcat_token
        if not bot.is_enabled:
            raise BusinessError("机器人账号已停用", 40034)
        return (
            bot.connection_mode or "auto",
            (bot.http_url or "").rstrip("/"),
            bot.access_token or self.settings.napcat_token,
        )

    async def _bot_nickname(self, bot_qq: str) -> str:
        """读取当前机器人管理配置中的名称，用于群聊 AI 关键词触发。"""
        if not bot_qq or not getattr(self, "database", None):
            return ""
        try:
            async with self.database.session_factory() as session:
                nickname = await session.scalar(
                    select(BotAccount.nickname).where(
                        BotAccount.bot_qq == bot_qq,
                        BotAccount.is_deleted.is_(False),
                    )
                )
            return str(nickname or "").strip()
        except Exception as exc:
            logger.error(
                "读取机器人名称失败，机器人=%s，错误=%s",
                bot_qq,
                exc,
            )
            return ""

    async def _match_ai_trigger_keyword(
        self, event: dict[str, Any], bot_qq: str
    ) -> str | None:
        """查询消息是否命中当前机器人的 AI 触发关键词。"""
        service = getattr(self, "ai_trigger_keyword_service", None)
        if service is None:
            return None
        try:
            lookup_event = event if event.get("self_id") else {**event, "self_id": bot_qq}
            async with self.database.session_factory() as session:
                return await service.match_event(session, lookup_event)
        except Exception as exc:
            logger.error(
                "AI 触发关键词匹配失败，机器人=%s，群=%s，错误=%s",
                event.get("self_id"),
                event.get("group_id"),
                exc,
            )
            return None

    async def _is_ai_reply_all_enabled(
        self, event: dict[str, Any], bot_qq: str
    ) -> bool:
        """查询当前机器人是否要求群消息全部触发 AI。"""
        service = getattr(self, "ai_trigger_keyword_service", None)
        if service is None:
            return False
        try:
            lookup_event = event if event.get("self_id") else {**event, "self_id": bot_qq}
            async with self.database.session_factory() as session:
                return await service.is_reply_all_enabled(session, lookup_event)
        except Exception as exc:
            logger.error(
                "AI 回复全部配置读取失败，机器人=%s，群=%s，错误=%s",
                bot_qq,
                event.get("group_id"),
                exc,
            )
            return False

    async def _ai_reply_trigger_mode(
        self, event: dict[str, Any], bot_qq: str
    ) -> dict[str, Any]:
        """读取 AI 触发关键词配置，返回回复全部或随机回复模式。"""
        service = getattr(self, "ai_trigger_keyword_service", None)
        default = {
            "reply_all": False,
            "random_reply": False,
            "random_reply_probability": 50,
        }
        if service is None:
            return default
        try:
            lookup_event = event if event.get("self_id") else {**event, "self_id": bot_qq}
            async with self.database.session_factory() as session:
                return await service.get_reply_mode(session, lookup_event)
        except Exception as exc:
            logger.error(
                "AI 回复触发模式读取失败，机器人=%s，群=%s，错误=%s",
                bot_qq,
                event.get("group_id"),
                exc,
            )
            return default

    @classmethod
    def _parse_moderation_command(
        cls, event: dict[str, Any]
    ) -> tuple[str, list[str]] | None:
        """解析群消息中的艾特禁言或解除禁言命令。"""
        if event.get("message_type") != "group":
            return None
        command_text = re.sub(r"\s+", "", cls._extract_ai_text(event))
        text_targets: list[str] = []
        if command_text == "禁言":
            action = "ban"
        elif command_text == "解除禁言":
            action = "unban"
        else:
            matched = re.fullmatch(
                r"(?P<targets>\d+(?:[,，]\d+)*)(?P<command>解除禁言|禁言)",
                command_text,
            )
            if matched is None:
                return None
            action = "unban" if matched.group("command") == "解除禁言" else "ban"
            text_targets = re.split(r"[,，]", matched.group("targets"))
        if not action:
            return None
        bot_qq = str(event.get("self_id") or "")
        targets = [
            target
            for target in [*cls._extract_mentioned_users(event), *text_targets]
            if target != bot_qq
        ]
        return (action, targets) if targets else None

    @staticmethod
    def _extract_mentioned_users(event: dict[str, Any]) -> list[str]:
        """提取消息中的数字 QQ 艾特目标并去重。"""
        values: list[str] = []
        message = event.get("message")
        if isinstance(message, list):
            for segment in message:
                if not isinstance(segment, dict) or segment.get("type") != "at":
                    continue
                data = segment.get("data")
                if not isinstance(data, dict):
                    continue
                value = str(data.get("qq") or "").strip()
                if value and value.lower() != "all" and value.isdigit():
                    values.append(value)
        raw_messages = [
            value
            for value in (event.get("message"), event.get("raw_message"))
            if isinstance(value, str)
        ]
        for raw_message in raw_messages:
            for body in re.findall(r"\[CQ:at,([^\]]+)\]", raw_message):
                match = re.search(r"(?:^|,)qq=([^,]+)", body)
                value = str(match.group(1) if match else "").strip()
                if value and value.lower() != "all" and value.isdigit():
                    values.append(value)
        return list(dict.fromkeys(values))

    async def _is_moderation_admin(self, event: dict[str, Any], bot_qq: str) -> bool:
        """校验命令发言人是否为机器人配置的群管理命令管理员。"""
        sender = event.get("sender")
        sender = sender if isinstance(sender, dict) else {}
        sender_qq = str(event.get("user_id") or sender.get("user_id") or "")
        try:
            async with self.database.session_factory() as session:
                admin_qq = await self.bot_service.get_configured_admin_qq(
                    session, bot_qq
                )
        except Exception as exc:
            logger.error(
                "群禁言命令管理员校验失败，机器人=%s，发言人=%s，错误=%s",
                bot_qq,
                sender_qq,
                exc,
            )
            return False
        if not admin_qq:
            logger.warning("群禁言命令未配置管理员，机器人=%s", bot_qq)
            return False
        allowed = sender_qq == admin_qq
        if not allowed:
            logger.warning(
                "群禁言命令权限不足，机器人=%s，发言人=%s，配置管理员=%s",
                bot_qq,
                sender_qq,
                admin_qq,
            )
        return allowed

    @staticmethod
    def _moderation_action_success(response: Any) -> bool:
        """判断 OneBot 群管理动作是否成功。"""
        if not isinstance(response, dict) or response.get("status") != "ok":
            return False
        return response.get("retcode") in (None, 0, "0")

    async def _execute_moderation_command(
        self,
        event: dict[str, Any],
        bot_qq: str,
        command: tuple[str, list[str]],
        invoke: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> None:
        """执行管理员禁言命令并发送汇总结果。"""
        if not await self._is_moderation_admin(event, bot_qq):
            return
        action, targets = command
        duration = 0 if action == "unban" else 300
        action_name = "解除禁言" if action == "unban" else "禁言"
        succeeded: list[str] = []
        failed: list[str] = []
        group_id = event.get("group_id")
        for target in targets:
            params = {
                "group_id": group_id,
                "user_id": target,
                "duration": duration,
            }
            try:
                response = await invoke("set_group_ban", params)
                if self._moderation_action_success(response):
                    succeeded.append(target)
                else:
                    failed.append(target)
                    logger.error(
                        "群%s命令失败，机器人=%s，目标=%s，response=%s",
                        action_name,
                        bot_qq,
                        target,
                        _format_payload(response),
                    )
            except Exception as exc:
                failed.append(target)
                logger.error(
                    "群%s命令异常，机器人=%s，目标=%s，错误=%s",
                    action_name,
                    bot_qq,
                    target,
                    exc,
                )
        result_text = f"{action_name}成功：{', '.join(succeeded)}" if succeeded else ""
        if failed:
            failure_text = f"{action_name}失败：{', '.join(failed)}"
            result_text = f"{result_text}；{failure_text}" if result_text else failure_text
        if not result_text:
            result_text = f"{action_name}未执行"
        try:
            response = await invoke(
                "send_group_msg",
                {"group_id": group_id, "message": result_text},
            )
            if not self._moderation_action_success(response):
                logger.error(
                    "群%s结果通知发送失败，机器人=%s，群=%s，response=%s",
                    action_name,
                    bot_qq,
                    group_id,
                    _format_payload(response),
                )
        except Exception as exc:
            logger.error(
                "群%s结果通知发送异常，机器人=%s，群=%s，错误=%s",
                action_name,
                bot_qq,
                group_id,
                exc,
            )

    async def _handle_http_moderation_command(
        self, event: dict[str, Any], command: tuple[str, list[str]]
    ) -> None:
        """通过 HTTP 动作代理执行管理员群禁言命令。"""
        bot_qq = str(event.get("self_id") or "")

        async def invoke(action: str, params: dict[str, Any]) -> dict[str, Any]:
            return await self.call_action(action, params, self_id=bot_qq)

        await self._execute_moderation_command(event, bot_qq, command, invoke)

    async def _handle_websocket_moderation_command(
        self,
        event: dict[str, Any],
        session: ReverseSession,
        command: tuple[str, list[str]],
    ) -> None:
        """通过当前反向 WebSocket 连接执行管理员群禁言命令。"""
        async def invoke(action: str, params: dict[str, Any]) -> dict[str, Any]:
            return await self.reverse_manager.call(action, params, session=session)

        await self._execute_moderation_command(
            event, session.self_id, command, invoke
        )

    @staticmethod
    def _parse_title_command(event: dict[str, Any]) -> tuple[bool, str]:
        """解析专属头衔命令，并返回命令后直接指定的头衔内容。"""
        if event.get("message_type") != "group":
            return False, ""
        text = OneBotService._extract_ai_text(event)
        matched = re.match(r"^\s*#\s*头\s*衔(?P<suffix>.*)$", text, re.S)
        if matched is None:
            return False, ""
        return True, matched.group("suffix").strip()

    @staticmethod
    def _is_title_command(event: dict[str, Any]) -> bool:
        """判断群消息是否为专属头衔命令，允许命令后附加头衔内容。"""
        return OneBotService._parse_title_command(event)[0]

    @staticmethod
    def _sender_nickname(event: dict[str, Any]) -> str:
        """提取发送者昵称，优先昵称，其次群名片，最后使用 QQ 号。"""
        sender = event.get("sender")
        sender = sender if isinstance(sender, dict) else {}
        for value in (sender.get("nickname"), sender.get("card")):
            nickname = str(value or "").strip()
            if nickname:
                return nickname
        return str(event.get("user_id") or sender.get("user_id") or "").strip()

    @staticmethod
    def _clean_special_title(value: Any) -> str:
        """清理 AI 头衔输出并限制为最多四个汉字。"""
        text = str(value or "")
        text = re.sub(r'[\s\r\n"\'“”‘’`*_#]+', "", text)
        text = re.sub(r"[^\u3400-\u9fff]", "", text)
        return text[:4]

    @staticmethod
    def _clean_explicit_title(value: str) -> str:
        """清理命令直接指定的头衔，仅移除首尾空白和换行。"""
        return re.sub(r"[\r\n]+", " ", str(value or "")).strip()

    async def _generate_special_title(
        self, event: dict[str, Any], direct_title: str | None = None
    ) -> str | None:
        """生成头衔；命令已指定内容时直接使用，不调用 AI。"""
        if direct_title is None:
            _, parsed_title = self._parse_title_command(event)
            if parsed_title:
                direct_title = parsed_title
        bot_qq = str(event.get("self_id") or "")
        group_id = event.get("group_id")
        user_id = str(event.get("user_id") or "")
        sender = event.get("sender")
        if isinstance(sender, dict):
            user_id = user_id or str(sender.get("user_id") or "")
        nickname = self._sender_nickname(event)
        if not bot_qq or group_id is None or not user_id:
            logger.error(
                "专属头衔命令缺少必要目标，机器人=%s，群=%s，用户=%s",
                bot_qq or "未提供",
                group_id,
                user_id or "未提供",
            )
            return None
        if user_id == bot_qq:
            logger.info("专属头衔命令来自机器人自身，跳过处理，机器人=%s", bot_qq)
            return None
        if direct_title is not None:
            title = self._clean_explicit_title(direct_title)
            if not title:
                logger.error(
                    "专属头衔命令指定内容为空，机器人=%s，群=%s，用户=%s",
                    bot_qq,
                    group_id,
                    user_id,
                )
                return None
            logger.info(
                "使用命令直接指定的专属头衔，机器人=%s，群=%s，用户=%s，头衔=%s",
                bot_qq,
                group_id,
                user_id,
                title,
            )
            return title
        if not nickname:
            logger.error(
                "专属头衔命令缺少发送者昵称，机器人=%s，群=%s，用户=%s",
                bot_qq,
                group_id,
                user_id,
            )
            return None
        try:
            async with self.database.session_factory() as session:
                config = await self.bot_ai_service.active_runtime_config(session, bot_qq)
            if config is None:
                logger.error(
                    "专属头衔生成失败，机器人=%s 未启用 AI 或尚未配置", bot_qq
                )
                return None
            prompt = (
                f"用户昵称：{nickname}\n"
                "请为这个用户生成一个专属群头衔。"
                "必须只返回头衔正文，长度不超过4个汉字（即4个汉字以内），只能使用汉字，不要解释、标点、英文、数字或换行。"
            )
            reply = await self.bot_ai_service.generate(config, prompt)
            title = self._clean_special_title(reply)
            if not title:
                logger.error(
                    "专属头衔生成失败，AI 返回空内容，机器人=%s，群=%s，用户=%s",
                    bot_qq,
                    group_id,
                    user_id,
                )
                return None
            logger.info(
                "专属头衔已生成，机器人=%s，群=%s，用户=%s，昵称=%s，头衔=%s",
                bot_qq,
                group_id,
                user_id,
                nickname,
                title,
            )
            return title
        except BusinessError as exc:
            logger.error(
                "专属头衔 AI 处理失败，机器人=%s，群=%s，用户=%s，错误=%s",
                bot_qq,
                group_id,
                user_id,
                exc.message,
            )
        except Exception as exc:
            logger.exception(
                "专属头衔 AI 处理异常，机器人=%s，群=%s，用户=%s，错误=%s",
                bot_qq,
                group_id,
                user_id,
                exc,
            )
        return None

    async def _handle_http_special_title(self, event: dict[str, Any]) -> None:
        """处理 HTTP 群消息的专属头衔命令并调用 OneBot 动作。"""
        _, direct_title = self._parse_title_command(event)
        title = await self._generate_special_title(
            event, direct_title if direct_title else None
        )
        if not title:
            return
        sender = event.get("sender")
        sender = sender if isinstance(sender, dict) else {}
        params = {
            "group_id": event["group_id"],
            "user_id": event.get("user_id")
            or sender.get("user_id"),
            "special_title": title,
        }
        if not params["user_id"]:
            logger.error("专属头衔设置失败，事件缺少用户 QQ，机器人=%s", event.get("self_id"))
            return
        try:
            logger.info(
                "[OneBot HTTP] 发送设置专属头衔动作，机器人=%s，参数=%s",
                event.get("self_id"),
                _format_payload(params),
            )
            response = await self.call_action(
                "set_group_special_title",
                params,
                self_id=str(event.get("self_id") or ""),
            )
            logger.info(
                "[OneBot HTTP] 设置专属头衔动作返回，机器人=%s，response=%s",
                event.get("self_id"),
                _format_payload(response),
            )
            if response.get("status") != "ok" or response.get("retcode", 0) not in (0, None):
                logger.error(
                    "OneBot HTTP 设置专属头衔失败，机器人=%s，群=%s，用户=%s，response=%s",
                    event.get("self_id"),
                    params["group_id"],
                    params["user_id"],
                    _format_payload(response),
                )
        except Exception as exc:
            logger.error(
                "OneBot HTTP 设置专属头衔异常，机器人=%s，群=%s，用户=%s，错误=%s",
                event.get("self_id"),
                params["group_id"],
                params["user_id"],
                exc,
            )

    async def _handle_websocket_special_title(
        self, event: dict[str, Any], session: ReverseSession
    ) -> None:
        """处理 WebSocket 群消息的专属头衔命令并调用当前连接动作。"""
        _, direct_title = self._parse_title_command(event)
        title = await self._generate_special_title(
            event, direct_title if direct_title else None
        )
        if not title:
            return
        sender = event.get("sender")
        sender = sender if isinstance(sender, dict) else {}
        user_id = event.get("user_id") or sender.get("user_id")
        if event.get("group_id") is None or not user_id:
            logger.error(
                "专属头衔设置失败，事件缺少群号或用户 QQ，机器人=%s",
                session.self_id,
            )
            return
        params = {
            "group_id": event["group_id"],
            "user_id": user_id,
            "special_title": title,
        }
        try:
            logger.info(
                "[OneBot WebSocket] 发送设置专属头衔动作，机器人=%s，参数=%s",
                session.self_id,
                _format_payload(params),
            )
            response = await self.reverse_manager.call(
                "set_group_special_title", params, session=session
            )
            logger.info(
                "[OneBot WebSocket] 设置专属头衔动作返回，机器人=%s，response=%s",
                session.self_id,
                _format_payload(response),
            )
            if response.get("status") != "ok" or response.get("retcode", 0) not in (0, None):
                logger.error(
                    "OneBot WebSocket 设置专属头衔失败，机器人=%s，群=%s，用户=%s，response=%s",
                    session.self_id,
                    params["group_id"],
                    params["user_id"],
                    _format_payload(response),
                )
        except Exception as exc:
            logger.error(
                "OneBot WebSocket 设置专属头衔异常，机器人=%s，群=%s，用户=%s，错误=%s",
                session.self_id,
                params["group_id"],
                params["user_id"],
                exc,
            )

    async def _generate_ai_reply(
        self,
        event: dict[str, Any],
        fallback_bot_qq: str = "",
        session: ReverseSession | None = None,
        defer_bookkeeping: bool = False,
        intent: AiMessageIntent | None = None,
    ) -> tuple[str, bool] | None:
        """检查机器人 AI 配置和触发方式，并生成回复及免触发状态。"""
        bot_qq = str(event.get("self_id") or fallback_bot_qq or "")
        if not bot_qq or (
            event.get("post_type") != "message"
            and not is_poke_event(event, bot_qq)
        ):
            return None
        sender = event.get("sender")
        sender = sender if isinstance(sender, dict) else {}
        user_id = str(event.get("user_id") or sender.get("user_id") or "")
        if user_id == bot_qq:
            return None
        started_at = time.perf_counter()
        try:
            async with self.database.session_factory() as db_session:
                config = await self.bot_ai_service.active_runtime_config(
                    db_session, bot_qq
                )
            if config is None:
                logger.info(
                    "[OneBot AI] AI 开关未开启或尚未配置，跳过 AI 回复，%s",
                    self._trace(event, bot_qq),
                )
                return None
            logger.info(
                "[OneBot AI] AI 开关已开启，继续检查触发条件，%s",
                self._trace(event, bot_qq),
            )
            prompt = await self._prepare_ai_prompt(
                event,
                bot_qq,
                session,
                int(config.get("followup_count", 3)),
            )
            if prompt is None:
                logger.info(
                    "[OneBot AI] 消息不符合触发条件或没有文本，不触发 AI，%s",
                    self._trace(event, bot_qq),
                )
                return None
            if not await self._ensure_reply_claim(event, bot_qq, "OneBot AI"):
                return None
            user_text, quoted_text, followup_used, images = prompt
            if intent is None:
                intent = await self._analyse_message_intent(event, None, session)
            event["_ai_message_intent"] = intent
            if should_skip_reply(intent):
                # AI 判定本条消息不需要机器人参与，直接结束且不生成、不发送任何内容。
                logger.info(
                    "[OneBot AI] 机器人=%s，群=%s，用户=%s，意图判定为不回复，跳过本次回复，原因=%s",
                    bot_qq,
                    event.get("group_id") or "",
                    user_id,
                    intent["reason"] or "未提供",
                )
                # 标记消息已由即时 AI 处理完成，避免主动回复等流程再次回复；
                # 本次没有真正回复，因此不再续期后续免触发额度。
                await self._finish_ai_bookkeeping(
                    event,
                    bot_qq,
                    int(config.get("followup_count", 3)),
                    True,
                )
                return None
            prompt_prepared_at = time.perf_counter()
            history = await self.chat_message_service.get_ai_history(
                event,
                bot_qq,
                limit=int(config.get("history_limit", 10) or 0),
            )
            history_loaded_at = time.perf_counter()
            generation_config = (
                self.bot_ai_service.image_model_config(config)
                if images
                else config
            )
            current_text = format_message_with_sender(user_text, **sender_fields_from_event(event))
            forward_context = format_forward_context(event, self._forward_text)
            if forward_context:
                current_text = f"{current_text}\n\n{forward_context}"
            reply = await self.bot_ai_service.generate(
                generation_config,
                current_text,
                quoted_text,
                images,
                history,
                flatten_history=event.get("message_type") == "group",
            )
            generated_at = time.perf_counter()
            if defer_bookkeeping:
                task = asyncio.create_task(
                    self._finish_ai_bookkeeping(
                        event,
                        bot_qq,
                        int(config.get("followup_count", 3)),
                        followup_used,
                    )
                )
                self._reply_tasks.add(task)
                task.add_done_callback(self._reply_tasks.discard)
            else:
                await self._finish_ai_bookkeeping(
                    event,
                    bot_qq,
                    int(config.get("followup_count", 3)),
                    followup_used,
                )
            logger.info(
                "[OneBot AI] 机器人=%s，用户内容=%s，引用内容=%s，图片数=%s，AI 返回=%s",
                bot_qq,
                user_text,
                quoted_text,
                len(images),
                reply,
            )
            logger.info(
                "[OneBot AI] 机器人=%s，历史聊天记录数=%s",
                bot_qq,
                len(history),
            )
            logger.info(
                "[OneBot AI] 处理耗时，机器人=%s，触发准备=%.3f秒，历史读取=%.3f秒，模型生成=%.3f秒，总耗时=%.3f秒",
                bot_qq,
                prompt_prepared_at - started_at,
                history_loaded_at - prompt_prepared_at,
                generated_at - history_loaded_at,
                generated_at - started_at,
            )
            return reply, followup_used
        except BusinessError as exc:
            logger.error(
                "AI 回复处理失败，机器人=%s，错误=%s",
                bot_qq,
                exc.message,
            )
            return None
        except Exception as exc:
            logger.exception(
                "AI 回复处理失败，机器人=%s，事件=%s，错误=%s",
                bot_qq,
                _format_payload(event),
                exc,
            )
            return None

    async def _finish_ai_bookkeeping(
        self,
        event: dict[str, Any],
        bot_qq: str,
        followup_count: int,
        followup_used: bool,
    ) -> None:
        """完成即时 AI 的消息标记和免触发额度写入。"""
        try:
            await self.chat_message_service.mark_ai_triggered(event, bot_qq)
        except Exception:
            logger.exception(
                "即时 AI 已生成，但消息处理标记写入失败，机器人=%s，消息=%s",
                bot_qq,
                event.get("message_id") or "无消息ID",
            )
        if not followup_used:
            try:
                await self.ai_followup_service.activate(
                    event,
                    bot_qq,
                    followup_count,
                )
            except Exception:
                logger.exception(
                    "即时 AI 已生成，但后续免触发状态写入失败，机器人=%s，群=%s，用户=%s",
                    bot_qq,
                    event.get("group_id") or "",
                    event.get("user_id") or "",
                )
    async def _classify_reply_category(
        self,
        config: dict[str, Any],
        reply: str,
        categories: list[str],
        bot_qq: str = "",
    ) -> str:
        """让当前机器人根据 AI 回复文本选择一个已有动画表情分类。"""
        bot_ai_service = getattr(self, "bot_ai_service", None)
        if bot_ai_service is None or not categories:
            return ""
        classifier_config = dict(config)
        classifier_config["system_prompt"] = ""
        classifier_config["_system_prompt_override"] = ""
        classifier_config["temperature"] = 0
        try:
            configured_max_tokens = int(classifier_config.get("max_tokens") or 200)
        except (TypeError, ValueError):
            configured_max_tokens = 200
        classifier_config["max_tokens"] = max(50, min(configured_max_tokens, 200))
        category_text = "、".join(categories)
        prompt = (
            "你是QQ群动画表情分类选择器。请根据下面的AI回复语义，"
            "从候选分类中选择最匹配的一项。只能选择候选分类中的原文，"
            "不得创建新分类或凭空猜测。必须严格返回JSON，不要Markdown、解释或其他文字。"
            f"候选分类：{category_text}\n"
            f"AI回复：{str(reply or '').strip()}\n"
            '匹配明确时返回：{"success":true,"category":"候选分类"}；'
            '无法明确匹配时返回：{"success":false,"category":"","reason":"无法明确匹配"}。'
        )
        try:
            result = await bot_ai_service.generate(classifier_config, prompt)
        except Exception as exc:
            logger.warning(
                "[OneBot AI 表情分类] 分类调用失败，机器人=%s，错误=%s",
                bot_qq or config.get("bot_qq", "") or "未知",
                exc,
            )
            return ""
        category = normalise_ai_category(result)
        if not category:
            logger.info("[OneBot AI 表情分类] AI 未返回明确分类，原始返回=%s", result)
        return category

    async def _resolve_reply_image(
        self,
        config: dict[str, Any] | None,
        reply: str,
        bot_qq: str,
        event: dict[str, Any],
    ) -> dict[str, str] | None:
        """按回复语义随机选择一张当前机器人已有分类的 Base64 动画表情。"""
        if event.get("message_type") != "group" or event.get("group_id") is None:
            return None
        emoji_service = getattr(self, "animated_emoji_service", None)
        database = getattr(self, "database", None)
        if emoji_service is None or database is None or not config or not bot_qq:
            return None
        try:
            async with database.session_factory() as session:
                categories = await emoji_service.categories_for_bot(session, bot_qq)
            if not categories:
                return None
            category = await self._classify_reply_category(
                config, reply, categories, bot_qq
            )
            if not category:
                return None
            async with database.session_factory() as session:
                image = await emoji_service.random_image_for_category(
                    session, bot_qq, category
                )
            if image is None:
                logger.info(
                    "[OneBot AI 表情分类] 没有可发送的 Base64 图片，机器人=%s，分类=%s",
                    bot_qq,
                    category,
                )
                return None
            logger.info(
                "[OneBot AI 表情分类] 已选择随机图片，机器人=%s，分类=%s，记录=%s",
                bot_qq,
                category,
                image.get("id", ""),
            )
            return image
        except Exception as exc:
            logger.warning(
                "[OneBot AI 表情分类] 选择图片失败，机器人=%s，错误=%s",
                bot_qq,
                exc,
            )
            return None

    async def _resolve_intent_reply_image(
        self,
        config: dict[str, Any] | None,
        intent: AiMessageIntent,
        bot_qq: str,
        event: dict[str, Any],
    ) -> dict[str, str] | None:
        """根据严格意图结果选择已有表情或调用文生图模型。"""
        reply_type = intent["reply_type"]
        try:
            if reply_type == "emoji":
                if event.get("message_type") != "group":
                    return None
                emoji_service = getattr(self, "animated_emoji_service", None)
                if emoji_service is None:
                    return None
                async with self.database.session_factory() as session:
                    image = await emoji_service.random_image_for_category(
                        session,
                        bot_qq,
                        intent["emoji_category"],
                    )
                if image is None:
                    logger.warning(
                        "[OneBot AI 意图] 指定表情分类没有可用图片，机器人=%s，分类=%s",
                        bot_qq,
                        intent["emoji_category"],
                    )
                return image
            if reply_type == "generate_image":
                if not config:
                    return None
                image = await self.bot_ai_service.generate_image(
                    config,
                    intent["generate_words"],
                )
                logger.info(
                    "[OneBot AI 意图] 文生图完成，机器人=%s，群=%s，提示词=%s",
                    bot_qq,
                    event.get("group_id") or "私聊",
                    intent["generate_words"],
                )
                return image
        except BusinessError as exc:
            logger.error(
                "[OneBot AI 意图] 图片处理失败，将回退文本，机器人=%s，意图=%s，错误=%s",
                bot_qq,
                reply_type,
                exc.message,
            )
        except Exception as exc:
            logger.exception(
                "[OneBot AI 意图] 图片处理异常，将回退文本，机器人=%s，意图=%s，错误=%s",
                bot_qq,
                reply_type,
                exc,
            )
        return None

    async def _send_reply_image(
        self,
        event: dict[str, Any],
        bot_qq: str,
        image: dict[str, str],
        session: ReverseSession | None = None,
    ) -> dict[str, Any]:
        """发送一条不引用原消息的 Base64 动画表情消息。"""
        action_request = self._build_reply_action(
            event,
            "",
            bot_qq,
            suppress_quote=True,
            image=image,
        )
        if action_request is None:
            return onebot_error("当前事件类型不支持动画表情回复", 1402)
        action, params = action_request
        try:
            response = (
                await self.reverse_manager.call(action, params, session=session)
                if session is not None
                else await self.call_action(action, params, self_id=bot_qq)
            )
        except Exception as exc:
            logger.error(
                "AI 动画表情发送异常，机器人=%s，群=%s，错误=%s",
                bot_qq,
                event.get("group_id"),
                exc,
            )
            return onebot_error("AI 动画表情发送失败", 1403)
        if response.get("status") != "ok":
            logger.error(
                "AI 动画表情发送失败，机器人=%s，群=%s，response=%s",
                bot_qq,
                event.get("group_id"),
                _format_payload(response),
            )
        return response

    async def _generate_welcome_ai_reply(
        self, event: dict[str, Any]
    ) -> str | None:
        """没有配置欢迎词时，让当前机器人的 AI 生成简短欢迎正文。"""
        bot_qq = str(event.get("self_id") or "")
        user_id = str(event.get("user_id") or "")
        group_id = str(event.get("group_id") or "")
        if not bot_qq or not user_id or not group_id:
            return None
        try:
            async with self.database.session_factory() as session:
                config = await self.bot_ai_service.active_runtime_config(
                    session, bot_qq
                )
            if config is None:
                logger.info(
                    "[OneBot Welcome AI] 机器人=%s，AI 未开启，未生成欢迎词", bot_qq
                )
                return None
            prompt = (
                f"QQ群 {group_id} 有新成员 QQ {user_id} 加入。"
                "请生成一段简短、友好、自然的中文进群欢迎词。"
                "只返回欢迎词正文，不要添加@、CQ码、标题或解释。"
            )
            reply = await self.bot_ai_service.generate(config, prompt)
            logger.info(
                "[OneBot Welcome AI] 机器人=%s，群=%s，新成员=%s，AI 返回=%s",
                bot_qq,
                group_id,
                user_id,
                reply,
            )
            return reply
        except BusinessError as exc:
            logger.error(
                "进群欢迎 AI 处理失败，机器人=%s，错误=%s", bot_qq, exc.message
            )
            return None
        except Exception as exc:
            logger.exception(
                "进群欢迎 AI 处理异常，机器人=%s，事件=%s，错误=%s",
                bot_qq,
                _format_payload(event),
                exc,
            )
            return None

    @staticmethod
    def _build_http_welcome_response(
        event: dict[str, Any], content: str | None
    ) -> dict[str, Any] | None:
        """构造 HTTP 快速回复，并用 CQ at 提及新成员。"""
        user_id = str(event.get("user_id") or "")
        if not user_id or not content:
            return None
        return {"reply": f"[CQ:at,qq={user_id}] {content}"}

    async def _handle_websocket_welcome(
        self,
        event: dict[str, Any],
        session: ReverseSession,
        rule: WelcomeMessageRule | None,
    ) -> None:
        """异步发送配置或 AI 生成的进群欢迎词，固定 @ 新成员。"""
        content = rule.welcome_content if rule else None
        if not content:
            content = await self._generate_welcome_ai_reply(event)
        user_id = str(event.get("user_id") or "")
        group_id = event.get("group_id")
        if not content or not user_id or group_id is None:
            logger.info("[OneBot WebSocket] 进群欢迎缺少内容或目标，未发送回复")
            return
        params = {
            "group_id": group_id,
            "message": [
                {"type": "at", "data": {"qq": user_id}},
                {"type": "text", "data": {"text": f" {content}"}},
            ],
        }
        try:
            response = await self.reverse_manager.call(
                "send_group_msg", params, session=session
            )
            logger.info(
                "[OneBot WebSocket] 进群欢迎动作返回，response：%s",
                _format_payload(response),
            )
            if response.get("status") != "ok":
                logger.error("进群欢迎发送失败，response=%s", response)
        except Exception as exc:
            logger.error("进群欢迎发送异常，错误=%s", exc)

    async def _prepare_ai_prompt(
        self,
        event: dict[str, Any],
        bot_qq: str,
        session: ReverseSession | None,
        followup_count: int,
    ) -> tuple[str, str, bool, list[AiImage]] | None:
        """提取文本、语音和图片，并校验群聊的 @、引用和戳一戳触发条件。"""
        if is_poke_event(event, bot_qq):
            return "有人戳了戳你，请回复。", "", False, []
        message_type = event.get("message_type")
        if message_type not in {"private", "group"}:
            return None
        text = self._extract_ai_text(event)
        has_voice = self._has_voice(event)
        has_image_candidates = self.ai_image_service.has_candidates(event)
        if message_type == "private":
            if not text and not has_image_candidates and not has_voice:
                return None
            voice_text = ""
            if has_voice:
                voice_text = (
                    str(event["_ai_voice_text"] or "")
                    if "_ai_voice_text" in event
                    else await self._fetch_voice_text(event, bot_qq, session)
                )
                event["_ai_voice_text"] = voice_text
                text = self._merge_voice_text(
                    text,
                    voice_text,
                )
            _, http_url, http_token = await self._connection_settings(bot_qq)
            self.ai_image_service.http_client = self.http_client
            images = await self._collect_ai_images(event, bot_qq, http_url, http_token)
            if has_voice and not voice_text and not text and not images:
                logger.info(
                    "[OneBot AI] 私聊语音转写为空且没有其他有效内容，跳过 AI，消息=%s",
                    event.get("message_id") or "无消息ID",
                )
                return None
            if not text and not images:
                return None
            if not text:
                text = "请分析图片内容。"
            quoted_text = ""
            quote_id = reply_message_id(event)
            if quote_id:
                quoted_is_bot, quoted_text = await self._quoted_bot_message(quote_id, bot_qq, session)
                text, quoted_text = merge_quoted_message(text, quoted_text, quoted_is_bot)
            return text, quoted_text, False, images

        mentioned = mentions_bot(event, bot_qq)
        if mentioned:
            logger.info(
                "[OneBot AI] @机器人触发命中，机器人=%s，群=%s，用户=%s，消息=%s",
                bot_qq,
                event.get("group_id") or "",
                event.get("user_id") or "",
                event.get("message_id") or "无消息ID",
            )
        bot_nickname = await self._bot_nickname(bot_qq) if text else ""
        name_triggered = bool(
            bot_nickname and bot_nickname.casefold() in text.casefold()
        )
        if name_triggered:
            logger.info(
                "[OneBot AI] 机器人名称关键词命中，机器人=%s，名称=%s，群=%s，用户=%s",
                bot_qq,
                bot_nickname,
                event.get("group_id"),
                event.get("user_id"),
            )
        trigger_mode = await self._ai_reply_trigger_mode(event, bot_qq)
        reply_all_triggered = bool(trigger_mode.get("reply_all"))
        random_reply_mode = bool(trigger_mode.get("random_reply")) and not reply_all_triggered
        random_reply_triggered = False
        if random_reply_mode:
            try:
                probability = max(
                    0,
                    min(100, int(trigger_mode.get("random_reply_probability", 50))),
                )
            except (TypeError, ValueError):
                probability = 50
            random_reply_triggered = random.random() < probability / 100
            logger.info(
                "[OneBot AI] AI 随机回复检查，机器人=%s，群=%s，概率=%s%%，结果=%s",
                bot_qq,
                event.get("group_id"),
                probability,
                "命中" if random_reply_triggered else "未命中",
            )
        if reply_all_triggered:
            logger.info(
                "[OneBot AI] AI 回复全部已开启，机器人=%s，群=%s，用户=%s",
                bot_qq,
                event.get("group_id"),
                event.get("user_id"),
            )
        keyword_match = (
            await self._match_ai_trigger_keyword(event, bot_qq) if text else None
        )
        # 随机回复模式以概率决定普通消息是否触发，不再让关键词绕过概率。
        keyword_triggered = bool(keyword_match) and not random_reply_mode
        if keyword_triggered:
            logger.info(
                "[OneBot AI] AI 触发关键词命中，机器人=%s，关键词=%s，群=%s，用户=%s",
                bot_qq,
                keyword_match,
                event.get("group_id"),
                event.get("user_id"),
            )
        reply_id = reply_message_id(event)
        quoted_text = ""
        quoted_is_bot = False
        if reply_id:
            quoted_is_bot, quoted_text = await self._quoted_bot_message(
                reply_id, bot_qq, session
            )
        if (
            not text
            and not has_image_candidates
            and not has_voice
            and not quoted_text
            and not mentioned
            and not name_triggered
            and not keyword_triggered
            and not reply_all_triggered
            and not random_reply_triggered
        ):
            logger.info(
                "[OneBot AI] 消息无有效内容且未命中任何触发条件，不触发 AI，%s",
                self._trace(event, bot_qq),
            )
            return None
        # 先尝试消费后续免触发额度：随机模式概率未命中时也允许凭额度触发，
        # 这样开启随机回复的机器人在一次真正触发后仍能延续会话。
        followup_used = False
        if (
            not mentioned
            and not quoted_is_bot
            and not name_triggered
            and not keyword_triggered
            and not reply_all_triggered
            and not random_reply_triggered
        ):
            followup_used = await self.ai_followup_service.consume(
                event,
                bot_qq,
                followup_count,
            )
        if (
            random_reply_mode
            and not random_reply_triggered
            and not mentioned
            and not quoted_is_bot
            and not name_triggered
            and not followup_used
        ):
            # 随机模式未命中且没有剩余免触发额度时，不触发 AI，也不绕过随机概率。
            logger.info(
                "[OneBot AI] 随机回复模式未命中且无后续免触发额度，不触发 AI，%s",
                self._trace(event, bot_qq),
            )
            return None
        if (
            not mentioned
            and not quoted_is_bot
            and not name_triggered
            and not keyword_triggered
            and not reply_all_triggered
            and not random_reply_triggered
            and not followup_used
        ):
            logger.info(
                "[OneBot AI] 普通群消息未命中触发条件且无后续免触发额度，不触发 AI，%s",
                self._trace(event, bot_qq),
            )
            return None
        voice_text = ""
        if has_voice:
            voice_text = (
                str(event["_ai_voice_text"] or "")
                if "_ai_voice_text" in event
                else await self._fetch_voice_text(event, bot_qq, session)
            )
            event["_ai_voice_text"] = voice_text
            text = self._merge_voice_text(
                text,
                voice_text,
            )
        _, http_url, http_token = await self._connection_settings(bot_qq)
        self.ai_image_service.http_client = self.http_client
        images = await self._collect_ai_images(event, bot_qq, http_url, http_token)
        if (
            not text
            and not images
            and not quoted_text
            and not mentioned
            and not name_triggered
            and not keyword_triggered
            and not reply_all_triggered
            and not random_reply_triggered
        ):
            logger.info(
                "[OneBot AI] 收集图片后仍无有效内容且未命中触发条件，不触发 AI，%s",
                self._trace(event, bot_qq),
            )
            return None
        if has_voice and not voice_text and not text and not images and not quoted_text:
            logger.info(
                "[OneBot AI] 群聊语音转写为空且没有其他有效内容，跳过 AI，消息=%s",
                event.get("message_id") or "无消息ID",
            )
            return None
        if not text:
            if mentioned:
                text = "有人@了你，请回复。"
            elif reply_all_triggered:
                text = "请回复这条群消息。"
            elif images:
                text = "请分析图片内容。"
            else:
                text = "请回复这条消息。"
        text, quoted_text = merge_quoted_message(text, quoted_text, quoted_is_bot)
        if followup_used:
            logger.info(
                "[OneBot AI] 机器人=%s，用户=%s，群=%s，使用后续免触发额度",
                bot_qq,
                event.get("user_id"),
                event.get("group_id"),
            )
        logger.info(
            "[OneBot AI] 触发条件通过，准备生成回复，%s，图片数=%s",
            self._trace(event, bot_qq),
            len(images),
        )
        return text, quoted_text, followup_used, images

    async def _collect_ai_images(
        self,
        event: dict[str, Any],
        bot_qq: str,
        http_url: str,
        http_token: str,
    ) -> list[AiImage]:
        """优先复用动画表情入库时的 Base64，其他图片仍按事件即时读取。"""
        emoji_service = getattr(self, "animated_emoji_service", None)
        if emoji_service is not None:
            try:
                cached_images = await emoji_service.get_event_images(event, bot_qq)
                candidate_count = len(AiImageService.candidates(event))
                if cached_images and len(cached_images) == candidate_count:
                    return cached_images
            except Exception as exc:
                logger.warning(
                    "[OneBot AI] 读取动画表情 Base64 失败，将重新下载，机器人=%s，错误=%s",
                    bot_qq,
                    exc,
                )
        self.ai_image_service.http_client = self.http_client
        return await self.ai_image_service.collect(event, http_url, http_token)

    async def _fetch_voice_text(
        self,
        event: dict[str, Any],
        bot_qq: str,
        session: ReverseSession | None,
    ) -> str:
        """调用 OneBot 获取语音转文字结果。"""
        message_id = str(event.get("message_id") or "").strip()
        if not message_id:
            logger.error(
                "语音转文字失败，机器人=%s，事件缺少 message_id，事件=%s",
                bot_qq,
                _format_payload(event),
            )
            return ""
        try:
            if session is not None:
                response = await self.reverse_manager.call(
                    "fetch_ptt_text",
                    {"message_id": message_id},
                    session=session,
                )
            else:
                response = await self.call_action(
                    "fetch_ptt_text",
                    {"message_id": message_id},
                    self_id=bot_qq,
                )
        except Exception as exc:
            logger.error(
                "语音转文字动作调用失败，机器人=%s，消息=%s，错误=%s",
                bot_qq,
                message_id,
                exc,
            )
            return ""
        if response.get("status") != "ok" or response.get("retcode", 0) not in (0, None):
            logger.error(
                "语音转文字动作返回失败，机器人=%s，消息=%s，response=%s",
                bot_qq,
                message_id,
                _format_payload(response),
            )
            return ""
        text = self._voice_text_from_response(response.get("data"))
        if not text:
            logger.error(
                "语音转文字结果为空，机器人=%s，消息=%s，response=%s",
                bot_qq,
                message_id,
                _format_payload(response),
            )
            return ""
        logger.info(
            "语音转文字成功，机器人=%s，消息=%s，文字=%s",
            bot_qq,
            message_id,
            text,
        )
        return text

    @staticmethod
    def _voice_text_from_response(data: Any) -> str:
        """兼容 OneBot/NapCat 语音转文字响应中的常见文字字段。"""
        if isinstance(data, str):
            return data.strip()
        if not isinstance(data, dict):
            return ""
        for key in ("text", "transcript", "content", "result"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _merge_voice_text(text: str, voice_text: str) -> str:
        """将语音转写内容合并到当前用户输入，避免丢失同条文本。"""
        text = text.strip()
        voice_text = voice_text.strip()
        if not voice_text:
            return text
        if not text:
            return voice_text
        return f"{text}\n语音内容：{voice_text}"

    @staticmethod
    def _has_voice(event: dict[str, Any]) -> bool:
        """判断 OneBot 事件是否包含语音消息段。"""
        message = event.get("message")
        if isinstance(message, list):
            return any(
                isinstance(segment, dict)
                and segment.get("type") in {"record", "voice", "ptt"}
                for segment in message
            )
        value = message if isinstance(message, str) else event.get("raw_message")
        return isinstance(value, str) and bool(
            re.search(r"\[CQ:(?:record|voice|ptt)(?:,|\])", value)
        )

    async def _quoted_bot_message(
        self,
        message_id: str,
        bot_qq: str,
        session: ReverseSession | None,
    ) -> tuple[bool, str]:
        """调用 get_msg 读取被引用消息，并返回发送者是否为当前机器人。"""
        try:
            if session is not None:
                result = await self.reverse_manager.call(
                    "get_msg", {"message_id": message_id}, session=session
                )
            else:
                result = await self.call_action(
                    "get_msg", {"message_id": message_id}, self_id=bot_qq
                )
        except Exception as exc:
            logger.error(
                "引用消息查询失败，机器人=%s，message_id=%s，错误=%s",
                bot_qq,
                message_id,
                exc,
            )
            return False, ""
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, dict):
            return False, ""
        sender = sender_fields_from_event({**data, "self_id": bot_qq})
        text = self._extract_ai_text(data)
        return sender["user_id"] == bot_qq, (
            format_message_with_sender(text, **sender) if text else ""
        )

    async def _handle_websocket_ai_reply(
        self,
        event: dict[str, Any],
        session: ReverseSession,
        intent: AiMessageIntent | None = None,
    ) -> None:
        """在后台生成并发送 AI 回复，保持 WebSocket 收包循环可响应 get_msg。"""
        ai_result = await self._generate_ai_reply(
            event,
            session.self_id,
            session,
            defer_bookkeeping=True,
            intent=intent,
        )
        if ai_result is None:
            logger.info(
                "[OneBot WebSocket] AI 未产生回复，本条流程结束，%s",
                self._trace(event, session.self_id),
            )
            return
        reply, followup_used = ai_result
        try:
            async with self.database.session_factory() as db_session:
                config = await self.bot_ai_service.active_runtime_config(
                    db_session, session.self_id
                )
            resolved_intent = (
                event.pop("_ai_message_intent", None)
                or intent
                or default_message_intent()
            )
            intent_type = resolved_intent["reply_type"]
            explicit_image = intent_type in {"emoji", "generate_image"}
            image_config = (
                config
                if intent_type == "other" and self._should_use_image(config, event)
                else None
            )
            send_config = self._intent_reply_config(config, resolved_intent)
            reply_image = None
            if explicit_image:
                reply_image = await self._resolve_intent_reply_image(
                    send_config,
                    resolved_intent,
                    session.self_id,
                    event,
                )
            if image_config is not None:
                # 主回复不能等待额外的表情分类 AI 和图片查询，先发送正文。
                send_config["image_reply_probability"] = 0
            action, response, used_voice = await self.send_ai_reply(
                event,
                reply,
                session.self_id,
                config=send_config,
                session=session,
                force_quote=not followup_used,
                suppress_quote=followup_used,
                reply_image=reply_image,
                image_only=reply_image is not None,
            )
            logger.info(
                "[OneBot WebSocket] AI 回复动作返回，%s，action=%s，voice=%s，response=%s",
                self._trace(event, session.self_id),
                action or "none",
                used_voice,
                _format_payload(response),
            )
            if response.get("status") != "ok":
                logger.error(
                    "AI 回复发送失败，%s，action=%s，response=%s",
                    self._trace(event, session.self_id),
                    action,
                    response,
                )
            elif image_config is not None:
                task = asyncio.create_task(
                    self._handle_background_reply_image(
                        event,
                        reply,
                        session.self_id,
                        image_config,
                        session,
                        None,
                    )
                )
                self._reply_tasks.add(task)
                task.add_done_callback(self._reply_tasks.discard)
        except Exception as exc:
            logger.error("AI 回复发送异常，错误=%s，事件=%s", exc, _format_payload(event))

    async def _handle_background_reply_image(
        self,
        event: dict[str, Any],
        reply: str,
        bot_qq: str,
        config: dict[str, Any],
        session: ReverseSession,
        intent: AiMessageIntent | None = None,
    ) -> None:
        """在主回复发送成功后异步选择并补发动画表情。"""
        try:
            image = (
                await self._resolve_intent_reply_image(
                    config,
                    intent,
                    bot_qq,
                    event,
                )
                if intent is not None
                else await self._resolve_reply_image(
                    config,
                    self._plain_ai_reply(reply),
                    bot_qq,
                    event,
                )
            )
            if image is None:
                return
            response = await self._send_reply_image(
                event,
                bot_qq,
                image,
                session=session,
            )
            logger.info(
                "[OneBot WebSocket] AI 动画表情后台发送完成，机器人=%s，群=%s，response=%s",
                bot_qq,
                event.get("group_id") or "",
                _format_payload(response),
            )
        except Exception as exc:
            logger.error(
                "AI 动画表情后台发送异常，机器人=%s，群=%s，错误=%s",
                bot_qq,
                event.get("group_id") or "",
                exc,
            )

    async def send_ai_reply(
        self,
        event: dict[str, Any],
        reply: str,
        bot_qq: str,
        *,
        config: dict[str, Any] | None = None,
        session: ReverseSession | None = None,
        force_quote: bool = False,
        suppress_quote: bool = False,
        reply_image: dict[str, str] | None = None,
        image_only: bool = False,
    ) -> tuple[str, dict[str, Any], bool]:
        """按 AI 配置随机发送群语音，失败时自动回退到文本消息。

        Args:
            event: 触发 AI 的 OneBot 消息事件。
            reply: AI 生成的回复文本。
            bot_qq: 当前机器人 QQ。
            config: 当前机器人运行时 AI 配置。
            session: 反向 WebSocket 会话；为空时通过普通 action 调用。
            force_quote: 文本回退时是否引用当前消息。
            suppress_quote: 文本回退时是否禁止引用当前消息。
            reply_image: 已解析的 Base64 动画表情；为空时由本方法自动解析。
            image_only: 是否只发送 ``reply_image``，不再额外发送文本或语音。
        Returns:
            实际动作名、OneBot 返回值、是否使用语音。
        """
        reply_content = str(reply or "").strip()
        voice_text = self._plain_ai_reply(reply_content)
        if image_only:
            if reply_image is None or not str(reply_image.get("data") or "").strip():
                return "", onebot_error("AI 图片回复内容为空", 1404), False
            action_request = self._build_reply_action(
                event,
                "",
                bot_qq,
                suppress_quote=True,
                image=reply_image,
            )
            if action_request is None:
                return "", onebot_error("当前事件类型不支持 AI 图片回复", 1402), False
            action, params = action_request
            response = (
                await self.reverse_manager.call(action, params, session=session)
                if session is not None
                else await self.call_action(action, params, self_id=bot_qq)
            )
            return action, response, False
        if not reply_content:
            return "", onebot_error("AI 回复内容为空", 1401), False
        if reply_image is None and self._should_use_image(config, event):
            reply_image = await self._resolve_reply_image(
                config,
                voice_text,
                bot_qq,
                event,
            )
        group_id = event.get("group_id")
        if voice_text and self._should_use_voice(config, event):
            character_service = getattr(self, "ai_voice_character_service", None)
            if character_service is None:
                character_service = AiVoiceCharacterService(None)
            configured_character = str(
                (config or {}).get("voice_character") or ""
            ).strip()
            rule_service = getattr(self, "ai_voice_character_rule_service", None)
            if rule_service is not None:
                configured_character = await rule_service.resolve_character(
                    bot_qq, str(group_id)
                ) or configured_character
            if configured_character in {
                "",
                "ai_char_1",
                "lucy-voice-laibixiaoxin",
            }:
                preferred_character = DEFAULT_VOICE_CHARACTER
            else:
                preferred_character = configured_character

            async def fetch_characters() -> dict[str, Any]:
                if session is not None:
                    return await self.reverse_manager.call(
                        "get_ai_characters",
                        {"group_id": group_id},
                        session=session,
                    )
                return await self.call_action(
                    "get_ai_characters",
                    {"group_id": group_id},
                    self_id=bot_qq,
                )

            try:
                character = await character_service.resolve(
                    bot_qq,
                    str(group_id),
                    preferred_character,
                    fetch_characters,
                )
                voice_params = {
                    "character": character,
                    "group_id": group_id,
                    "text": voice_text,
                }
                logger.info(
                    "[OneBot AI] 语音角色选择，机器人=%s，群=%s，角色=%s",
                    bot_qq,
                    group_id,
                    character,
                )
                response = (
                    await self.reverse_manager.call(
                        "send_group_ai_record", voice_params, session=session
                    )
                    if session is not None
                    else await self.call_action(
                        "send_group_ai_record", voice_params, self_id=bot_qq
                    )
                )
                if response.get("status") == "ok":
                    if reply_image is not None:
                        await self._send_reply_image(
                            event, bot_qq, reply_image, session=session
                        )
                    return "send_group_ai_record", response, True
                await character_service.invalidate(
                    bot_qq, str(group_id)
                )
                refreshed_character = await character_service.resolve(
                    bot_qq,
                    str(group_id),
                    preferred_character,
                    fetch_characters,
                    force_refresh=True,
                )
                if refreshed_character and refreshed_character != character:
                    voice_params["character"] = refreshed_character
                    response = (
                        await self.reverse_manager.call(
                            "send_group_ai_record",
                            voice_params,
                            session=session,
                        )
                        if session is not None
                        else await self.call_action(
                            "send_group_ai_record",
                            voice_params,
                            self_id=bot_qq,
                        )
                    )
                    if response.get("status") == "ok":
                        if reply_image is not None:
                            await self._send_reply_image(
                                event, bot_qq, reply_image, session=session
                            )
                        return "send_group_ai_record", response, True
                logger.error(
                    "AI 语音回复失败，机器人=%s，群=%s，response=%s，回退文本",
                    bot_qq,
                    group_id,
                    _format_payload(response),
                )
            except Exception as exc:
                logger.error(
                    "AI 语音回复异常，机器人=%s，群=%s，错误=%s，回退文本",
                    bot_qq,
                    group_id,
                    exc,
                )
        action_request = self._build_reply_action(
            event,
            reply_content,
            bot_qq,
            force_quote=force_quote,
            suppress_quote=suppress_quote,
        )
        if action_request is None:
            return "", onebot_error("当前事件类型不支持 AI 回复", 1402), False
        action, params = action_request
        response = (
            await self.reverse_manager.call(action, params, session=session)
            if session is not None
            else await self.call_action(action, params, self_id=bot_qq)
        )
        if response.get("status") == "ok" and reply_image is not None:
            await self._send_reply_image(event, bot_qq, reply_image, session=session)
        return action, response, False

    @staticmethod
    def _plain_ai_reply(reply: Any) -> str:
        """移除 AI 回复中的 CQ 控制码，得到可发送文本或语音文本。"""
        return re.sub(r"\[CQ:[^\]]+\]", "", str(reply or "")).strip()

    @staticmethod
    def _should_use_voice(
        config: dict[str, Any] | None, event: dict[str, Any]
    ) -> bool:
        """根据 AI 配置和随机概率判断当前群聊是否发送语音。"""
        if event.get("message_type") != "group" or event.get("group_id") is None:
            return False
        if not config or not bool(config.get("voice_reply_enabled")):
            return False
        try:
            probability = max(
                0, min(100, int(config.get("voice_reply_probability", 0)))
            )
        except (TypeError, ValueError):
            probability = 0
        return probability > 0 and random.random() < probability / 100

    @staticmethod
    def _should_use_image(
        config: dict[str, Any] | None, event: dict[str, Any]
    ) -> bool:
        """根据 AI 配置和随机概率判断当前群聊是否发送动画表情。"""
        if event.get("message_type") != "group" or event.get("group_id") is None:
            return False
        if not config:
            return False
        try:
            probability = max(
                0, min(100, int(config.get("image_reply_probability", 100)))
            )
        except (TypeError, ValueError):
            probability = 100
        return probability > 0 and random.random() < probability / 100

    @staticmethod
    def _intent_reply_config(
        config: dict[str, Any] | None,
        intent: AiMessageIntent,
    ) -> dict[str, Any]:
        """将回复形式意图转换为现有语音和图片发送配置。"""
        selected = dict(config or {})
        if intent["reply_type"] == "voice":
            selected["voice_reply_enabled"] = True
            selected["voice_reply_probability"] = 100
            selected["image_reply_probability"] = 0
        elif intent["reply_type"] in {"emoji", "generate_image"}:
            selected["voice_reply_enabled"] = False
            selected["voice_reply_probability"] = 0
            selected["image_reply_probability"] = 0
        return selected

    @staticmethod
    def _extract_ai_text(event: dict[str, Any]) -> str:
        """提取文本和 QQ 表情描述，移除 CQ 控制码。"""
        def clean(value: Any) -> str:
            if value is None:
                return ""
            # NapCat 偶尔会把 CQ 码或零宽字符保留在 text 消息段中。
            value = html.unescape(re.sub(r"\[CQ:[^\]]+\]", "", str(value)))
            return value.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "").strip()

        def merge_forward_text(value: str) -> str:
            forward_text = clean(event.get("_forward_message_text"))
            return "\n".join(
                item for item in (value, forward_text) if item
            )

        message = event.get("message")
        if isinstance(message, list):
            parts: list[str] = []
            for segment in message:
                if not isinstance(segment, dict):
                    continue
                data = segment.get("data")
                if segment.get("type") == "text" and isinstance(data, dict) and data.get("text") is not None:
                    parts.append(str(data["text"]))
                elif segment.get("type") == "face" and isinstance(data, dict):
                    raw = data.get("raw")
                    raw = raw if isinstance(raw, dict) else {}
                    face_text = clean(raw.get("faceText") or data.get("name"))
                    if face_text:
                        parts.append(f"QQ 表情：{face_text}")
            return merge_forward_text(clean("".join(parts)))
        value = message if isinstance(message, str) else event.get("raw_message")
        return merge_forward_text(clean(value))

    async def _send_auto_reply(
        self,
        action: str,
        params: dict[str, Any],
        session: ReverseSession,
        rule_id: int,
    ) -> None:
        """发送 WebSocket 自动回复并记录明确失败上下文。"""
        try:
            response = await self.reverse_manager.call(
                action, params, session=session
            )
            logger.info(
                "[OneBot WebSocket] 自动回复动作返回，rule_id=%s，response：%s",
                rule_id,
                _format_payload(response),
            )
            if response.get("status") != "ok":
                logger.error(
                    "自动回复调用失败，rule_id=%s，action=%s，response=%s",
                    rule_id,
                    action,
                    response,
                )
        except Exception as exc:
            logger.error(
                "自动回复发送异常，rule_id=%s，action=%s，错误=%s",
                rule_id,
                action,
                exc,
            )

    @staticmethod
    def _build_reply_action(
        event: dict[str, Any],
        reply_content: str,
        bot_qq: str = "",
        force_quote: bool = False,
        suppress_quote: bool = False,
        image: dict[str, str] | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        """根据私聊或群聊事件构造 NapCat 发送消息动作。"""
        message_type = event.get("message_type")
        if (
            (message_type == "group" or is_poke_event(event, bot_qq))
            and event.get("group_id") is not None
        ):
            message: str | list[dict[str, Any]] = build_websocket_reply_content(
                event, reply_content, bot_qq, force_quote, suppress_quote
            )
            if image is not None and str(image.get("data") or "").strip():
                image_file = f"base64://{str(image['data']).strip()}"
                image_segment = {
                    "type": "image",
                    "data": {"file": image_file},
                }
                if isinstance(message, list):
                    message = [*message, image_segment]
                elif "[CQ:" in message:
                    # 保留 AI 回复中已有的 CQ 控制码，避免转为 text 段后失去解析。
                    message = f"{message}[CQ:image,file={image_file}]"
                else:
                    message = (
                        [{"type": "text", "data": {"text": message}}]
                        if message
                        else []
                    )
                    message.append(image_segment)
            return "send_group_msg", {
                "group_id": event["group_id"],
                "message": message,
            }
        if (
            (message_type == "private" or is_poke_event(event, bot_qq))
            and event.get("user_id") is not None
        ):
            private_message: str | list[dict[str, Any]] = reply_content
            if image is not None and str(image.get("data") or "").strip():
                private_message = []
                if reply_content:
                    private_message.append(
                        {"type": "text", "data": {"text": reply_content}}
                    )
                private_message.append(
                    {
                        "type": "image",
                        "data": {"file": f"base64://{str(image['data']).strip()}"},
                    }
                )
            return "send_private_msg", {
                "user_id": event["user_id"],
                "message": private_message,
            }
        return None
