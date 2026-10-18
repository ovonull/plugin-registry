"""AI 群聊后续免触发状态服务。

使用 Redis 保存用户在机器人回复后的短期会话额度，避免多进程部署时状态不一致。
"""

import asyncio
import logging
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError


logger = logging.getLogger("uvicorn.error")
MAX_WRITE_RETRIES = 3
RETRY_DELAY_SECONDS = 0.2


class AiFollowupService:
    """管理同一用户后续消息的免 @/引用触发额度。"""

    _CONSUME_SCRIPT = """
local remaining = redis.call('DECR', KEYS[1])
if remaining < 0 then
    redis.call('DEL', KEYS[1])
    return 0
end
if remaining == 0 then
    redis.call('DEL', KEYS[1])
end
return 1
"""

    def __init__(
        self,
        redis: Redis | None,
        followup_count: int = 3,
        ttl_seconds: int = 259200,
    ) -> None:
        self.redis = redis
        self.followup_count = followup_count
        self.ttl_seconds = ttl_seconds

    async def consume(
        self,
        event: dict[str, Any],
        bot_qq: str,
        followup_count: int | None = None,
    ) -> bool:
        """原子消费一条后续免触发额度。"""
        if followup_count is not None and followup_count <= 0:
            return False
        key = self._key(event, bot_qq)
        if not key or self.redis is None:
            return False
        try:
            return bool(await self.redis.eval(self._CONSUME_SCRIPT, 1, key))
        except RedisError as exc:
            logger.error("AI 后续免触发状态读取失败，key=%s，错误=%s", key, exc)
            return False

    async def activate(
        self,
        event: dict[str, Any],
        bot_qq: str,
        followup_count: int | None = None,
    ) -> bool:
        """为首次 AI 回复后的用户创建后续免触发额度。"""
        count = self.followup_count if followup_count is None else followup_count
        if count <= 0:
            return False
        key = self._key(event, bot_qq)
        if not key or self.redis is None:
            return False
        for attempt in range(MAX_WRITE_RETRIES + 1):
            try:
                created = await self.redis.set(
                    key,
                    count,
                    ex=self.ttl_seconds,
                    nx=True,
                )
                if created:
                    logger.info(
                        "AI 后续免触发已开启，机器人=%s，群=%s，用户=%s，剩余次数=%s，过期=%s秒",
                        bot_qq,
                        event.get("group_id"),
                        self._user_id(event),
                        count,
                        self.ttl_seconds,
                    )
                return bool(created)
            except RedisError as exc:
                if attempt >= MAX_WRITE_RETRIES:
                    logger.error(
                        "AI 后续免触发状态写入失败，已重试%s次，key=%s，错误=%s",
                        MAX_WRITE_RETRIES,
                        key,
                        exc,
                    )
                    return False
                logger.warning(
                    "AI 后续免触发状态写入失败，将进行第%s/%s次重试，key=%s，错误=%s",
                    attempt + 1,
                    MAX_WRITE_RETRIES,
                    key,
                    exc,
                )
                await asyncio.sleep(RETRY_DELAY_SECONDS * (attempt + 1))

    async def close(self) -> None:
        """关闭 Redis 连接池。"""
        if self.redis is not None:
            await self.redis.aclose()

    @classmethod
    def _key(cls, event: dict[str, Any], bot_qq: str) -> str:
        """按机器人、群和用户生成隔离键，仅对群消息启用。"""
        if event.get("message_type") != "group":
            return ""
        group_id = event.get("group_id")
        user_id = cls._user_id(event)
        if group_id is None or not user_id or not bot_qq:
            return ""
        return f"napcat:ai_followup:{bot_qq}:{group_id}:{user_id}"

    @staticmethod
    def _user_id(event: dict[str, Any]) -> str:
        """提取消息发送者 QQ。"""
        sender = event.get("sender")
        sender = sender if isinstance(sender, dict) else {}
        return str(event.get("user_id") or sender.get("user_id") or "")
