"""动画表情分类任务的 Base64 复用和失效图片软删除测试。"""

import base64
import json

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.application import create_app
from app.common.time_utils import beijing_now
from app.models.entities import AnimatedEmoji
from app.services.scheduled_task_service import ANIMATED_EMOJI_CLASSIFY_TASK_KEY
from test_bot_ai import BOT_QQ, _ai_payload, _create_bot, _headers
from test_service import build_settings, login, register_user


def test_classification_reuses_base64_and_soft_deletes_unreadable_image(tmp_path) -> None:
    """缓存图片直接分类，公网图片无法读取时只做软删除。"""
    settings = build_settings(tmp_path)
    ai_payloads: list[dict] = []
    image_requests: list[str] = []

    def ai_handler(request: httpx.Request) -> httpx.Response:
        ai_payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"success":true,"category":"搞笑"}'}},
                ]
            },
        )

    def image_handler(request: httpx.Request) -> httpx.Response:
        image_requests.append(str(request.url))
        return httpx.Response(400, text="expired")

    with TestClient(create_app(settings)) as client:
        headers = _headers(client)
        _create_bot(client, headers)
        assert client.put(
            f"/api/bots/{BOT_QQ}/ai",
            headers=headers,
            json=_ai_payload(image_model_name="emoji-vision-model"),
        ).json()["success"]

        async def add_records() -> None:
            async with client.app.state.container.database.session_factory() as session:
                session.add_all(
                    [
                        AnimatedEmoji(
                            bot_qq=BOT_QQ,
                            file_name="unreadable.jpg",
                            source_url="https://multimedia.example/unreadable.jpg",
                            source_key="cleanup:unreadable",
                            category="未分类",
                            received_at=beijing_now(),
                        ),
                        AnimatedEmoji(
                            bot_qq=BOT_QQ,
                            file_name="cached.png",
                            media_type="image/png",
                            image_base64=base64.b64encode(b"cached-image").decode("ascii"),
                            source_key="cleanup:cached",
                            category="未分类",
                            received_at=beijing_now(),
                        ),
                    ]
                )
                await session.commit()

        client.portal.call(add_records)
        onebot = client.app.state.container.onebot_service
        client.portal.call(onebot.http_client.aclose)
        onebot.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(image_handler), timeout=2
        )
        provider = client.app.state.container.bot_ai_service.provider
        client.portal.call(provider.http_client.aclose)
        provider.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(ai_handler), timeout=2
        )

        tasks = client.get("/api/scheduled_tasks", headers=headers).json()["data"]["items"]
        task = next(item for item in tasks if item["task_key"] == ANIMATED_EMOJI_CLASSIFY_TASK_KEY)
        result = client.post(
            f"/api/scheduled_tasks/{task['id']}/run", headers=headers
        ).json()
        assert result["success"] is True
        task_result = json.loads(result["data"]["last_result"])
        assert task_result["pending"] == 2
        assert task_result["classified"] == 1
        assert task_result["deleted"] == 1
        assert task_result["failed"] == 0
        assert image_requests == ["https://multimedia.example/unreadable.jpg"]
        assert ai_payloads[0]["messages"][-1]["content"][1]["image_url"]["url"].endswith(
            base64.b64encode(b"cached-image").decode("ascii")
        )

        async def read_rows() -> list[AnimatedEmoji]:
            async with client.app.state.container.database.session_factory() as session:
                return list(
                    (
                        await session.scalars(
                            select(AnimatedEmoji).order_by(AnimatedEmoji.id.asc())
                        )
                    ).all()
                )

        rows = client.portal.call(read_rows)
        assert rows[0].is_deleted is True
        assert "已自动软删除" in rows[0].classification_error
        assert rows[1].is_deleted is False


def test_animated_emoji_prompt_settings_are_saved_and_used(tmp_path) -> None:
    """管理员可修改分类提示词，分类任务会使用最新配置。"""
    settings = build_settings(tmp_path)
    custom_prompt = '请按图片内容分类，只返回 JSON：{"success":true,"category":"测试分类"}'
    ai_payloads: list[dict] = []

    def ai_handler(request: httpx.Request) -> httpx.Response:
        ai_payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"success":true,"category":"搞笑"}'}},
                ]
            },
        )

    with TestClient(create_app(settings)) as client:
        admin_headers = _headers(client)
        default_response = client.get(
            "/api/settings/animated_emoji", headers=admin_headers
        ).json()
        assert default_response["success"] is True
        assert "必须根据实际图片内容归入一个中文分类" in default_response["data"]["classification_prompt"]

        saved_response = client.put(
            "/api/settings/animated-emoji/save",
            headers=admin_headers,
            json={"classification_prompt": custom_prompt},
        ).json()
        assert saved_response["success"] is True
        assert saved_response["data"]["classification_prompt"] == custom_prompt

        register_user(client, "emoji_prompt_user", "user-password1", "prompt@example.com", "提示词用户")
        user_headers = {
            "Authorization": f"Bearer {login(client, 'emoji_prompt_user', 'user-password1')}"
        }
        denied_response = client.put(
            "/api/settings/animated-emoji/save",
            headers=user_headers,
            json={"classification_prompt": "普通用户不应修改"},
        ).json()
        assert denied_response["success"] is False
        assert denied_response["code"] == 40310

        _create_bot(client, admin_headers)
        assert client.put(
            f"/api/bots/{BOT_QQ}/ai",
            headers=admin_headers,
            json=_ai_payload(image_model_name="emoji-vision-model"),
        ).json()["success"]

        async def add_record() -> None:
            async with client.app.state.container.database.session_factory() as session:
                session.add(
                    AnimatedEmoji(
                        bot_qq=BOT_QQ,
                        file_name="cached.png",
                        media_type="image/png",
                        image_base64=base64.b64encode(b"cached-image").decode("ascii"),
                        source_key="prompt-settings:cached",
                        category="未分类",
                        received_at=beijing_now(),
                    )
                )
                await session.commit()

        client.portal.call(add_record)
        provider = client.app.state.container.bot_ai_service.provider
        client.portal.call(provider.http_client.aclose)
        provider.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(ai_handler), timeout=2
        )

        tasks = client.get("/api/scheduled_tasks", headers=admin_headers).json()["data"]["items"]
        task = next(item for item in tasks if item["task_key"] == ANIMATED_EMOJI_CLASSIFY_TASK_KEY)
        run_response = client.post(
            f"/api/scheduled_tasks/{task['id']}/run", headers=admin_headers
        ).json()
        assert run_response["success"] is True
        assert ai_payloads
        prompt_content = ai_payloads[0]["messages"][-1]["content"][0]["text"]
        assert prompt_content == custom_prompt


def test_advertising_category_is_soft_deleted(tmp_path) -> None:
    """AI 返回广告分类时软删除记录，不再展示或重复分类。"""
    settings = build_settings(tmp_path)

    def ai_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"success":true,"category":"广告"}'}},
                ]
            },
        )

    with TestClient(create_app(settings)) as client:
        headers = _headers(client)
        _create_bot(client, headers)
        assert client.put(
            f"/api/bots/{BOT_QQ}/ai",
            headers=headers,
            json=_ai_payload(image_model_name="emoji-vision-model"),
        ).json()["success"]

        async def add_record() -> None:
            async with client.app.state.container.database.session_factory() as session:
                session.add(
                    AnimatedEmoji(
                        bot_qq=BOT_QQ,
                        file_name="advertisement.png",
                        media_type="image/png",
                        image_base64=base64.b64encode(b"advertisement-image").decode("ascii"),
                        source_key="advertisement:cached",
                        category="未分类",
                        received_at=beijing_now(),
                    )
                )
                await session.commit()

        client.portal.call(add_record)
        provider = client.app.state.container.bot_ai_service.provider
        client.portal.call(provider.http_client.aclose)
        provider.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(ai_handler), timeout=2
        )

        tasks = client.get("/api/scheduled_tasks", headers=headers).json()["data"]["items"]
        task = next(item for item in tasks if item["task_key"] == ANIMATED_EMOJI_CLASSIFY_TASK_KEY)
        response = client.post(
            f"/api/scheduled_tasks/{task['id']}/run", headers=headers
        ).json()
        assert response["success"] is True
        task_result = json.loads(response["data"]["last_result"])
        assert task_result["classified"] == 0
        assert task_result["failed"] == 0
        assert task_result["deleted"] == 1

        async def read_record() -> AnimatedEmoji:
            async with client.app.state.container.database.session_factory() as session:
                return await session.scalar(select(AnimatedEmoji))

        row = client.portal.call(read_record)
        assert row.is_deleted is True
        assert "AI 判定为广告" in row.classification_error
