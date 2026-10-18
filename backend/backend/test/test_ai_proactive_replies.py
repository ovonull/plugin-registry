"""AI 随机主动回复规则和调度发送测试。"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.application import create_app
from app.common.time_utils import beijing_now
from app.models.entities import AiProactiveReplyMessage, AiProactiveReplyRule, ChatMessage
from app.services.ai_message_reply_guard import AiMessageReplyGuard
from test_service import build_settings, login


def _headers(client: TestClient) -> dict[str, str]:
    """登录测试管理员并返回请求头。"""
    token = login(client, "admin", "admin-password")
    return {"Authorization": f"Bearer {token}"}


def _create_bot(client: TestClient, headers: dict[str, str]) -> None:
    """创建测试机器人。"""
    response = client.post(
        "/api/bots",
        headers=headers,
        json={"bot_qq": "123456", "group_mode": "all", "is_enabled": True},
    )
    assert response.json()["success"] is True


def test_ai_proactive_reply_crud_defaults_and_group_scope(tmp_path: Path) -> None:
    """主动回复规则默认使用 1 到 5 分钟，且支持指定群。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        headers = _headers(client)
        _create_bot(client, headers)
        created = client.post(
            "/api/ai_proactive_replies",
            headers=headers,
            json={"bot_qq": "123456"},
        ).json()
        assert created["success"] is True
        assert created["data"]["group_id"] == ""
        assert created["data"]["min_interval_seconds"] == 60
        assert created["data"]["max_interval_seconds"] == 300

        updated = client.put(
            f"/api/ai_proactive_replies/{created['data']['id']}",
            headers=headers,
            json={
                "bot_qq": "123456",
                "group_id": "9988",
                "min_interval_seconds": 120,
                "max_interval_seconds": 240,
                "is_enabled": True,
            },
        ).json()
        assert updated["success"] is True
        assert updated["data"]["group_id"] == "9988"

        deleted = client.post(
            f"/api/ai_proactive_replies/{created['data']['id']}/delete",
            headers=headers,
        ).json()
        assert deleted["success"] is True
        assert client.get("/api/ai_proactive_replies", headers=headers).json()["data"]["total"] == 0


def test_ai_proactive_reply_scheduler_sends_plain_text(tmp_path: Path) -> None:
    """发送后下一轮跳过机器人末条消息，新用户消息到达后可继续回复。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        headers = _headers(client)
        _create_bot(client, headers)
        created = client.post(
            "/api/ai_proactive_replies",
            headers=headers,
            json={"bot_qq": "123456", "group_id": "9988"},
        ).json()
        service = client.app.state.container.ai_proactive_reply_service
        onebot = client.app.state.container.onebot_service
        service.bot_ai_service.active_runtime_config = AsyncMock(
            return_value={"is_enabled": True}
        )
        service.bot_ai_service.generate = AsyncMock(return_value="[CQ:reply,id=1]自然回复")
        calls: list[tuple[str, dict]] = []

        async def fake_call_action(action: str, params: dict, **_kwargs):
            calls.append((action, params))
            return {"status": "ok", "retcode": 0}

        onebot.call_action = fake_call_action

        async def prepare_message() -> None:
            async with service.database.session_factory() as session:
                for message_id in ("55", "56", "57"):
                    session.add(
                        ChatMessage(
                            bot_qq="123456",
                            post_type="message",
                            message_type="group",
                            message_id=message_id,
                            user_id="777",
                            group_id="9988",
                            sender_user_id="777",
                            sender_nickname="群友",
                            message_text="大家今天吃什么",
                            event_time=beijing_now(),
                        )
                    )
                await session.commit()
                rule = await session.get(AiProactiveReplyRule, created["data"]["id"])
                assert rule is not None
                rule.next_run_at = beijing_now()
                await session.commit()

        client.portal.call(prepare_message)
        client.portal.call(service._process_rule, created["data"]["id"])
        assert calls == [
            ("send_group_msg", {"group_id": "9988", "message": "自然回复"})
        ]

        async def make_rule_due() -> None:
            async with service.database.session_factory() as session:
                rule = await session.get(AiProactiveReplyRule, created["data"]["id"])
                assert rule is not None
                rule.next_run_at = beijing_now()
                await session.commit()

        client.portal.call(make_rule_due)
        client.portal.call(service._process_rule, created["data"]["id"])
        assert calls == [
            ("send_group_msg", {"group_id": "9988", "message": "自然回复"})
        ]

        async def record_new_user_message() -> None:
            async with service.database.session_factory() as session:
                session.add(
                    ChatMessage(
                        bot_qq="123456",
                        post_type="message",
                        message_type="group",
                        message_id="58",
                        user_id="777",
                        group_id="9988",
                        sender_user_id="777",
                        sender_nickname="群友",
                        message_text="新的群友消息",
                        event_time=beijing_now(),
                    )
                )
                await session.commit()

        client.portal.call(record_new_user_message)
        client.portal.call(make_rule_due)
        client.portal.call(service._process_rule, created["data"]["id"])
        assert calls == [
            ("send_group_msg", {"group_id": "9988", "message": "自然回复"}),
            ("send_group_msg", {"group_id": "9988", "message": "自然回复"}),
        ]

        async def count_processed_messages() -> int:
            async with service.database.session_factory() as session:
                rows = await session.scalars(
                    select(AiProactiveReplyMessage).where(
                        AiProactiveReplyMessage.rule_id == created["data"]["id"]
                    )
                )
                return len(rows.all())

        assert client.portal.call(count_processed_messages) == 2


def test_immediate_ai_triggered_message_is_skipped_by_proactive_reply(
    tmp_path: Path,
) -> None:
    """即时 AI 已处理的艾特消息不会再次进入主动回复。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        headers = _headers(client)
        _create_bot(client, headers)
        created = client.post(
            "/api/ai_proactive_replies",
            headers=headers,
            json={"bot_qq": "123456", "group_id": "9988"},
        ).json()
        service = client.app.state.container.ai_proactive_reply_service
        onebot = client.app.state.container.onebot_service
        service.bot_ai_service.active_runtime_config = AsyncMock(
            return_value={"is_enabled": True}
        )
        service.bot_ai_service.generate = AsyncMock(return_value="即时回复")
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": 123456,
            "user_id": 777,
            "group_id": 9988,
            "message_id": 90,
            "message": [
                {"type": "at", "data": {"qq": "123456"}},
                {"type": "text", "data": {"text": "你好"}},
            ],
            "raw_message": "[CQ:at,qq=123456] 你好",
            "sender": {"user_id": 777, "nickname": "群友"},
        }

        async def record_and_trigger() -> None:
            await onebot.chat_message_service.record_event(
                event, "websocket", "123456"
            )
            result = await onebot._generate_ai_reply(event, "123456")
            assert result == ("即时回复", False)

        client.portal.call(record_and_trigger)

        async def make_rule_due() -> None:
            async with service.database.session_factory() as session:
                rule = await session.get(AiProactiveReplyRule, created["data"]["id"])
                assert rule is not None
                rule.next_run_at = beijing_now()
                await session.commit()

        calls: list[tuple[str, dict]] = []

        async def fake_call_action(action: str, params: dict, **_kwargs):
            calls.append((action, params))
            return {"status": "ok"}

        onebot.call_action = fake_call_action
        client.portal.call(make_rule_due)
        client.portal.call(service._process_rule, created["data"]["id"])
        assert calls == []

        async def load_message() -> ChatMessage:
            async with service.database.session_factory() as session:
                return await session.scalar(
                    select(ChatMessage).where(ChatMessage.message_id == "90")
                )

        assert client.portal.call(load_message).ai_triggered is True


def test_message_reply_lock_blocks_proactive_reply_for_ten_minutes(
    tmp_path: Path,
) -> None:
    """实时消息抢占后，主动回复不能处理同一消息，失败收口也不主动释放。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        headers = _headers(client)
        _create_bot(client, headers)
        created = client.post(
            "/api/ai_proactive_replies",
            headers=headers,
            json={"bot_qq": "123456", "group_id": "9988"},
        ).json()
        service = client.app.state.container.ai_proactive_reply_service
        onebot = client.app.state.container.onebot_service
        assert service.ai_message_reply_guard is onebot.ai_message_reply_guard
        assert service.ai_message_reply_guard.ttl_seconds == 600
        service.bot_ai_service.active_runtime_config = AsyncMock(
            return_value={"is_enabled": True}
        )
        service.bot_ai_service.generate = AsyncMock(return_value="不应生成")
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": 123456,
            "user_id": 777,
            "group_id": 9988,
            "message_id": 91,
            "message": "正在处理的消息",
            "raw_message": "正在处理的消息",
            "sender": {"user_id": 777, "nickname": "群友"},
        }

        async def claim_and_record():
            key = onebot.ai_message_reply_guard.event_key(event, "123456")
            claim = await onebot.ai_message_reply_guard.claim(key)
            assert claim is not None
            await onebot.chat_message_service.record_event(
                event,
                "websocket",
                "123456",
            )
            assert await onebot.ai_message_reply_guard.claim(key) is None
            other_event = {**event, "message_id": 92}
            other_key = onebot.ai_message_reply_guard.event_key(
                other_event,
                "123456",
            )
            assert await onebot.ai_message_reply_guard.claim(other_key) is not None
            event_without_message_id = {
                key: value
                for key, value in event.items()
                if key != "message_id"
            }
            assert onebot.ai_message_reply_guard.event_key(
                event_without_message_id,
                "123456",
            ) == ""

        async def make_rule_due() -> None:
            async with service.database.session_factory() as session:
                rule = await session.get(AiProactiveReplyRule, created["data"]["id"])
                assert rule is not None
                rule.next_run_at = beijing_now()
                await session.commit()

        client.portal.call(claim_and_record)
        client.portal.call(make_rule_due)
        client.portal.call(service._process_rule, created["data"]["id"])
        service.bot_ai_service.generate.assert_not_awaited()


def test_message_reply_guard_uses_redis_ttl_without_active_release() -> None:
    """Redis 消息锁固定使用 600 秒 TTL，完成和失败收口均不主动删除。"""
    redis = AsyncMock()
    redis.set.return_value = True
    guard = AiMessageReplyGuard(redis)

    async def run() -> None:
        claim = await guard.claim("napcat:ai_reply:message:test")
        assert claim is not None

    asyncio.run(run())
    redis.set.assert_awaited_once()
    assert redis.set.await_args.kwargs == {"ex": 600, "nx": True}
    redis.eval.assert_not_awaited()
    redis.delete.assert_not_awaited()


def test_ai_proactive_reply_uses_message_intent_classification(
    tmp_path: Path,
) -> None:
    """主动回复根据统一意图结果选择文生图并发送返回图片。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        headers = _headers(client)
        _create_bot(client, headers)
        created = client.post(
            "/api/ai_proactive_replies",
            headers=headers,
            json={"bot_qq": "123456", "group_id": "9988"},
        ).json()
        service = client.app.state.container.ai_proactive_reply_service
        onebot = client.app.state.container.onebot_service
        config = {"is_enabled": True, "history_limit": 10}
        service.bot_ai_service.active_runtime_config = AsyncMock(return_value=config)
        service.bot_ai_service.generate = AsyncMock(return_value="图片马上来")
        onebot.analyse_message_intent = AsyncMock(
            return_value={
                "mute": False,
                "mute_reason": "",
                "reply_type": "generate_image",
                "emoji_category": "",
                "generate_words": "一张蓝色机器人自拍",
                "reason": "用户明确要求图片",
            }
        )
        image = {"media_type": "image/png", "data": "aW1hZ2U="}
        onebot._resolve_intent_reply_image = AsyncMock(return_value=image)
        onebot.send_ai_reply = AsyncMock(
            return_value=("send_group_msg", {"status": "ok", "data": {}}, False)
        )
        onebot.record_sent_group_message = AsyncMock(return_value=None)

        async def prepare_message() -> None:
            async with service.database.session_factory() as session:
                session.add(
                    ChatMessage(
                        bot_qq="123456",
                        post_type="message",
                        message_type="group",
                        message_id="92",
                        user_id="777",
                        group_id="9988",
                        sender_user_id="777",
                        sender_nickname="群友",
                        raw_message="发张自拍",
                        message_text="发张自拍",
                        event_time=beijing_now(),
                    )
                )
                rule = await session.get(
                    AiProactiveReplyRule,
                    created["data"]["id"],
                )
                assert rule is not None
                rule.next_run_at = beijing_now()
                await session.commit()

        client.portal.call(prepare_message)
        client.portal.call(service._process_rule, created["data"]["id"])

        intent_event = onebot.analyse_message_intent.await_args.args[0]
        assert intent_event["message_id"] == "92"
        assert intent_event["message"] == "发张自拍"
        onebot._resolve_intent_reply_image.assert_awaited_once()
        send_kwargs = onebot.send_ai_reply.await_args.kwargs
        assert send_kwargs["reply_image"] == image
        assert send_kwargs["image_only"] is True
        assert send_kwargs["config"]["voice_reply_probability"] == 0
        assert send_kwargs["config"]["image_reply_probability"] == 0
