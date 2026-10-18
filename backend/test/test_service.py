"""管理接口与 OneBot HTTP/反向 WebSocket 协议测试。"""

import asyncio
import hashlib
import hmac
import json
from datetime import timedelta
from pathlib import Path
import secrets
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.application import create_app
from app.common.config import GEETEST_PRIVATE_KEY, Settings
from app.common.time_utils import beijing_now
from app.models.entities import ChatMessage, EmailCode, GeetestChallenge
from app.services.email_service import EmailService


def build_settings(tmp_path: Path, token: str = "napcat-secret") -> Settings:
    """构造隔离测试数据库配置。"""
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        admin_username="admin",
        admin_password="admin-password",
        napcat_token=token,
        service_token="service-secret",
        database_retry_count=2,
        database_retry_interval=1,
        # 本机 SQLite 建库偶发超过默认的 10 秒，放宽超时避免测试出现假失败。
        database_connect_timeout=30,
        session_hours=2,
        onebot_action_timeout=2,
    )


def login(client: TestClient, username: str, password: str) -> str:
    """登录并返回 Bearer 令牌。"""
    challenge = seed_challenge(client)
    response = client.post(
        "/api/auth/login",
        json={
            "login_type": "username_password",
            "username": username,
            "password": password,
            "geetest_challenge": challenge,
        },
    )
    assert response.json()["success"] is True
    return response.json()["data"]["token"]


def seed_challenge(client: TestClient, consumed: bool = False) -> str:
    """直接写入已通过的 challenge，避免自动化测试访问极验上游。"""
    challenge = secrets.token_hex(16)

    async def seed() -> None:
        async with client.app.state.container.database.session_factory() as session:
            session.add(
                GeetestChallenge(
                    challenge=challenge,
                    is_verified=True,
                    verified_at=beijing_now(),
                    consumed_at=beijing_now() if consumed else None,
                    expires_at=beijing_now() + timedelta(minutes=5),
                )
            )
            await session.commit()

    client.portal.call(seed)
    return challenge


def register_user(
    client: TestClient,
    username: str,
    password: str,
    email: str,
    nickname: str,
) -> dict:
    """写入注册验证码后调用真实注册接口。"""
    challenge = secrets.token_hex(16)
    code = "123456"

    async def seed() -> None:
        async with client.app.state.container.database.session_factory() as session:
            session.add(
                EmailCode(
                    email=email,
                    purpose="register",
                    challenge=challenge,
                    code_hash=EmailService._code_hash(email, "register", code),
                    expires_at=beijing_now() + timedelta(minutes=10),
                )
            )
            await session.commit()

    client.portal.call(seed)
    return client.post(
        "/api/auth/register",
        json={
            "username": username,
            "nickname": nickname,
            "email": email,
            "password": password,
            "email_code": code,
            "geetest_challenge": challenge,
        },
    ).json()


def test_user_scope_and_rule_crud(tmp_path: Path) -> None:
    """普通用户只能看见自己的规则，规则删除使用软删除。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        register = register_user(client, "alice", "alice-pass1", "alice@example.com", "Alice")
        assert register["success"] is True
        alice_token = login(client, "alice", "alice-pass1")
        headers = {"Authorization": f"Bearer {alice_token}"}
        client.post(
            "/api/bots",
            headers=headers,
            json={"bot_qq": "123456", "group_mode": "all", "is_enabled": True},
        )
        created = client.post(
            "/api/auto_replies",
            headers=headers,
            json={"keyword": "帮助", "bot_qq": "123456", "reply_content": "请查看帮助"},
        )
        assert created.json()["success"] is True
        listed = client.get("/api/auto_replies", headers=headers)
        assert listed.json()["data"]["total"] == 1

        register_user(client, "bob", "bob-password1", "bob@example.com", "Bob")
        bob_token = login(client, "bob", "bob-password1")
        denied = client.post(
            "/api/auto_replies",
            headers={"Authorization": f"Bearer {bob_token}"},
            json={"keyword": "other", "bot_qq": "123456", "reply_content": "denied"},
        ).json()
        assert denied["success"] is False
        assert denied["code"] == 40332

        admin_token = login(client, "admin", "admin-password")
        client.app.state.container.onebot_service.reverse_manager._sessions[
            "test-connection"
        ] = SimpleNamespace(self_id="987654")
        admin_list = client.get(
            "/api/auto_replies", headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert admin_list.json()["data"]["total"] == 1
        dashboard = client.get(
            "/api/dashboard?days=7",
            headers={"Authorization": f"Bearer {admin_token}"},
        ).json()
        assert dashboard["success"] is True
        assert "recent_rules" in dashboard["data"]
        assert dashboard["data"]["public_service_url"] == "http://testserver:8000"
        assert dashboard["data"]["connected_bot_count"] == 1
        assert dashboard["data"]["connected_bots"] == ["987654"]
        user_dashboard = client.get(
            "/api/dashboard?days=7", headers=headers
        ).json()
        assert user_dashboard["data"]["connected_bot_count"] == 1
        assert user_dashboard["data"]["connected_bots"] == []
        deleted = client.post(f"/api/auto_replies/{created.json()['data']['id']}/delete", headers=headers)
        assert deleted.json()["success"] is True
        assert client.get("/api/auto_replies", headers=headers).json()["data"]["total"] == 0


def test_http_event_signature_and_quick_reply(tmp_path: Path, caplog) -> None:
    """HTTP 上报验签后能返回快速回复，并完整记录请求与响应。"""
    caplog.set_level(20, logger="uvicorn.error")
    settings = build_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        token = login(client, "admin", "admin-password")
        headers = {"Authorization": f"Bearer {token}"}
        client.post(
            "/api/bots",
            headers=headers,
            json={"bot_qq": "123456", "group_mode": "all", "is_enabled": True},
        )
        created = client.post(
            "/api/auto_replies",
            headers=headers,
            json={"keyword": "你好", "bot_qq": "123456", "reply_content": "你好呀"},
        )
        assert created.json()["success"] is True
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": 123456,
            "user_id": 88,
            "group_id": 99,
            "message_id": 321,
            "raw_message": "你好，请问在吗",
            "message": [
                {"type": "at", "data": {"qq": "123456"}},
                {"type": "text", "data": {"text": "你好，请问在吗"}},
            ],
            "sender": {
                "user_id": 88,
                "nickname": "测试用户",
                "card": "测试群名片",
                "role": "member",
            },
        }
        raw = json.dumps(event, ensure_ascii=False).encode()
        signature = "sha1=" + hmac.new(settings.napcat_token.encode(), raw, hashlib.sha1).hexdigest()
        response = client.post(
            "/onebot/v11/events?source=napcat",
            content=raw,
            headers={
                "x-signature": signature,
                "x-log-marker": "complete-request-header",
            },
        )
        assert response.status_code == 200
        assert response.json() == {"reply": "你好呀"}
        assert "complete-request-header" in caplog.text
        assert "你好，请问在吗" in caplog.text
        assert '"query_string": "source=napcat"' in caplog.text
        assert '"reply": "你好呀"' in caplog.text
        assert '"http_status": 200' in caplog.text
        async def load_messages() -> list[ChatMessage]:
            async with client.app.state.container.database.session_factory() as session:
                result = await session.execute(
                    select(ChatMessage).where(ChatMessage.is_deleted.is_(False))
                )
                return list(result.scalars())

        messages = client.portal.call(load_messages)
        assert len(messages) == 1
        assert messages[0].bot_qq == "123456"
        assert messages[0].owner_id is not None
        assert messages[0].group_id == "99"
        assert messages[0].sender_user_id == "88"
        assert messages[0].sender_nickname == "测试用户"
        assert messages[0].message_text == "你好，请问在吗"
        assert json.loads(messages[0].event_data)["raw_message"] == "你好，请问在吗"
        invalid = client.post("/onebot/v11/events", content=raw, headers={"x-signature": "sha1=invalid"})
        assert invalid.json()["retcode"] == 1403


def test_reverse_websocket_auto_reply_and_action(tmp_path: Path) -> None:
    """反向 WS 能接收事件、自动发动作，并关联业务动作响应。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        admin_token = login(client, "admin", "admin-password")
        client.post(
            "/api/bots",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"bot_qq": "123456", "group_mode": "all", "is_enabled": True},
        )
        client.post(
            "/api/auto_replies",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"keyword": "ping", "bot_qq": "123456", "reply_content": "pong"},
        )
        with client.websocket_connect("/onebot/v11/ws", headers={"Authorization": "Bearer napcat-secret", "X-Self-ID": "123456"}) as napcat:
            napcat.send_json(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "self_id": 123456,
                    "user_id": 88,
                    "group_id": 99,
                    "message_id": 456,
                    "raw_message": "ping",
                    "message": [
                        {"type": "at", "data": {"qq": "123456"}},
                        {"type": "text", "data": {"text": "ping"}},
                    ],
                }
            )
            auto_action = napcat.receive_json()
            assert auto_action["action"] == "send_group_msg"
            assert auto_action["params"]["message"] == [
                {"type": "reply", "data": {"id": "456"}},
                {"type": "text", "data": {"text": "pong"}},
            ]
            async def load_messages() -> list[ChatMessage]:
                async with client.app.state.container.database.session_factory() as session:
                    result = await session.execute(
                        select(ChatMessage).where(ChatMessage.is_deleted.is_(False))
                    )
                    return list(result.scalars())

            messages = client.portal.call(load_messages)
            assert len(messages) == 1
            assert messages[0].source == "websocket"
            assert messages[0].bot_account_id is not None
            assert messages[0].message_text == "ping"
            napcat.send_json({"status": "ok", "retcode": 0, "data": {"message_id": 1}, "echo": auto_action["echo"]})

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    lambda: client.post(
                        "/onebot/api/get_login_info?access_token=service-secret",
                        json={"echo": "hello"},
                    )
                )
                action = napcat.receive_json()
                assert action["action"] == "get_login_info"
                napcat.send_json({"status": "ok", "retcode": 0, "data": {"user_id": 123456}, "echo": action["echo"]})
                result = future.result(timeout=3).json()
                assert result["status"] == "ok"
                assert result["echo"] == "hello"


def test_reverse_websocket_poke_auto_reply(tmp_path: Path) -> None:
    """反向 WS 收到戳一戳机器人事件时按规则发送群消息。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        headers = {"Authorization": f"Bearer {login(client, 'admin', 'admin-password')}"}
        client.post(
            "/api/bots",
            headers=headers,
            json={"bot_qq": "123456", "group_mode": "all", "is_enabled": True},
        )
        client.post(
            "/api/auto_replies",
            headers=headers,
            json={"keyword": "戳一戳", "bot_qq": "123456", "reply_content": "别戳啦"},
        )
        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer napcat-secret", "X-Self-ID": "123456"},
        ) as napcat:
            napcat.send_json(
                {
                    "post_type": "notice",
                    "notice_type": "notify",
                    "sub_type": "poke",
                    "self_id": 123456,
                    "user_id": 88,
                    "group_id": 99,
                    "target_id": 123456,
                }
            )
            action = napcat.receive_json()
            assert action["action"] == "send_group_msg"
            assert action["params"] == {"group_id": 99, "message": "别戳啦"}
            napcat.send_json(
                {"status": "ok", "retcode": 0, "data": {}, "echo": action["echo"]}
            )


def test_bot_group_allowlist_and_action_catalog(tmp_path: Path) -> None:
    """Configured bot allowlists gate group replies and expose the full action UI catalog."""
    settings = build_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        token = login(client, "admin", "admin-password")
        headers = {"Authorization": f"Bearer {token}"}
        bot = client.post(
            "/api/bots",
            headers=headers,
            json={
                "bot_qq": "123456",
                "nickname": "test bot",
                "group_mode": "allowlist",
                "is_enabled": True,
            },
        ).json()
        assert bot["success"] is True
        assert client.get("/api/bots", headers=headers).json()["data"][0]["bot_qq"] == "123456"

        client.post(
            "/api/auto_replies",
            headers=headers,
            json={"keyword": "hello", "bot_qq": "123456", "reply_content": "world"},
        )
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": 123456,
            "user_id": 88,
            "group_id": 99,
            "raw_message": "hello",
        }
        raw = json.dumps(event).encode()
        signature = "sha1=" + hmac.new(
            settings.napcat_token.encode(), raw, hashlib.sha1
        ).hexdigest()
        default_allowed = client.post(
            "/onebot/v11/events", content=raw, headers={"x-signature": signature}
        )
        assert default_allowed.json() == {"reply": "world"}

        group = client.post(
            "/api/bots/123456/groups",
            headers=headers,
            json={"group_id": "99", "group_name": "test group", "is_enabled": False},
        ).json()
        assert group["success"] is True
        blocked = client.post(
            "/onebot/v11/events", content=raw, headers={"x-signature": signature}
        )
        assert blocked.json() == {}

        group = client.post(
            "/api/bots/123456/groups",
            headers=headers,
            json={"group_id": "99", "group_name": "test group", "is_enabled": True},
        ).json()
        assert group["success"] is True
        allowed = client.post(
            "/onebot/v11/events", content=raw, headers={"x-signature": signature}
        )
        assert allowed.json() == {"reply": "world"}

        catalog = client.get("/api/bots/catalog", headers=headers).json()
        assert catalog["success"] is True
        assert len(catalog["data"]) > 100
        actions = {item["action"]: item for item in catalog["data"]}
        assert {
            "forward_group_single_msg",
            "friend_poke",
            "get_ai_characters",
            "send_ark_share",
            "send_group_ark_share",
            "send_group_sign",
        }.issubset(actions)
        assert actions["forward_group_single_msg"]["requires_group"] is True
        assert actions["set_group_add_request"]["requires_group"] is False
        assert actions["get_group_system_msg"]["requires_group"] is False
        assert actions["get_ai_characters"]["requires_group"] is True
        assert actions["get_ai_characters"]["example"] == {}
        assert actions["set_group_kick"]["dangerous"] is True
        assert actions["forward_msg"]["compatibility"] is True
        assert actions["set_group_add_request"]["example"]["flag"] == "flag_123"
        assert actions["send_group_msg"]["example"] == {"message": "hello"}
        assert all(isinstance(item["example"], dict) for item in catalog["data"])


def test_business_websocket_group_action_is_scoped(tmp_path: Path) -> None:
    """Business WebSocket actions cannot bypass a bot's group allowlist."""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        token = login(client, "admin", "admin-password")
        headers = {"Authorization": f"Bearer {token}"}
        client.post(
            "/api/bots",
            headers=headers,
            json={"bot_qq": "123456", "group_mode": "allowlist", "is_enabled": True},
        )
        client.post(
            "/api/bots/123456/groups",
            headers=headers,
            json={"group_id": "99", "group_name": "test group", "is_enabled": False},
        )
        with client.websocket_connect("/ws?access_token=service-secret") as service:
            service.send_json(
                {
                    "action": "send_group_msg",
                    "self_id": "123456",
                    "params": {"group_id": 99, "message": "blocked"},
                }
            )
            result = service.receive_json()
            assert result["retcode"] == 40331


def test_per_bot_http_transport(tmp_path: Path) -> None:
    """A bot-specific HTTP endpoint and token override global transport settings."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["self_id"] = request.headers.get("x-self-id")
        return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {}})

    with TestClient(create_app(build_settings(tmp_path))) as client:
        token = login(client, "admin", "admin-password")
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post(
            "/api/bots",
            headers=headers,
            json={
                "bot_qq": "123456",
                "connection_mode": "http",
                "http_url": "http://bot.example:3000",
                "access_token": "bot-secret",
                "group_mode": "all",
                "is_enabled": True,
            },
        ).json()
        assert created["success"] is True
        client.app.state.container.onebot_service.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=2
        )
        response = client.post(
            "/api/bots/123456/actions",
            headers=headers,
            json={"bot_qq": "123456", "action": "get_login_info", "params": {}},
        ).json()
        assert response["success"] is True
        assert captured == {
            "url": "http://bot.example:3000/get_login_info",
            "authorization": "Bearer bot-secret",
            "self_id": "123456",
        }


def test_email_password_and_code_login(tmp_path: Path) -> None:
    """注册后支持邮箱密码和一次性邮箱验证码登录。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        registered = register_user(
            client, "email_user", "email-pass1", "user@example.com", "邮箱用户"
        )
        assert registered["success"] is True

        password_challenge = seed_challenge(client)
        password_login = client.post(
            "/api/auth/login",
            json={
                "login_type": "email_password",
                "email": "user@example.com",
                "password": "email-pass1",
                "geetest_challenge": password_challenge,
            },
        ).json()
        assert password_login["success"] is True

        code_challenge = secrets.token_hex(16)
        code = "654321"

        async def seed_login_code() -> None:
            async with client.app.state.container.database.session_factory() as session:
                session.add(
                    EmailCode(
                        owner_id=registered["data"]["user"]["id"],
                        email="user@example.com",
                        purpose="login",
                        challenge=code_challenge,
                        code_hash=EmailService._code_hash(
                            "user@example.com", "login", code
                        ),
                        expires_at=beijing_now() + timedelta(minutes=10),
                    )
                )
                await session.commit()

        client.portal.call(seed_login_code)
        payload = {
            "login_type": "email_code",
            "email": "user@example.com",
            "email_code": code,
            "geetest_challenge": code_challenge,
        }
        first = client.post("/api/auth/login", json=payload).json()
        second = client.post("/api/auth/login", json=payload).json()
        assert first["success"] is True
        assert second["success"] is False
        assert second["code"] == 40051


def test_system_settings_admin_permission_and_mask(tmp_path: Path) -> None:
    """系统设置仅管理员可写，SMTP 密码返回时必须脱敏。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        admin_headers = {
            "Authorization": f"Bearer {login(client, 'admin', 'admin-password')}"
        }
        saved = client.put(
            "/api/settings/email/save",
            headers=admin_headers,
            json={
                "host": "smtp.example.com",
                "port": 465,
                "username": "sender@example.com",
                "password": "smtp-secret",
                "sender_name": "NapCat Test",
                "use_ssl": True,
            },
        ).json()
        assert saved["success"] is True
        assert saved["data"]["password"] == "******"
        loaded = client.get("/api/settings/email", headers=admin_headers).json()
        assert loaded["data"]["password"] == "******"

        user = register_user(
            client, "normal_user", "normal-pass1", "normal@example.com", "普通用户"
        )
        denied = client.get(
            "/api/settings/email",
            headers={"Authorization": f"Bearer {user['data']['token']}"},
        ).json()
        assert denied["success"] is False
        assert denied["code"] == 40310

        public = client.get("/api/settings/public").json()
        assert public["success"] is True
        assert set(public["data"]) == {"appearance", "introduction"}


def test_verified_email_code_registration_flow(
    tmp_path: Path, monkeypatch
) -> None:
    """极验结果只能发信一次，发送的验证码可完成真实注册流程。"""

    async def fake_send_smtp(
        _service: EmailService, _email: str, _code: str, _purpose: str
    ) -> None:
        return None

    monkeypatch.setattr(EmailService, "_send_smtp", fake_send_smtp)
    monkeypatch.setattr(
        "app.services.email_service.secrets.randbelow", lambda _upper: 123456
    )
    with TestClient(create_app(build_settings(tmp_path))) as client:
        challenge = seed_challenge(client)
        send_payload = {
            "email": "flow@example.com",
            "purpose": "register",
            "geetest_challenge": challenge,
        }
        sent = client.post("/api/auth/email-code", json=send_payload).json()
        reused = client.post("/api/auth/email-code", json=send_payload).json()
        assert sent["success"] is True
        assert reused["success"] is False
        assert reused["code"] == 40033

        registered = client.post(
            "/api/auth/register",
            json={
                "username": "flow_user",
                "email": "flow@example.com",
                "password": "flow-pass1",
                "email_code": "123456",
                "geetest_challenge": challenge,
            },
        ).json()
        assert registered["success"] is True
        assert registered["data"]["user"]["email_verified"] is True


def test_geetest_validate_accepts_sdk_challenge_without_register_row(
    tmp_path: Path,
) -> None:
    """二次校验以 GT3 SDK 返回值为准，不强制匹配初始化登记记录。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"seccode": "validated"})

    with TestClient(create_app(build_settings(tmp_path))) as client:
        geetest = client.app.state.container.geetest_service
        client.portal.call(geetest.http_client.aclose)
        geetest.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=2
        )
        challenge = secrets.token_hex(16)
        validate = hashlib.md5(
            (GEETEST_PRIVATE_KEY + "geetest" + challenge).encode("utf-8")
        ).hexdigest()
        payload = {
            "challenge": challenge,
            "validate": validate,
            "seccode": "sdk-seccode",
        }
        first = client.post("/api/geetest/validate", json=payload).json()
        second = client.post("/api/geetest/validate", json=payload).json()
        assert first["success"] is True
        assert second["success"] is False
        assert second["code"] == 40034
