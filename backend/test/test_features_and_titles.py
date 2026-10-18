"""系统功能列表和 AI 专属头衔命令测试。"""

import asyncio
import hashlib
import hmac
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.application import create_app
from test_service import build_settings, login


def _signed_event(client: TestClient, event: dict) -> dict:
    """通过 HTTP OneBot 入口提交签名事件。"""
    raw = json.dumps(event, ensure_ascii=False).encode()
    token = client.app.state.container.settings.napcat_token
    signature = "sha1=" + hmac.new(token.encode(), raw, hashlib.sha1).hexdigest()
    return client.post(
        "/onebot/v11/events",
        content=raw,
        headers={"x-signature": signature},
    ).json()


def _configure_bot(client: TestClient) -> str:
    """创建可接收群事件的测试机器人并返回登录令牌。"""
    token = login(client, "admin", "admin-password")
    response = client.post(
        "/api/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"bot_qq": "123456", "group_mode": "all", "is_enabled": True},
    )
    assert response.json()["success"] is True
    return token


def _title_event(message: str) -> dict:
    """构造专属头衔群消息事件。"""
    return {
        "post_type": "message",
        "message_type": "group",
        "self_id": 123456,
        "user_id": 778899,
        "group_id": 9988,
        "message_id": 456,
        "raw_message": message,
        "message": [{"type": "text", "data": {"text": message}}],
        "sender": {"user_id": 778899, "nickname": "测试用户", "role": "member"},
    }


def _patch_title_ai(client: TestClient, prompts: list[str]) -> None:
    """将测试机器人的 AI 固定为可观测的四字头衔生成器。"""
    async def active_config(_session, _bot_qq):
        return {"followup_count": 3}

    async def generate(_config, prompt, *_args):
        prompts.append(prompt)
        return "星河守护者"

    client.app.state.container.bot_ai_service.active_runtime_config = active_config
    client.app.state.container.bot_ai_service.generate = generate


def test_feature_list_is_initialized_and_read_only(tmp_path: Path) -> None:
    """启动会初始化功能数据，登录后可查询功能列表。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        token = login(client, "admin", "admin-password")
        response = client.get(
            "/api/features", headers={"Authorization": f"Bearer {token}"}
        ).json()
        assert response["success"] is True
        assert response["data"]
        title_feature = next(
            item for item in response["data"] if item["code"] == "ai_special_title"
        )
        assert title_feature["name"] == "AI 专属头衔"
        assert title_feature["trigger_keyword"] == "#头衔 [可选头衔]（支持空格）"
        assert {item["code"] for item in response["data"]} >= {
            "ai_chat",
            "auto_reply",
            "welcome_message",
            "forbidden_words",
            "api_center",
        }


def test_http_title_command_generates_and_sets_title(tmp_path: Path) -> None:
    """HTTP 头衔命令支持空格，调用动作且不发送普通 AI 回复。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        _configure_bot(client)
        prompts: list[str] = []
        _patch_title_ai(client, prompts)
        calls: list[tuple[str, dict]] = []

        async def fake_call_action(action: str, params: dict, **_kwargs):
            calls.append((action, params))
            return {"status": "ok", "retcode": 0}

        client.app.state.container.onebot_service.call_action = fake_call_action
        assert _signed_event(client, _title_event("#头衔")) == {}
        assert _signed_event(client, _title_event("# 头 衔")) == {}
        assert _signed_event(client, _title_event("#头衔 王者")) == {}
        assert len(prompts) == 2
        assert all("测试用户" in prompt and "不超过4个汉字" in prompt for prompt in prompts)
        assert calls == [
            (
                "set_group_special_title",
                {"group_id": 9988, "user_id": 778899, "special_title": "星河守护"},
            ),
            (
                "set_group_special_title",
                {"group_id": 9988, "user_id": 778899, "special_title": "星河守护"},
            ),
            (
                "set_group_special_title",
                {"group_id": 9988, "user_id": 778899, "special_title": "王者"},
            ),
        ]


def test_websocket_title_command_uses_current_session(tmp_path: Path) -> None:
    """WebSocket 头衔命令异步调用当前连接的设置动作。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        _configure_bot(client)
        prompts: list[str] = []
        _patch_title_ai(client, prompts)
        calls: list[tuple[str, dict, object]] = []

        async def fake_reverse_call(action: str, params: dict, **kwargs):
            calls.append((action, params, kwargs.get("session")))
            return {"status": "ok", "retcode": 0}

        service = client.app.state.container.onebot_service
        service.reverse_manager.call = fake_reverse_call

        async def dispatch() -> None:
            await service.handle_websocket_event(
                _title_event("# 头衔 统帅"),
                SimpleNamespace(self_id="123456"),
            )
            await asyncio.sleep(0.05)

        client.portal.call(dispatch)
        assert len(prompts) == 0
        assert len(calls) == 1
        assert calls[0][0] == "set_group_special_title"
        assert calls[0][1] == {
            "group_id": 9988,
            "user_id": 778899,
            "special_title": "统帅",
        }
        assert calls[0][2].self_id == "123456"
