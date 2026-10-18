"""AI 语音消息转文字和回复流程测试。"""

import hashlib
import hmac
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.application import create_app
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


def test_group_voice_at_bot_is_transcribed_before_ai(tmp_path: Path) -> None:
    """群聊 @ 机器人的纯语音消息会先转写，再把转写文本发给 AI。"""
    settings = build_settings(tmp_path)
    ai_prompts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/fetch_ptt_text"):
            assert json.loads(request.content)["message_id"] == "901"
            return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {"text": "语音里的问题"}})
        payload = json.loads(request.content)
        if _is_intent_request(payload):
            return _intent_response()
        ai_prompts.append(payload["messages"][-1]["content"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "语音回复"}}]})

    with TestClient(create_app(settings)) as client:
        headers = _headers(client)
        _create_bot(
            client,
            headers,
            connection_mode="http",
            http_url="http://napcat.example:3000",
        )
        client.put(f"/api/bots/{BOT_QQ}/ai", headers=headers, json=_ai_payload())
        onebot = client.app.state.container.onebot_service
        provider = client.app.state.container.bot_ai_service.provider
        client.portal.call(onebot.http_client.aclose)
        client.portal.call(provider.http_client.aclose)
        transport = httpx.MockTransport(handler)
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
                "message_id": 901,
                "message": [
                    {"type": "at", "data": {"qq": BOT_QQ}},
                    {"type": "record", "data": {"file": "voice.amr"}},
                ],
            },
        )

    assert response == {"reply": "语音回复"}
    assert ai_prompts == ["语音里的问题"]


def test_private_voice_is_transcribed_before_ai(tmp_path: Path) -> None:
    """私聊纯语音消息会转写后直接触发 AI。"""
    settings = build_settings(tmp_path)
    ai_prompts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/fetch_ptt_text"):
            return httpx.Response(200, json={"status": "ok", "data": "私聊语音"})
        payload = json.loads(request.content)
        if _is_intent_request(payload):
            return _intent_response()
        ai_prompts.append(payload["messages"][-1]["content"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "收到"}}]})

    with TestClient(create_app(settings)) as client:
        headers = _headers(client)
        _create_bot(
            client,
            headers,
            connection_mode="http",
            http_url="http://napcat.example:3000",
        )
        client.put(f"/api/bots/{BOT_QQ}/ai", headers=headers, json=_ai_payload())
        onebot = client.app.state.container.onebot_service
        provider = client.app.state.container.bot_ai_service.provider
        client.portal.call(onebot.http_client.aclose)
        client.portal.call(provider.http_client.aclose)
        transport = httpx.MockTransport(handler)
        onebot.http_client = httpx.AsyncClient(transport=transport, timeout=2)
        provider.http_client = httpx.AsyncClient(transport=transport, timeout=2)

        response = _post_event(
            client,
            settings,
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": int(BOT_QQ),
                "user_id": 88,
                "message_id": 902,
                "message": [{"type": "record", "data": {"file": "voice.amr"}}],
            },
        )

    assert response == {"reply": "收到"}
    assert ai_prompts == ["私聊语音"]


def test_voice_transcription_failure_skips_ai(tmp_path: Path) -> None:
    """语音转写失败时不把 CQ 码当作文本发送给 AI。"""
    settings = build_settings(tmp_path)
    ai_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal ai_calls
        if request.url.path.endswith("/fetch_ptt_text"):
            return httpx.Response(200, json={"status": "failed", "retcode": 100, "data": None})
        ai_calls += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "不应回复"}}]})

    with TestClient(create_app(settings)) as client:
        headers = _headers(client)
        _create_bot(
            client,
            headers,
            connection_mode="http",
            http_url="http://napcat.example:3000",
        )
        client.put(f"/api/bots/{BOT_QQ}/ai", headers=headers, json=_ai_payload())
        onebot = client.app.state.container.onebot_service
        provider = client.app.state.container.bot_ai_service.provider
        client.portal.call(onebot.http_client.aclose)
        client.portal.call(provider.http_client.aclose)
        transport = httpx.MockTransport(handler)
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
                "message_id": 903,
                "message": [
                    {"type": "at", "data": {"qq": BOT_QQ}},
                    {"type": "record", "data": {"file": "voice.amr"}},
                ],
            },
        )

    assert response == {}
    assert ai_calls == 0
