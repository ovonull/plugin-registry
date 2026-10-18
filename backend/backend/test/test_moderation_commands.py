"""机器人管理员艾特禁言命令测试。"""

import asyncio
import hashlib
import hmac
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.application import create_app
from app.services.onebot_service import OneBotService
from test_service import build_settings, login


def test_moderation_command_parser_accepts_string_cq_message() -> None:
    """字符串 CQ 消息没有 message 数组时仍能提取艾特目标。"""
    parsed = OneBotService._parse_moderation_command(
        {
            "message_type": "group",
            "self_id": 123456,
            "message": "[CQ:at,qq=2457859130] 禁言",
        }
    )
    assert parsed == ("ban", ["2457859130"])


def test_moderation_command_parser_accepts_target_before_command() -> None:
    """艾特机器人后在文本中紧接目标 QQ 和命令也能解析。"""
    parsed = OneBotService._parse_moderation_command(
        {
            "message_type": "group",
            "self_id": 3668943488,
            "message": [
                {"type": "at", "data": {"qq": "3668943488"}},
                {"type": "text", "data": {"text": " 2528251483解除禁言"}},
            ],
        }
    )
    assert parsed == ("unban", ["2528251483"])


def test_moderation_command_parser_ignores_reply_segment() -> None:
    """带回复段和艾特目标的禁言消息可正常解析。"""
    parsed = OneBotService._parse_moderation_command(
        {
            "message_type": "group",
            "self_id": 3668943488,
            "message": [
                {"type": "reply", "data": {"id": "466990535"}},
                {"type": "at", "data": {"qq": "3765105827"}},
                {"type": "text", "data": {"text": " 禁言"}},
            ],
        }
    )
    assert parsed == ("ban", ["3765105827"])


def _configure_bot(client: TestClient, admin_qq: str = "778899") -> None:
    """创建带群管理命令管理员的测试机器人。"""
    token = login(client, "admin", "admin-password")
    response = client.post(
        "/api/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "bot_qq": "123456",
            "admin_qq": admin_qq,
            "group_mode": "all",
            "is_enabled": True,
        },
    )
    assert response.json()["success"] is True


def _event(command: str, user_id: int = 778899) -> dict:
    """构造包含两个艾特目标的群管理命令事件。"""
    return {
        "post_type": "message",
        "message_type": "group",
        "self_id": 123456,
        "user_id": user_id,
        "group_id": 9988,
        "message_id": 456,
        "raw_message": f"[CQ:at,qq=2457859130] [CQ:at,qq=1655226948] {command}",
        "message": [
            {"type": "at", "data": {"qq": "2457859130"}},
            {"type": "text", "data": {"text": " "}},
            {"type": "at", "data": {"qq": "1655226948"}},
            {"type": "text", "data": {"text": f" {command}"}},
        ],
        "sender": {"user_id": user_id, "role": "member"},
    }


def _post_event(client: TestClient, event: dict) -> dict:
    """向 HTTP OneBot 入口提交签名事件。"""
    raw = json.dumps(event, ensure_ascii=False).encode()
    token = client.app.state.container.settings.napcat_token
    signature = "sha1=" + hmac.new(token.encode(), raw, hashlib.sha1).hexdigest()
    return client.post(
        "/onebot/v11/events",
        content=raw,
        headers={"x-signature": signature},
    ).json()


def test_http_admin_mention_ban_and_unban_targets(tmp_path: Path) -> None:
    """配置的管理员可艾特多个成员执行禁言和解除禁言，并收到结果通知。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        _configure_bot(client)
        calls: list[tuple[str, dict]] = []

        async def fake_call_action(action: str, params: dict, **_kwargs):
            calls.append((action, params.copy()))
            return {"status": "ok", "retcode": 0}

        client.app.state.container.onebot_service.call_action = fake_call_action
        assert _post_event(client, _event("禁言")) == {}
        assert _post_event(client, _event("解除禁言")) == {}

    assert calls == [
        ("set_group_ban", {"group_id": 9988, "user_id": "2457859130", "duration": 300}),
        ("set_group_ban", {"group_id": 9988, "user_id": "1655226948", "duration": 300}),
        ("send_group_msg", {"group_id": 9988, "message": "禁言成功：2457859130, 1655226948"}),
        ("set_group_ban", {"group_id": 9988, "user_id": "2457859130", "duration": 0}),
        ("set_group_ban", {"group_id": 9988, "user_id": "1655226948", "duration": 0}),
        ("send_group_msg", {"group_id": 9988, "message": "解除禁言成功：2457859130, 1655226948"}),
    ]


def test_http_non_admin_mention_command_is_ignored(tmp_path: Path) -> None:
    """非配置管理员发送同类消息时不调用群管理动作。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        _configure_bot(client)
        calls: list[tuple[str, dict]] = []

        async def fake_call_action(action: str, params: dict, **_kwargs):
            calls.append((action, params.copy()))
            return {"status": "ok", "retcode": 0}

        client.app.state.container.onebot_service.call_action = fake_call_action
        assert _post_event(client, _event("禁言", user_id=112233)) == {}

    assert calls == []


def test_websocket_admin_mention_command_uses_current_session(tmp_path: Path) -> None:
    """WebSocket 管理员命令通过当前 NapCat 连接执行并发送通知。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        _configure_bot(client)
        calls: list[tuple[str, dict, object]] = []
        service = client.app.state.container.onebot_service

        async def fake_reverse_call(action: str, params: dict, **kwargs):
            calls.append((action, params.copy(), kwargs.get("session")))
            return {"status": "ok", "retcode": 0}

        service.reverse_manager.call = fake_reverse_call

        async def dispatch() -> None:
            await service.handle_websocket_event(
                _event("禁言"), SimpleNamespace(self_id="123456")
            )
            await asyncio.sleep(0.05)

        client.portal.call(dispatch)

    assert [item[0] for item in calls] == [
        "set_group_ban",
        "set_group_ban",
        "send_group_msg",
    ]
    assert all(item[2].self_id == "123456" for item in calls)
    assert calls[0][1]["duration"] == 300
