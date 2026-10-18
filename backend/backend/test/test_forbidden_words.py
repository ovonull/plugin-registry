"""违禁词规则 CRUD、范围匹配和 HTTP 事件禁言测试。"""

import hashlib
import hmac
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.application import create_app
from test_bot_ai import _intent_response
from test_service import build_settings, login


def test_forbidden_word_list_crud_and_http_ban(tmp_path: Path) -> None:
    """逗号分隔词列表保存为一条规则，命中 HTTP 事件会调用禁言动作。"""
    settings = build_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        token = login(client, "admin", "admin-password")
        headers = {"Authorization": f"Bearer {token}"}
        bot = client.post(
            "/api/bots",
            headers=headers,
            json={"bot_qq": "123456", "group_mode": "all", "is_enabled": True},
        )
        assert bot.json()["success"] is True

        created = client.post(
            "/api/forbidden_words",
            headers=headers,
            json={
                "bot_qq": "123456",
                "group_id": "9988",
                "keywords": "违规词,广告,违规词",
                "mute_duration": 120,
            },
        ).json()
        assert created["success"] is True
        assert created["data"]["keyword"] == "违规词,广告"
        assert created["data"]["mute_enabled"] is True

        listed = client.get(
            "/api/forbidden_words",
            headers=headers,
            params={"bot_qq": "123456", "group_id": "9988"},
        ).json()
        assert listed["data"]["total"] == 1

        edited = client.put(
            f"/api/forbidden_words/{created['data']['id']}",
            headers=headers,
            json={
                "bot_qq": "123456",
                "group_id": "9988",
                "keyword": "编辑一,编辑二",
                "mute_duration": 120,
            },
        ).json()
        assert edited["success"] is True
        assert edited["data"]["keyword"] == "编辑一,编辑二"
        assert client.get("/api/forbidden_words", headers=headers).json()["data"]["total"] == 1

        calls: list[tuple[str, dict]] = []

        async def fake_call_action(action: str, params: dict, **_kwargs):
            calls.append((action, params))
            return {"status": "ok", "retcode": 0}

        client.app.state.container.onebot_service.call_action = fake_call_action
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": 123456,
            "user_id": 778899,
            "group_id": 9988,
            "message_id": 456,
            "raw_message": "请勿发布编辑二内容",
            "sender": {"user_id": 778899, "role": "member"},
        }
        raw = json.dumps(event, ensure_ascii=False).encode()
        signature = "sha1=" + hmac.new(
            settings.napcat_token.encode(), raw, hashlib.sha1
        ).hexdigest()
        response = client.post(
            "/onebot/v11/events",
            content=raw,
            headers={"x-signature": signature},
        )
        assert response.json() == {}
        assert calls == [
            ("delete_msg", {"message_id": 456}),
            ("set_group_ban", {"group_id": 9988, "user_id": 778899, "duration": 120}),
        ]

        event_without_message_id = dict(event)
        event_without_message_id.pop("message_id")
        raw_without_message_id = json.dumps(
            event_without_message_id, ensure_ascii=False
        ).encode()
        signature_without_message_id = "sha1=" + hmac.new(
            settings.napcat_token.encode(),
            raw_without_message_id,
            hashlib.sha1,
        ).hexdigest()
        response_without_message_id = client.post(
            "/onebot/v11/events",
            content=raw_without_message_id,
            headers={"x-signature": signature_without_message_id},
        )
        assert response_without_message_id.json() == {}
        assert calls == [
            ("delete_msg", {"message_id": 456}),
            ("set_group_ban", {"group_id": 9988, "user_id": 778899, "duration": 120}),
            ("set_group_ban", {"group_id": 9988, "user_id": 778899, "duration": 120}),
        ]

        deleted = client.post(
            "/api/forbidden_words/batch-delete",
            headers=headers,
            json={"ids": [created["data"]["id"]]},
        ).json()
        assert deleted["success"] is True
        assert deleted["data"]["deleted_count"] == 1
        assert client.get("/api/forbidden_words", headers=headers).json()["data"]["total"] == 0


def test_forbidden_word_ai_prompt_controls_mute(tmp_path: Path) -> None:
    """未命中关键词时 AI 返回 true 才禁言；命中本地关键词直接禁言不再调用 AI。"""
    settings = build_settings(tmp_path)
    ai_requests: list[dict] = []
    ai_results = iter([True, False, False])

    def ai_handler(request: httpx.Request) -> httpx.Response:
        ai_requests.append(json.loads(request.content))
        mute = next(ai_results)
        return _intent_response(
            mute=mute,
            mute_reason="测试" if mute else "",
            reason="禁言审核",
        )

    with TestClient(create_app(settings)) as client:
        token = login(client, "admin", "admin-password")
        headers = {"Authorization": f"Bearer {token}"}
        assert client.post(
            "/api/bots",
            headers=headers,
            json={"bot_qq": "123456", "group_mode": "all", "is_enabled": True},
        ).json()["success"]
        assert client.put(
            "/api/bots/123456/ai",
            headers=headers,
            json={
                "is_enabled": True,
                "provider_type": "openai_compatible",
                "base_url": "https://ai.example/v1",
                "api_key": "test-key",
                "model_name": "moderation-model",
                "max_tokens": 100,
            },
        ).json()["success"]
        created = client.post(
            "/api/forbidden_words",
            headers=headers,
            json={
                "bot_qq": "123456",
                "group_id": "9988",
                "keywords": "明显关键词",
                "mute_prompt": "判断消息是否需要禁言",
                "mute_duration": 120,
            },
        ).json()
        assert created["success"] is True
        assert created["data"]["mute_prompt"] == "判断消息是否需要禁言"

        provider = client.app.state.container.bot_ai_service.provider
        client.portal.call(provider.http_client.aclose)
        provider.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(ai_handler), timeout=2
        )
        calls: list[tuple[str, dict]] = []

        async def fake_call_action(action: str, params: dict, **_kwargs):
            calls.append((action, params))
            return {"status": "ok", "retcode": 0}

        client.app.state.container.onebot_service.call_action = fake_call_action

        def post_event(message_id: int) -> dict:
            event = {
                "post_type": "message",
                "message_type": "group",
                "self_id": 123456,
                "user_id": 778899,
                "group_id": 9988,
                "message_id": message_id,
                "raw_message": "这是一条需要审核的消息",
                "sender": {"user_id": 778899, "role": "member"},
            }
            raw = json.dumps(event, ensure_ascii=False).encode()
            signature = "sha1=" + hmac.new(
                settings.napcat_token.encode(), raw, hashlib.sha1
            ).hexdigest()
            return client.post(
                "/onebot/v11/events",
                content=raw,
                headers={"x-signature": signature},
            ).json()

        assert post_event(901) == {}
        assert calls == [
            ("delete_msg", {"message_id": 901}),
            ("set_group_ban", {"group_id": 9988, "user_id": 778899, "duration": 120}),
        ]
        assert post_event(902) == {}
        assert len(calls) == 2
        keyword_event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": 123456,
            "user_id": 778899,
            "group_id": 9988,
            "message_id": 903,
            "raw_message": "明显关键词",
            "sender": {"user_id": 778899, "role": "member"},
        }
        raw = json.dumps(keyword_event, ensure_ascii=False).encode()
        signature = "sha1=" + hmac.new(
            settings.napcat_token.encode(), raw, hashlib.sha1
        ).hexdigest()
        assert client.post(
            "/onebot/v11/events",
            content=raw,
            headers={"x-signature": signature},
        ).json() == {}
        # 命中本地违禁词列表直接禁言并撤回，且不再调用 AI（ai_requests 不新增）。
        assert calls == [
            ("delete_msg", {"message_id": 901}),
            ("set_group_ban", {"group_id": 9988, "user_id": 778899, "duration": 120}),
            ("delete_msg", {"message_id": 903}),
            ("set_group_ban", {"group_id": 9988, "user_id": 778899, "duration": 120}),
        ]
        assert len(ai_requests) == 2
        assert ai_requests[0]["messages"][-1]["content"]
        assert not any(
            item.get("role") == "system"
            for item in ai_requests[0]["messages"]
        )


def test_forbidden_word_ai_learns_keyword_and_reuses_locally(tmp_path: Path) -> None:
    """AI 判定禁言并返回连续违规关键词后应落库，同样内容下次本地直接命中不再调用 AI。"""
    settings = build_settings(tmp_path)
    ai_requests: list[dict] = []

    def ai_handler(request: httpx.Request) -> httpx.Response:
        ai_requests.append(json.loads(request.content))
        return _intent_response(
            mute=True,
            mute_reason="包含赌博引流",
            mute_keyword="赌博网站",
            reason="禁言审核",
        )

    with TestClient(create_app(settings)) as client:
        token = login(client, "admin", "admin-password")
        headers = {"Authorization": f"Bearer {token}"}
        assert client.post(
            "/api/bots",
            headers=headers,
            json={"bot_qq": "123456", "group_mode": "all", "is_enabled": True},
        ).json()["success"]
        assert client.put(
            "/api/bots/123456/ai",
            headers=headers,
            json={
                "is_enabled": True,
                "provider_type": "openai_compatible",
                "base_url": "https://ai.example/v1",
                "api_key": "test-key",
                "model_name": "moderation-model",
                "max_tokens": 100,
            },
        ).json()["success"]
        created = client.post(
            "/api/forbidden_words",
            headers=headers,
            json={
                "bot_qq": "123456",
                "group_id": "9988",
                "keywords": "初始不会命中的词",
                "mute_prompt": "推广赌博等违规内容时需要禁言",
                "mute_duration": 120,
            },
        ).json()
        assert created["success"] is True
        rule_id = created["data"]["id"]

        provider = client.app.state.container.bot_ai_service.provider
        client.portal.call(provider.http_client.aclose)
        provider.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(ai_handler), timeout=2
        )
        calls: list[tuple[str, dict]] = []

        async def fake_call_action(action: str, params: dict, **_kwargs):
            calls.append((action, params))
            return {"status": "ok", "retcode": 0}

        client.app.state.container.onebot_service.call_action = fake_call_action

        def post_event(message_id: int) -> dict:
            event = {
                "post_type": "message",
                "message_type": "group",
                "self_id": 123456,
                "user_id": 778899,
                "group_id": 9988,
                "message_id": message_id,
                "raw_message": "快来这个赌博网站一起玩",
                "sender": {"user_id": 778899, "role": "member"},
            }
            raw = json.dumps(event, ensure_ascii=False).encode()
            signature = "sha1=" + hmac.new(
                settings.napcat_token.encode(), raw, hashlib.sha1
            ).hexdigest()
            return client.post(
                "/onebot/v11/events",
                content=raw,
                headers={"x-signature": signature},
            ).json()

        # 第一次未命中本地词，AI 判定禁言并返回连续违规关键词，随后落库。
        assert post_event(1001) == {}
        assert calls == [
            ("delete_msg", {"message_id": 1001}),
            ("set_group_ban", {"group_id": 9988, "user_id": 778899, "duration": 120}),
        ]
        assert len(ai_requests) == 1
        listed = client.get(
            "/api/forbidden_words",
            headers=headers,
            params={"bot_qq": "123456", "group_id": "9988"},
        ).json()
        assert "赌博网站" in listed["data"]["items"][0]["keyword"]

        # 第二次相同内容命中本地词，直接禁言且不再调用 AI。
        assert post_event(1002) == {}
        assert calls[-2:] == [
            ("delete_msg", {"message_id": 1002}),
            ("set_group_ban", {"group_id": 9988, "user_id": 778899, "duration": 120}),
        ]
        assert len(ai_requests) == 1


def test_forbidden_word_ai_uses_image_model_for_image_messages(tmp_path: Path) -> None:
    """图片消息使用图像模型，并将图片和禁言提示词一并提交审核。"""
    settings = build_settings(tmp_path)
    image_path = tmp_path / "moderation.jpg"
    image_path.write_bytes(b"moderation-image")
    ai_requests: list[dict] = []

    def ai_handler(request: httpx.Request) -> httpx.Response:
        ai_requests.append(json.loads(request.content))
        return _intent_response(
            mute=True,
            mute_reason="图片违规",
            reason="图片禁言审核",
        )

    with TestClient(create_app(settings)) as client:
        token = login(client, "admin", "admin-password")
        headers = {"Authorization": f"Bearer {token}"}
        assert client.post(
            "/api/bots",
            headers=headers,
            json={"bot_qq": "123456", "group_mode": "all", "is_enabled": True},
        ).json()["success"]
        assert client.put(
            "/api/bots/123456/ai",
            headers=headers,
            json={
                "is_enabled": True,
                "provider_type": "openai_compatible",
                "base_url": "https://ai.example/v1",
                "api_key": "test-key",
                "model_name": "text-model",
                "image_model_name": "vision-model",
                "max_tokens": 100,
            },
        ).json()["success"]
        created = client.post(
            "/api/forbidden_words",
            headers=headers,
            json={
                "bot_qq": "123456",
                "group_id": "9988",
                "keywords": "不会命中的关键词",
                "mute_prompt": "图片中出现违规内容时需要禁言",
                "mute_duration": 120,
            },
        ).json()
        assert created["success"] is True
        assert created["data"]["mute_prompt"] == "图片中出现违规内容时需要禁言"
        listed = client.get("/api/forbidden_words", headers=headers).json()
        assert listed["data"]["items"][0]["mute_prompt"] == "图片中出现违规内容时需要禁言"

        provider = client.app.state.container.bot_ai_service.provider
        client.portal.call(provider.http_client.aclose)
        provider.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(ai_handler), timeout=2
        )
        calls: list[tuple[str, dict]] = []

        async def fake_call_action(action: str, params: dict, **_kwargs):
            calls.append((action, params))
            return {"status": "ok", "retcode": 0}

        client.app.state.container.onebot_service.call_action = fake_call_action
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": 123456,
            "user_id": 778899,
            "group_id": 9988,
            "message_id": 904,
            "message": [
                {
                    "type": "image",
                    "data": {"file": image_path.name, "path": str(image_path)},
                },
            ],
            "sender": {"user_id": 778899, "role": "member"},
        }
        raw = json.dumps(event, ensure_ascii=False).encode()
        signature = "sha1=" + hmac.new(
            settings.napcat_token.encode(), raw, hashlib.sha1
        ).hexdigest()
        assert client.post(
            "/onebot/v11/events",
            content=raw,
            headers={"x-signature": signature},
        ).json() == {}

    assert calls == [
        ("delete_msg", {"message_id": 904}),
        ("set_group_ban", {"group_id": 9988, "user_id": 778899, "duration": 120}),
    ]
    assert ai_requests[0]["model"] == "vision-model"
    content = ai_requests[0]["messages"][-1]["content"]
    assert any(item.get("type") == "image_url" for item in content)
    assert any("图片中出现违规内容" in item.get("text", "") for item in content)
