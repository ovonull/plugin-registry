"""AI 触发消息携带最近聊天上下文测试。"""

from datetime import timedelta
import hashlib
import hmac
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.application import create_app
from app.common.time_utils import beijing_now
from app.models.entities import ChatMessage
from test_bot_ai import (
    _ai_payload,
    _create_bot,
    _headers,
    _intent_response,
    _is_intent_request,
)
from test_service import build_settings


BOT_QQ = "123456"


def _post_event(client: TestClient, settings, event: dict) -> dict:
    """向 HTTP OneBot 入口发送签名事件。"""
    raw = json.dumps(event, ensure_ascii=False).encode("utf-8")
    signature = "sha1=" + hmac.new(
        settings.napcat_token.encode(), raw, hashlib.sha1
    ).hexdigest()
    return client.post(
        "/onebot/v11/events",
        content=raw,
        headers={"x-signature": signature},
    ).json()


def test_ai_history_limit_is_configurable(tmp_path: Path) -> None:
    """The configured history limit controls the number of context messages."""
    settings = build_settings(tmp_path)
    captured_messages: list[dict] = []

    def ai_handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if _is_intent_request(payload):
            return _intent_response()
        captured_messages.extend(payload["messages"])
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "custom-history-reply"}}]},
        )

    with TestClient(create_app(settings)) as client:
        headers = _headers(client)
        _create_bot(client, headers)
        client.put(
            f"/api/bots/{BOT_QQ}/ai",
            headers=headers,
            json=_ai_payload(history_limit=3),
        )

        async def seed_history() -> None:
            async with client.app.state.container.database.session_factory() as session:
                now = beijing_now()
                session.add_all(
                    [
                        ChatMessage(
                            bot_qq=BOT_QQ,
                            post_type="message",
                            message_type="group",
                            message_id=f"custom-history-{index}",
                            user_id="9000",
                            sender_user_id="9000",
                            group_id="99",
                            sender_nickname="",
                            message_text=f"history-{index}",
                            event_time=now - timedelta(minutes=10 - index),
                        )
                        for index in range(1, 6)
                    ]
                )
                await session.commit()

        client.portal.call(seed_history)
        onebot = client.app.state.container.onebot_service
        provider = client.app.state.container.bot_ai_service.provider
        client.portal.call(onebot.http_client.aclose)
        client.portal.call(provider.http_client.aclose)
        transport = httpx.MockTransport(ai_handler)
        onebot.http_client = httpx.AsyncClient(transport=transport, timeout=2)
        provider.http_client = httpx.AsyncClient(transport=transport, timeout=2)

        response = _post_event(
            client,
            settings,
            {
                "post_type": "message",
                "message_type": "group",
                "self_id": int(BOT_QQ),
                "user_id": 88,
                "group_id": 99,
                "message_id": 999,
                "message": [
                    {"type": "at", "data": {"qq": BOT_QQ}},
                    {"type": "text", "data": {"text": "current-question"}},
                ],
            },
        )

    assert response == {"reply": "custom-history-reply"}
    assert [item["content"] for item in captured_messages[1:-1]] == [
        "history-3",
        "history-4",
        "history-5",
    ]


def test_ai_receives_trigger_and_previous_ten_chat_messages(tmp_path: Path) -> None:
    """AI 请求包含按时间正序排列的最近十条历史，当前消息位于最后。"""
    settings = build_settings(tmp_path)
    captured_messages: list[dict] = []

    def ai_handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if _is_intent_request(payload):
            return _intent_response()
        captured_messages.extend(payload["messages"])
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "上下文回复"}}]},
        )

    with TestClient(create_app(settings)) as client:
        headers = _headers(client)
        _create_bot(
            client,
            headers,
            connection_mode="http",
            http_url="http://napcat.example:3000",
        )
        client.put(f"/api/bots/{BOT_QQ}/ai", headers=headers, json=_ai_payload())

        async def seed_history() -> None:
            async with client.app.state.container.database.session_factory() as session:
                now = beijing_now()
                rows = []
                for index in range(1, 12):
                    is_bot = index == 6
                    rows.append(
                        ChatMessage(
                            bot_qq=BOT_QQ,
                            post_type="message_sent" if is_bot else "message",
                            message_type="group",
                            message_id=f"history-{index}",
                            user_id=BOT_QQ if is_bot else "9000",
                            sender_user_id=BOT_QQ if is_bot else "9000",
                            group_id="99",
                            sender_nickname="",
                            message_text=(
                                "机器人历史回复"
                                if is_bot
                                else f"历史消息{index}"
                            ),
                            event_time=now - timedelta(minutes=12 - index),
                        )
                    )
                session.add_all(rows)
                await session.commit()

        client.portal.call(seed_history)
        onebot = client.app.state.container.onebot_service
        provider = client.app.state.container.bot_ai_service.provider
        client.portal.call(onebot.http_client.aclose)
        client.portal.call(provider.http_client.aclose)
        transport = httpx.MockTransport(ai_handler)
        onebot.http_client = httpx.AsyncClient(transport=transport, timeout=2)
        provider.http_client = httpx.AsyncClient(transport=transport, timeout=2)

        response = _post_event(
            client,
            settings,
            {
                "post_type": "message",
                "message_type": "group",
                "self_id": int(BOT_QQ),
                "user_id": 88,
                "group_id": 99,
                "message_id": 999,
                "message": [
                    {"type": "at", "data": {"qq": BOT_QQ}},
                    {"type": "text", "data": {"text": "当前问题"}},
                ],
            },
        )

    assert response == {"reply": "上下文回复"}
    assert len(captured_messages) == 12
    assert captured_messages[0] == {"role": "system", "content": "简洁回答"}
    history = captured_messages[1:-1]
    assert [item["content"] for item in history] == [
        "历史消息2",
        "历史消息3",
        "历史消息4",
        "历史消息5",
        "机器人历史回复",
        "历史消息7",
        "历史消息8",
        "历史消息9",
        "历史消息10",
        "历史消息11",
    ]
    assert history[4]["role"] == "assistant"
    assert captured_messages[-1] == {"role": "user", "content": "当前问题"}
