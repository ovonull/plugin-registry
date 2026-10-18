"""测试引用其他用户消息时的 AI 上下文拼接。"""

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


def test_group_at_with_other_user_quote_sends_both_contents(tmp_path: Path) -> None:
    """@ 机器人并引用其他用户时，AI 请求包含引用内容和当前问题。"""
    settings = build_settings(tmp_path)
    prompts: list[str] = []

    def ai_handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if _is_intent_request(payload):
            return _intent_response()
        prompts.append(payload["messages"][-1]["content"])
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "已根据引用内容回答"}}]},
        )

    def napcat_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/get_msg"):
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "retcode": 0,
                    "data": {
                        "sender": {"user_id": "9988"},
                        "message": [
                            {"type": "text", "data": {"text": "其他用户的原话"}}
                        ],
                    },
                },
            )
        raise AssertionError(f"不应调用 NapCat 动作：{request.url}")

    with TestClient(create_app(settings)) as client:
        headers = _headers(client)
        _create_bot(
            client,
            headers,
            connection_mode="http",
            http_url="http://napcat.example:3000",
        )
        client.put(f"/api/bots/{BOT_QQ}/ai", headers=headers, json=_ai_payload())
        provider = client.app.state.container.bot_ai_service.provider
        client.portal.call(provider.http_client.aclose)
        provider.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(ai_handler), timeout=2
        )
        onebot = client.app.state.container.onebot_service
        client.portal.call(onebot.http_client.aclose)
        onebot.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(napcat_handler), timeout=2
        )

        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": int(BOT_QQ),
            "user_id": 88,
            "group_id": 99,
            "message": [
                {"type": "reply", "data": {"id": "1144"}},
                {"type": "at", "data": {"qq": BOT_QQ}},
                {"type": "text", "data": {"text": " 请解释一下"}},
            ],
        }
        raw = json.dumps(event, ensure_ascii=False).encode("utf-8")
        signature = "sha1=" + hmac.new(
            settings.napcat_token.encode(), raw, hashlib.sha1
        ).hexdigest()
        response = client.post(
            "/onebot/v11/events",
            content=raw,
            headers={"x-signature": signature},
        ).json()

    assert response == {"reply": "已根据引用内容回答"}
    assert prompts == ["引用内容：其他用户的原话\n当前消息：请解释一下"]
