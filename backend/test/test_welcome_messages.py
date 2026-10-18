"""进群欢迎词的权限、匹配优先级和 OneBot 传输测试。"""

from datetime import timedelta
import hashlib
import hmac
import json
from pathlib import Path
import secrets

import httpx
from fastapi.testclient import TestClient

from app.application import create_app
from app.common.config import Settings
from app.common.time_utils import beijing_now
from app.models.entities import GeetestChallenge


BOT_QQ = "123456"


def build_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'welcome.db'}",
        admin_username="admin",
        admin_password="admin-password",
        napcat_token="napcat-secret",
        database_retry_count=1,
        database_retry_interval=1,
        onebot_action_timeout=2,
    )


def login_admin(client: TestClient) -> str:
    challenge = secrets.token_hex(16)

    async def seed() -> None:
        async with client.app.state.container.database.session_factory() as session:
            session.add(
                GeetestChallenge(
                    challenge=challenge,
                    is_verified=True,
                    verified_at=beijing_now(),
                    expires_at=beijing_now() + timedelta(minutes=5),
                )
            )
            await session.commit()

    client.portal.call(seed)
    response = client.post(
        "/api/auth/login",
        json={
            "login_type": "username_password",
            "username": "admin",
            "password": "admin-password",
            "geetest_challenge": challenge,
        },
    )
    assert response.json()["success"] is True
    return response.json()["data"]["token"]


def post_event(client: TestClient, event: dict, headers: dict | None = None) -> dict:
    raw = json.dumps(event, ensure_ascii=False).encode()
    signature = "sha1=" + hmac.new(
        b"napcat-secret", raw, hashlib.sha1
    ).hexdigest()
    request_headers = {"x-signature": signature}
    if headers:
        request_headers.update(headers)
    return client.post("/onebot/v11/events", content=raw, headers=request_headers).json()


def test_welcome_rule_priority_and_reuse_after_soft_delete(tmp_path: Path) -> None:
    with TestClient(create_app(build_settings(tmp_path))) as client:
        headers = {"Authorization": f"Bearer {login_admin(client)}"}
        created_bot = client.post(
            "/api/bots",
            headers=headers,
            json={"bot_qq": BOT_QQ, "group_mode": "all", "is_enabled": True, "welcome_enabled": True},
        ).json()
        assert created_bot["success"] is True

        default = client.post(
            "/api/welcome_messages",
            headers=headers,
            json={"bot_qq": BOT_QQ, "welcome_content": "欢迎来到本群"},
        ).json()
        specific = client.post(
            "/api/welcome_messages",
            headers=headers,
            json={"bot_qq": BOT_QQ, "group_id": "99", "welcome_content": "欢迎加入 99 群"},
        ).json()
        assert default["success"] is True
        assert specific["success"] is True
        duplicate = client.post(
            "/api/welcome_messages",
            headers=headers,
            json={"bot_qq": BOT_QQ, "group_id": "99", "welcome_content": "重复"},
        ).json()
        assert duplicate["success"] is False
        assert duplicate["code"] == 40042

        exact_event = {
            "post_type": "notice",
            "notice_type": "group_increase",
            "self_id": int(BOT_QQ),
            "group_id": 99,
            "user_id": 888,
        }
        assert post_event(client, exact_event) == {
            "reply": "[CQ:at,qq=888] 欢迎加入 99 群"
        }
        fallback_event = {**exact_event, "group_id": 100}
        assert post_event(client, fallback_event) == {
            "reply": "[CQ:at,qq=888] 欢迎来到本群"
        }
        missing_self_id = {**fallback_event}
        missing_self_id.pop("self_id")
        assert post_event(client, missing_self_id, {"X-Self-ID": BOT_QQ}) == {
            "reply": "[CQ:at,qq=888] 欢迎来到本群"
        }

        deleted = client.post(
            f"/api/welcome_messages/{default['data']['id']}/delete", headers=headers
        ).json()
        assert deleted["success"] is True
        restored = client.post(
            "/api/welcome_messages",
            headers=headers,
            json={"bot_qq": BOT_QQ, "welcome_content": "欢迎回来"},
        ).json()
        assert restored["success"] is True
        assert restored["data"]["id"] == default["data"]["id"]


def test_websocket_welcome_mentions_new_member(tmp_path: Path) -> None:
    with TestClient(create_app(build_settings(tmp_path))) as client:
        headers = {"Authorization": f"Bearer {login_admin(client)}"}
        client.post(
            "/api/bots",
            headers=headers,
            json={"bot_qq": BOT_QQ, "group_mode": "all", "is_enabled": True, "welcome_enabled": True},
        )
        client.post(
            "/api/welcome_messages",
            headers=headers,
            json={"bot_qq": BOT_QQ, "group_id": "99", "welcome_content": "请查看群规"},
        )

        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer napcat-secret", "X-Self-ID": BOT_QQ},
        ) as napcat:
            napcat.send_json(
                {
                    "post_type": "notice",
                    "notice_type": "group_increase",
                    "self_id": int(BOT_QQ),
                    "group_id": 99,
                    "user_id": 888,
                }
            )
            action = napcat.receive_json()
            assert action["action"] == "send_group_msg"
            assert action["params"] == {
                "group_id": 99,
                "message": [
                    {"type": "at", "data": {"qq": "888"}},
                    {"type": "text", "data": {"text": " 请查看群规"}},
                ],
            }
            napcat.send_json(
                {"status": "ok", "retcode": 0, "data": {}, "echo": action["echo"]}
            )


def test_welcome_without_rule_falls_back_to_bot_ai(tmp_path: Path) -> None:
    def ai_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "欢迎新朋友加入！"}}]},
        )

    with TestClient(create_app(build_settings(tmp_path))) as client:
        headers = {"Authorization": f"Bearer {login_admin(client)}"}
        client.post(
            "/api/bots",
            headers=headers,
            json={"bot_qq": BOT_QQ, "group_mode": "all", "is_enabled": True, "welcome_enabled": True},
        )
        client.put(
            f"/api/bots/{BOT_QQ}/ai",
            headers=headers,
            json={
                "is_enabled": True,
                "provider_type": "openai_compatible",
                "base_url": "https://ai.example/v1",
                "api_key": "ai-secret",
                "model_name": "test-model",
                "system_prompt": "",
                "temperature": 0.7,
                "max_tokens": 1000,
            },
        )
        provider = client.app.state.container.bot_ai_service.provider
        client.portal.call(provider.http_client.aclose)
        provider.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(ai_handler), timeout=2
        )
        response = post_event(
            client,
            {
                "post_type": "notice",
                "notice_type": "group_increase",
                "self_id": int(BOT_QQ),
                "group_id": 99,
                "user_id": 888,
            },
        )
        assert response == {"reply": "[CQ:at,qq=888] 欢迎新朋友加入！"}
