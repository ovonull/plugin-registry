"""NapCat AI 语音角色缓存服务。

按机器人和群隔离缓存角色列表，避免每次发送 AI 语音都重复调用
``get_ai_characters``。缓存没有命中、内容损坏或发送端主动要求刷新时，
由调用方重新拉取当前群可用的真实角色编号。
"""

import json
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError


logger = logging.getLogger("uvicorn.error")
DEFAULT_CACHE_TTL_SECONDS = 259200
_FetchCharacters = Callable[[], Awaitable[dict[str, Any]]]


class AiVoiceCharacterService:
    """管理 NapCat AI 语音角色列表的 Redis 缓存。"""

    def __init__(
        self,
        redis: Redis | None,
        ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    ) -> None:
        self.redis = redis
        self.ttl_seconds = max(1, int(ttl_seconds))

    async def resolve(
        self,
        bot_qq: str,
        group_id: str,
        preferred_character: str,
        fetch_characters: _FetchCharacters,
        *,
        force_refresh: bool = False,
    ) -> str:
        """获取当前群可用的真实角色编号。

        Args:
            bot_qq: 机器人 QQ 号。
            group_id: 目标群号。
            preferred_character: 配置中优先使用的角色编号。
            fetch_characters: 调用 NapCat ``get_ai_characters`` 的回调。
            force_refresh: 是否跳过 Redis 并重新获取角色列表。
        Returns:
            可用于 ``send_group_ai_record`` 的角色编号；无法获取时返回配置值。
        """
        key = self._key(bot_qq, group_id)
        characters: list[str] = []
        if key and not force_refresh:
            characters = await self._read(key)
        if not characters:
            try:
                response = await fetch_characters()
            except Exception as exc:
                logger.error(
                    "AI 语音角色获取异常，机器人=%s，群=%s，错误=%s",
                    bot_qq,
                    group_id,
                    exc,
                )
                response = {}
            characters = self._extract_ids(response)
            if characters and key:
                await self._write(key, characters)
            elif key:
                logger.warning(
                    "AI 语音角色列表为空，机器人=%s，群=%s，无法缓存真实角色",
                    bot_qq,
                    group_id,
                )

        preferred = str(preferred_character or "").strip()
        if preferred and preferred in characters:
            return preferred
        if characters:
            return random.choice(characters)
        return preferred

    async def invalidate(self, bot_qq: str, group_id: str) -> None:
        """删除指定机器人和群的角色缓存，供发送失败时刷新使用。"""
        key = self._key(bot_qq, group_id)
        if not key or self.redis is None:
            return
        try:
            await self.redis.delete(key)
        except RedisError as exc:
            logger.error(
                "AI 语音角色缓存删除失败，key=%s，错误=%s",
                key,
                exc,
            )

    async def _read(self, key: str) -> list[str]:
        """读取并校验 Redis 中的角色列表。"""
        if self.redis is None:
            return []
        try:
            value = await self.redis.get(key)
        except RedisError as exc:
            logger.error("AI 语音角色缓存读取失败，key=%s，错误=%s", key, exc)
            return []
        if not value:
            return []
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            logger.warning("AI 语音角色缓存格式无效，key=%s", key)
            return []
        return self._normalise_ids(parsed)

    async def _write(self, key: str, characters: list[str]) -> None:
        """将去重后的角色编号写入 Redis。"""
        if self.redis is None:
            return
        try:
            await self.redis.set(
                key,
                json.dumps(characters, ensure_ascii=False),
                ex=self.ttl_seconds,
            )
            logger.info(
                "AI 语音角色已缓存，key=%s，角色数=%s，过期=%s秒",
                key,
                len(characters),
                self.ttl_seconds,
            )
        except RedisError as exc:
            logger.error("AI 语音角色缓存写入失败，key=%s，错误=%s", key, exc)

    @classmethod
    def _extract_ids(cls, response: dict[str, Any]) -> list[str]:
        """从 NapCat 返回的分类角色结构中提取角色编号。"""
        if not isinstance(response, dict) or response.get("status") not in {None, "ok"}:
            return []
        data = response.get("data")
        categories: list[Any]
        if isinstance(data, list):
            categories = data
        elif isinstance(data, dict):
            categories = [data]
        else:
            categories = []
        values: list[Any] = []
        for category in categories:
            if not isinstance(category, dict):
                continue
            characters = category.get("characters")
            if isinstance(characters, list):
                values.extend(characters)
            elif category.get("character_id") is not None:
                values.append(category)
        return cls._normalise_ids(values)

    @staticmethod
    def _normalise_ids(values: Any) -> list[str]:
        """清洗角色编号并保持接口返回顺序。"""
        if not isinstance(values, list):
            return []
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            if isinstance(value, dict):
                value = value.get("character_id")
            character_id = str(value or "").strip()
            if character_id and character_id not in seen:
                seen.add(character_id)
                result.append(character_id)
        return result

    @staticmethod
    def _key(bot_qq: str, group_id: str) -> str:
        """生成机器人和群隔离的 Redis key。"""
        if not str(bot_qq).strip() or not str(group_id).strip():
            return ""
        return f"napcat:ai_voice_characters:{bot_qq}:{group_id}"
