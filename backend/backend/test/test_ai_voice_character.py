"""AI 语音角色获取与 Redis 缓存测试。"""

import asyncio
import json
from unittest.mock import AsyncMock

from app.services.ai_voice_character_service import AiVoiceCharacterService


def _response(*character_ids: str) -> dict:
    """构造 NapCat 分组角色响应。"""
    return {
        "status": "ok",
        "data": [
            {
                "type": "推荐",
                "characters": [
                    {"character_id": character_id} for character_id in character_ids
                ],
            }
        ],
    }


def test_voice_character_cache_avoids_repeated_fetch() -> None:
    """首次从 NapCat 获取角色并缓存，后续直接读取 Redis。"""
    redis = AsyncMock()
    redis.get.return_value = None
    fetch_characters = AsyncMock(return_value=_response("lucy-voice-laibixiaoxin"))
    service = AiVoiceCharacterService(redis)

    first = asyncio.run(
        service.resolve(
            "123456",
            "9988",
            "invalid-character",
            fetch_characters,
        )
    )
    redis.get.return_value = json.dumps(["lucy-voice-laibixiaoxin"])
    second = asyncio.run(
        service.resolve(
            "123456",
            "9988",
            "invalid-character",
            fetch_characters,
        )
    )

    assert first == "lucy-voice-laibixiaoxin"
    assert second == "lucy-voice-laibixiaoxin"
    fetch_characters.assert_awaited_once()
    redis.set.assert_awaited_once()
    assert redis.set.await_args.args[0] == (
        "napcat:ai_voice_characters:123456:9988"
    )


def test_preferred_character_is_used_when_available() -> None:
    """配置中的角色在当前群可用时优先使用该角色。"""
    redis = AsyncMock()
    redis.get.return_value = json.dumps(
        ["lucy-voice-laibixiaoxin", "lucy-voice-houge"]
    )
    fetch_characters = AsyncMock()
    service = AiVoiceCharacterService(redis)

    character = asyncio.run(
        service.resolve(
            "123456",
            "9988",
            "lucy-voice-houge",
            fetch_characters,
        )
    )

    assert character == "lucy-voice-houge"
    fetch_characters.assert_not_awaited()


def test_missing_preferred_character_uses_random_available_character(monkeypatch) -> None:
    """优先角色不可用时随机选择角色，而不是固定使用第一项。"""
    service = AiVoiceCharacterService(None)
    fetch_characters = AsyncMock(
        return_value=_response("lucy-voice-f38", "lucy-voice-houge")
    )
    monkeypatch.setattr(
        "app.services.ai_voice_character_service.random.choice",
        lambda values: values[-1],
    )

    character = asyncio.run(
        service.resolve(
            "123456",
            "9988",
            "lucy-voice-not-found",
            fetch_characters,
        )
    )

    assert character == "lucy-voice-houge"
