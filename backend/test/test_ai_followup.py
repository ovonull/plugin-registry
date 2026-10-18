"""AI 群聊后续免触发 Redis 状态服务测试。"""

import asyncio
from unittest.mock import AsyncMock

from redis.exceptions import TimeoutError

from app.services.ai_followup_service import AiFollowupService


def test_ai_followup_state_is_scoped_and_consumed() -> None:
    """后续免触发额度按机器人、群和用户隔离并通过 Redis 原子消费。"""
    async def run() -> None:
        redis = AsyncMock()
        redis.set.return_value = True
        redis.eval.side_effect = [1, 0]
        service = AiFollowupService(redis, followup_count=3, ttl_seconds=600)
        event = {"message_type": "group", "group_id": 99, "user_id": 88}

        assert await service.activate(event, "123456") is True
        assert await service.consume(event, "123456") is True
        assert await service.consume(event, "123456") is False
        redis.set.assert_awaited_once_with(
            "napcat:ai_followup:123456:99:88", 3, ex=600, nx=True
        )
        assert redis.eval.await_count == 2

    asyncio.run(run())


def test_ai_followup_custom_count_and_zero_disable() -> None:
    """机器人自定义额度传给 Redis，设置为 0 时不创建额度。"""
    async def run() -> None:
        redis = AsyncMock()
        redis.set.return_value = True
        service = AiFollowupService(redis, followup_count=3, ttl_seconds=600)
        event = {"message_type": "group", "group_id": 99, "user_id": 88}

        assert await service.activate(event, "123456", followup_count=5) is True
        redis.set.assert_awaited_once_with(
            "napcat:ai_followup:123456:99:88", 5, ex=600, nx=True
        )
        redis.reset_mock()
        assert await service.activate(event, "123456", followup_count=0) is False
        redis.set.assert_not_awaited()

    asyncio.run(run())


def test_ai_followup_activate_retries_three_times_before_success() -> None:
    """Redis 写入超时后最多重试三次，恢复后正常开启状态。"""
    async def run() -> None:
        redis = AsyncMock()
        redis.set.side_effect = [
            TimeoutError("第一次超时"),
            TimeoutError("第二次超时"),
            TimeoutError("第三次超时"),
            True,
        ]
        service = AiFollowupService(redis, followup_count=3, ttl_seconds=600)
        event = {"message_type": "group", "group_id": 99, "user_id": 88}

        assert await service.activate(event, "123456") is True
        assert redis.set.await_count == 4

    asyncio.run(run())


def test_ai_followup_activate_returns_false_after_three_retries() -> None:
    """Redis 持续写入失败时，重试耗尽后返回失败。"""
    async def run() -> None:
        redis = AsyncMock()
        redis.set.side_effect = TimeoutError("连接超时")
        service = AiFollowupService(redis, followup_count=3, ttl_seconds=600)
        event = {"message_type": "group", "group_id": 99, "user_id": 88}

        assert await service.activate(event, "123456") is False
        assert redis.set.await_count == 4

    asyncio.run(run())
