"""动画表情入库、去重和分类任务初始化测试。"""

import base64
import hashlib
import hmac
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import undefer

from app.application import create_app
from app.common.time_utils import beijing_now
from app.models.entities import AnimatedEmoji, ChatMessage, User
from app.services.ai_image_service import AiImageService
from app.services.scheduled_task_service import (
    ANIMATED_EMOJI_CLASSIFY_TASK_KEY,
    ScheduledTaskService,
)
from test_service import build_settings, login, register_user


def test_animated_emoji_event_is_recorded_once(tmp_path: Path) -> None:
    """动画表情入站事件会记录，普通图片不会记录，同一来源不会重复。"""
    settings = build_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        token = login(client, "admin", "admin-password")
        headers = {"Authorization": f"Bearer {token}"}
        client.post(
            "/api/bots",
            headers=headers,
            json={"bot_qq": "123456", "group_mode": "all", "is_enabled": True},
        )
        image_path = tmp_path / "emoji.jpg"
        image_path.write_bytes(b"received-emoji")
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": 123456,
            "user_id": 778899,
            "group_id": 9988,
            "group_name": "测试群",
            "message_id": 1001,
            "time": 1786000000,
            "sender": {"user_id": 778899, "nickname": "测试用户"},
            "message": [
                {
                    "type": "image",
                    "data": {
                        "summary": "[动画表情]",
                        "file": "emoji.jpg",
                        "path": str(image_path),
                        "url": "https://example.com/emoji.jpg",
                    },
                }
            ],
        }
        raw = json.dumps(event, ensure_ascii=False).encode()
        signature = "sha1=" + hmac.new(
            settings.napcat_token.encode(), raw, hashlib.sha1
        ).hexdigest()
        for _ in range(2):
            response = client.post(
                "/onebot/v11/events",
                content=raw,
                headers={"x-signature": signature},
            )
            assert response.json() == {}

        normal = {**event, "message_id": 1002, "message": [{"type": "image", "data": {"summary": "", "file": "normal.jpg", "url": "https://example.com/normal.jpg"}}]}
        normal_raw = json.dumps(normal, ensure_ascii=False).encode()
        normal_signature = "sha1=" + hmac.new(
            settings.napcat_token.encode(), normal_raw, hashlib.sha1
        ).hexdigest()
        client.post(
            "/onebot/v11/events",
            content=normal_raw,
            headers={"x-signature": normal_signature},
        )
        listed = client.get("/api/animated_emojis", headers=headers).json()
        assert listed["success"] is True
        assert listed["data"]["total"] == 1
        assert listed["data"]["items"][0]["category"] == "未分类"
        assert listed["data"]["items"][0]["bot_qq"] == "123456"
        assert listed["data"]["items"][0]["source_url"] == "https://example.com/emoji.jpg"
        assert "preview_url" not in listed["data"]["items"][0]
        record_id = listed["data"]["items"][0]["id"]
        preview = client.get(
            f"/api/animated_emojis/{record_id}/preview", headers=headers
        ).json()
        assert preview["success"] is True
        assert preview["data"]["media_type"] == "image/jpeg"
        assert preview["data"]["data"] == base64.b64encode(
            b"received-emoji"
        ).decode("ascii")

        async def load_rows() -> list[AnimatedEmoji]:
            async with client.app.state.container.database.session_factory() as session:
                return list(
                    (
                        await session.scalars(
                            select(AnimatedEmoji).options(
                                undefer(AnimatedEmoji.image_base64)
                            )
                        )
                    ).all()
                )

        rows = client.portal.call(load_rows)
        assert len(rows) == 1
        assert rows[0].image_base64 == base64.b64encode(b"received-emoji").decode("ascii")
        cached_images = client.portal.call(
            client.app.state.container.animated_emoji_service.get_event_images,
            event,
            "123456",
        )
        assert cached_images == [
            {
                "media_type": "image/jpeg",
                "data": base64.b64encode(b"received-emoji").decode("ascii"),
            }
        ]

        async def add_legacy_record() -> None:
            async with client.app.state.container.database.session_factory() as session:
                session.add(
                    AnimatedEmoji(
                        owner_id=1,
                        bot_qq="123456",
                        message_id="1003",
                        file_name="legacy.jpg",
                        source_key="1003:name:legacy.jpg",
                        source_url="",
                        category="搞笑",
                        received_at=beijing_now(),
                    )
                )
                session.add(
                    ChatMessage(
                        owner_id=1,
                        bot_qq="123456",
                        message_id="1003",
                        event_data=json.dumps(
                            {
                                "post_type": "message",
                                "message": [
                                    {
                                        "type": "image",
                                        "data": {
                                            "summary": "[动画表情]",
                                            "file": "legacy.jpg",
                                            "url": "https://example.com/legacy.jpg",
                                        },
                                    }
                                ],
                            },
                            ensure_ascii=False,
                        ),
                    )
                )
                await session.commit()

        client.portal.call(add_legacy_record)
        recovered = client.get("/api/animated_emojis", headers=headers).json()
        legacy = next(item for item in recovered["data"]["items"] if item["message_id"] == "1003")
        assert legacy["source_url"] == "https://example.com/legacy.jpg"
        assert recovered["data"]["items"][0]["message_id"] == "1003"


def test_animated_emoji_classification_task_is_initialized(tmp_path: Path) -> None:
    """启动自检会幂等创建动画表情分类任务。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        token = login(client, "admin", "admin-password")
        task_rows = client.get(
            "/api/scheduled_tasks", headers={"Authorization": f"Bearer {token}"}
        ).json()["data"]["items"]
        assert any(item["task_key"] == ANIMATED_EMOJI_CLASSIFY_TASK_KEY for item in task_rows)


def test_animated_emoji_cq_text_is_detected() -> None:
    """字符串消息中的 CQ 图片码也能识别为动画表情。"""
    event = {
        "message_format": "string",
        "message": (
            "[CQ:image,summary=&#91;动画表情&#93;,file=emoji.jpg,"
            "url=https://example.com/emoji.jpg]"
        ),
    }
    candidates = AiImageService.animated_candidates(event)
    assert len(candidates) == 1
    assert candidates[0]["name"] == "emoji.jpg"
    assert candidates[0]["summary"] == "[动画表情]"


def test_animated_emoji_classification_writes_ai_category(tmp_path: Path) -> None:
    """手动执行分类任务会读取图片并把 AI 分类写回记录。"""
    settings = build_settings(tmp_path)
    image_path = tmp_path / "emoji.jpg"
    image_path.write_bytes(b"local-emoji")

    ai_calls = 0
    ai_models: list[str] = []
    ai_messages: list[list[dict[str, object]]] = []

    def ai_handler(_request: httpx.Request) -> httpx.Response:
        nonlocal ai_calls
        ai_calls += 1
        payload = json.loads(_request.content)
        ai_models.append(payload["model"])
        ai_messages.append(payload["messages"])
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"success":true,"category":"搞笑"}'
                        }
                    }
                ]
            },
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
                "model_name": "vision-model",
                "image_model_name": "emoji-vision-model",
                "system_prompt": "这是聊天机器人的角色提示词，不应参与图片分类",
                "max_tokens": 100,
            },
        ).json()["success"]

        async def add_record() -> None:
            async with client.app.state.container.database.session_factory() as session:
                session.add(
                    AnimatedEmoji(
                        bot_qq="123456",
                        file_name=image_path.name,
                        source_path=str(image_path),
                        source_key="test:emoji",
                        category="未分类",
                        received_at=beijing_now(),
                    )
                )
                session.add(
                    AnimatedEmoji(
                        bot_qq="123456",
                        file_name="path-missing.jpg",
                        source_url="",
                        source_path="C:/napcat/missing/path-missing.jpg",
                        source_key="test:path-missing",
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
        response = client.post(f"/api/scheduled_tasks/{task['id']}/run", headers=headers).json()
        assert response["success"] is True
        assert response["data"]["last_status"] == "success"
        task_result = json.loads(response["data"]["last_result"])
        assert task_result["pending"] == 2
        assert ai_calls == 1
        assert ai_models == ["emoji-vision-model"]
        assert task_result["deleted"] == 1
        assert all(
            message.get("role") != "system"
            for message in ai_messages[0]
        )
        prompt_content = ai_messages[0][-1]["content"]
        prompt_text = prompt_content[0]["text"]
        assert "必须根据实际图片内容归入一个中文分类" in prompt_text
        assert "success=false" in prompt_text
        assert "确实无法归入具体类型时使用“其他”" in prompt_text
        listed = client.get("/api/animated_emojis", headers=headers).json()
        categories = {
            item["file_name"]: item["category"] for item in listed["data"]["items"]
        }
        assert categories[image_path.name] == "搞笑"
        assert "path-missing.jpg" not in categories


def test_animated_emoji_category_requires_explicit_success_json() -> None:
    """分类解析拒绝看不到图片等闲聊内容，只接受 success=true 的 JSON。"""
    assert ScheduledTaskService._normalise_category(
        '{"success":true,"category":"搞笑"}'
    ) == "搞笑"
    assert ScheduledTaskService._normalise_category(
        '{"success":false,"category":"","reason":"看不到图片"}'
    ) == ""
    assert ScheduledTaskService._normalise_category(
        '{"success":true,"category":"我看不到图片"}'
    ) == ""
    assert ScheduledTaskService._normalise_category("哎呀，我看不到图呀") == ""


def test_animated_emoji_batch_delete_is_soft_and_scoped(tmp_path: Path) -> None:
    """动画表情批量删除只标记软删除，普通用户不能操作他人记录。"""
    settings = build_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        admin_token = login(client, "admin", "admin-password")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        registered = register_user(
            client,
            "emoji_user",
            "user-password1",
            "emoji-user@example.com",
            "动画用户",
        )
        assert registered["success"] is True
        user_token = login(client, "emoji_user", "user-password1")
        user_headers = {"Authorization": f"Bearer {user_token}"}

        async def get_user_id() -> int:
            async with client.app.state.container.database.session_factory() as session:
                return int(await session.scalar(select(User.id).where(User.username == "emoji_user")))

        user_id = client.portal.call(get_user_id)

        async def add_records() -> list[int]:
            async with client.app.state.container.database.session_factory() as session:
                rows = [
                    AnimatedEmoji(
                        owner_id=1,
                        bot_qq="123456",
                        file_name="admin-1.jpg",
                        source_key="delete:admin-1",
                        category="未分类",
                        received_at=beijing_now(),
                    ),
                    AnimatedEmoji(
                        owner_id=1,
                        bot_qq="123456",
                        file_name="admin-2.jpg",
                        source_key="delete:admin-2",
                        category="搞笑",
                        received_at=beijing_now(),
                    ),
                    AnimatedEmoji(
                        owner_id=user_id,
                        bot_qq="123456",
                        file_name="user-1.jpg",
                        source_key="delete:user-1",
                        category="未分类",
                        received_at=beijing_now(),
                    ),
                ]
                session.add_all(rows)
                await session.commit()
                return [row.id for row in rows]

        ids = client.portal.call(add_records)
        denied = client.post(
            "/api/animated_emojis/batch-delete",
            headers=user_headers,
            json={"ids": [ids[0]]},
        ).json()
        assert denied["success"] is False
        assert denied["code"] == 40460

        deleted = client.post(
            "/api/animated_emojis/batch-delete",
            headers=admin_headers,
            json={"ids": ids[:2]},
        ).json()
        assert deleted["success"] is True
        assert deleted["data"]["deleted_count"] == 2

        listed = client.get("/api/animated_emojis", headers=admin_headers).json()
        assert {item["id"] for item in listed["data"]["items"]} == {ids[2]}

        async def read_rows() -> list[AnimatedEmoji]:
            async with client.app.state.container.database.session_factory() as session:
                return list((await session.scalars(select(AnimatedEmoji))).all())

        rows = client.portal.call(read_rows)
        by_id = {row.id: row for row in rows}
        assert all(by_id[item_id].is_deleted is True for item_id in ids[:2])
        assert by_id[ids[2]].is_deleted is False


def test_animated_emoji_batch_category_is_scoped_and_updates_state(tmp_path: Path) -> None:
    """动画表情批量分类校验数据范围，并同步分类完成状态。"""
    settings = build_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        admin_token = login(client, "admin", "admin-password")
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        registered = register_user(
            client,
            "category_user",
            "user-password1",
            "category-user@example.com",
            "分类用户",
        )
        assert registered["success"] is True
        user_token = login(client, "category_user", "user-password1")
        user_headers = {"Authorization": f"Bearer {user_token}"}

        async def get_user_id() -> int:
            async with client.app.state.container.database.session_factory() as session:
                return int(await session.scalar(select(User.id).where(User.username == "category_user")))

        user_id = client.portal.call(get_user_id)

        async def add_records() -> list[int]:
            async with client.app.state.container.database.session_factory() as session:
                rows = [
                    AnimatedEmoji(
                        owner_id=1,
                        bot_qq="123456",
                        file_name="category-admin-1.jpg",
                        source_key="category:admin-1",
                        category="未分类",
                        classification_error="上次分类失败",
                        received_at=beijing_now(),
                    ),
                    AnimatedEmoji(
                        owner_id=1,
                        bot_qq="123456",
                        file_name="category-admin-2.jpg",
                        source_key="category:admin-2",
                        category="旧分类",
                        received_at=beijing_now(),
                    ),
                    AnimatedEmoji(
                        owner_id=user_id,
                        bot_qq="123456",
                        file_name="category-user-1.jpg",
                        source_key="category:user-1",
                        category="未分类",
                        received_at=beijing_now(),
                    ),
                ]
                session.add_all(rows)
                await session.commit()
                return [row.id for row in rows]

        ids = client.portal.call(add_records)
        denied = client.post(
            "/api/animated_emojis/batch-category",
            headers=user_headers,
            json={"ids": [ids[0]], "category": "搞笑"},
        ).json()
        assert denied["success"] is False
        assert denied["code"] == 40460

        updated = client.post(
            "/api/animated_emojis/batch-category",
            headers=admin_headers,
            json={"ids": ids[:2], "category": "  搞笑  "},
        ).json()
        assert updated["success"] is True
        assert updated["data"] == {"updated_count": 2, "category": "搞笑"}

        async def read_rows() -> list[AnimatedEmoji]:
            async with client.app.state.container.database.session_factory() as session:
                return list((await session.scalars(select(AnimatedEmoji))).all())

        rows = client.portal.call(read_rows)
        by_id = {row.id: row for row in rows}
        assert by_id[ids[0]].category == "搞笑"
        assert by_id[ids[1]].category == "搞笑"
        assert by_id[ids[0]].classification_error == ""
        assert by_id[ids[0]].classified_at is not None
        assert by_id[ids[2]].category == "未分类"

        reset = client.post(
            "/api/animated_emojis/batch-category",
            headers=admin_headers,
            json={"ids": [ids[0]], "category": "未分类"},
        ).json()
        assert reset["success"] is True
        rows = client.portal.call(read_rows)
        reset_row = next(row for row in rows if row.id == ids[0])
        assert reset_row.category == "未分类"
        assert reset_row.classified_at is None
