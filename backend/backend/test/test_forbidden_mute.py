"""违禁消息 Redis 禁言递增测试。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services.forbidden_mute_service import (
    MUTE_WINDOW_SECONDS,
    ForbiddenMuteService,
)
from app.services.onebot_service import OneBotService


def test_forbidden_mute_duration_increments_and_resets_by_redis_ttl() -> None:
    """同一机器人、群和用户按五档时长递增，超过第五次保持一天。"""
    async def run() -> None:
        redis = AsyncMock()
        redis.eval.side_effect = [1, 2, 3, 4, 5, 6]
        service = ForbiddenMuteService(redis)
        event = {"message_type": "group", "group_id": 9988, "user_id": 778899}

        durations = [
            await service.next_duration(event, "123456", fallback_duration=300)
            for _ in range(6)
        ]

        assert durations == [300, 600, 3600, 43200, 86400, 86400]
        assert redis.eval.await_count == 6
        for call in redis.eval.await_args_list:
            assert call.args[1:] == (
                1,
                "napcat:forbidden_mute:123456:9988:778899",
                MUTE_WINDOW_SECONDS,
            )

    asyncio.run(run())


def test_http_forbidden_ban_retracts_before_using_escalated_duration() -> None:
    """HTTP 违规消息先撤回，再使用 Redis 计算的时长执行禁言。"""
    async def run() -> None:
        redis = AsyncMock()
        redis.eval.side_effect = [1, 2, 3]
        service = OneBotService.__new__(OneBotService)
        service.forbidden_mute_service = ForbiddenMuteService(redis)
        calls: list[tuple[str, dict]] = []

        async def fake_call_action(action: str, params: dict, **_kwargs):
            calls.append((action, params.copy()))
            return {"status": "ok", "retcode": 0}

        service.call_action = fake_call_action
        rule = SimpleNamespace(
            keyword="违规",
            keywords="违规",
            mute_duration=300,
        )
        for message_id in (101, 102, 103):
            await service._handle_http_forbidden(
                {
                    "message_type": "group",
                    "self_id": 123456,
                    "group_id": 9988,
                    "user_id": 778899,
                    "message_id": message_id,
                },
                rule,
            )

        assert calls == [
            ("delete_msg", {"message_id": 101}),
            ("set_group_ban", {"group_id": 9988, "user_id": 778899, "duration": 300}),
            ("delete_msg", {"message_id": 102}),
            ("set_group_ban", {"group_id": 9988, "user_id": 778899, "duration": 600}),
            ("delete_msg", {"message_id": 103}),
            ("set_group_ban", {"group_id": 9988, "user_id": 778899, "duration": 3600}),
        ]

    asyncio.run(run())


def test_http_forbidden_mute_disabled_only_retracts() -> None:
    """关闭同步禁言后，命中规则只撤回消息，不调用禁言动作。"""
    async def run() -> None:
        service = OneBotService.__new__(OneBotService)
        service.forbidden_mute_service = ForbiddenMuteService(AsyncMock())
        calls: list[tuple[str, dict]] = []

        async def fake_call_action(action: str, params: dict, **_kwargs):
            calls.append((action, params.copy()))
            return {"status": "ok", "retcode": 0}

        service.call_action = fake_call_action
        await service._handle_http_forbidden(
            {
                "message_type": "group",
                "self_id": 123456,
                "group_id": 9988,
                "user_id": 778899,
                "message_id": 201,
            },
            SimpleNamespace(
                keyword="违规",
                keywords="违规",
                mute_duration=300,
                mute_enabled=False,
            ),
        )

        assert calls == [("delete_msg", {"message_id": 201})]

    asyncio.run(run())
