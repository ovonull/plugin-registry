"""AI 消息回复的跨流程去重守卫。

功能：
1. 根据机器人、会话和消息 ID 生成稳定的消息级 Redis 键。
2. 使用 Redis ``SET NX EX`` 防止实时 AI、主动回复和重复上报并发处理同一消息。
3. Redis 不可用时使用共享的进程内状态兜底。
4. 抢占后固定保留 10 分钟，不主动释放，统一依赖 TTL 自动过期。
"""

import asyncio
from dataclasses import dataclass
import hashlib
import json
import logging
import time
from typing import Any
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError


logger = logging.getLogger("uvicorn.error")
MESSAGE_REPLY_TTL_SECONDS = 600


@dataclass(frozen=True, slots=True)
class AiMessageReplyClaim:
    """一次消息回复处理权。"""

    key: str
    token: str


class AiMessageReplyGuard:
    """维护消息级回复处理权，并在 Redis 异常时提供进程内兜底。"""

    def __init__(
        self,
        redis: Redis | None,
        ttl_seconds: int = MESSAGE_REPLY_TTL_SECONDS,
    ) -> None:
        """初始化守卫。

        Args:
            redis: 应用共享 Redis 客户端；为空时仅使用进程内兜底。
            ttl_seconds: 消息锁的固定有效时长，默认 10 分钟。
        """
        self.redis = redis
        self.ttl_seconds = max(60, int(ttl_seconds))
        self._local_lock = asyncio.Lock()
        self._local_claims: dict[str, tuple[str, float]] = {}

    async def claim(self, key: str) -> AiMessageReplyClaim | None:
        """原子抢占指定消息；已被其他流程处理时返回 ``None``。"""
        if not key:
            return None
        token = uuid4().hex
        if self.redis is not None:
            try:
                created = await self.redis.set(
                    key,
                    token,
                    ex=self.ttl_seconds,
                    nx=True,
                )
                return AiMessageReplyClaim(key, token) if created else None
            except RedisError as exc:
                logger.error(
                    "AI 消息回复 Redis 抢占失败，使用进程内兜底，key=%s，错误=%s",
                    key,
                    exc,
                )
        return await self._claim_local(key, token)

    @classmethod
    def event_key(cls, event: dict[str, Any], bot_qq: str) -> str:
        """根据实时 OneBot 消息事件生成共享 Redis 键。"""
        if event.get("post_type") != "message" or event.get("message_type") not in {
            "group",
            "private",
        }:
            return ""
        if not str(event.get("message_id") or "").strip():
            return ""
        return cls._build_key(
            bot_qq,
            str(event.get("message_type") or ""),
            str(event.get("group_id") or ""),
            str(event.get("user_id") or ""),
            str(event.get("message_id") or ""),
            event,
        )

    @classmethod
    def record_key(cls, message: Any) -> str:
        """根据聊天记录模型生成与实时事件一致的共享 Redis 键。"""
        if not str(getattr(message, "message_id", "") or "").strip():
            return ""
        event: dict[str, Any] = {}
        event_data = str(getattr(message, "event_data", "") or "")
        if event_data:
            try:
                loaded = json.loads(event_data)
                event = loaded if isinstance(loaded, dict) else {}
            except (TypeError, ValueError):
                event = {}
        return cls._build_key(
            str(getattr(message, "bot_qq", "") or ""),
            str(getattr(message, "message_type", "") or ""),
            str(getattr(message, "group_id", "") or ""),
            str(
                getattr(message, "user_id", "")
                or getattr(message, "sender_user_id", "")
                or ""
            ),
            str(getattr(message, "message_id", "") or ""),
            event or {"record_id": getattr(message, "id", "")},
        )

    @staticmethod
    def _build_key(
        bot_qq: str,
        message_type: str,
        group_id: str,
        user_id: str,
        message_id: str,
        fallback: dict[str, Any],
    ) -> str:
        """构造不暴露原始消息内容的稳定摘要键。"""
        if not bot_qq or message_type not in {"group", "private"}:
            return ""
        if message_id:
            content_fingerprint = json.dumps(
                {
                    "raw_message": fallback.get("raw_message"),
                    "message": fallback.get("message"),
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                default=str,
            )
            identity = "\x00".join(
                (
                    bot_qq,
                    message_type,
                    group_id,
                    user_id if not group_id else "",
                    message_id,
                    content_fingerprint,
                )
            )
        else:
            return ""
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return f"napcat:ai_reply:message:{digest}"

    async def _claim_local(
        self, key: str, token: str
    ) -> AiMessageReplyClaim | None:
        """使用进程内字典原子抢占消息。"""
        async with self._local_lock:
            self._purge_local()
            if key in self._local_claims:
                return None
            self._local_claims[key] = (
                token,
                time.monotonic() + self.ttl_seconds,
            )
            return AiMessageReplyClaim(key, token)

    def _purge_local(self) -> None:
        """清理进程内已过期处理权。"""
        now = time.monotonic()
        expired = [
            key
            for key, (_, expires_at) in self._local_claims.items()
            if expires_at <= now
        ]
        for key in expired:
            self._local_claims.pop(key, None)
