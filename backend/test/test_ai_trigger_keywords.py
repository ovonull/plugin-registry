"""AI 触发关键词规则 CRUD、CSV 去重和消息命中测试。"""

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.application import create_app
from test_service import build_settings, login


def test_ai_trigger_keyword_rule_crud_and_match(tmp_path: Path) -> None:
    """同一机器人只保存一条关键词列表，命中后可被消息处理服务读取。"""
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
            "/api/ai_trigger_keywords",
            headers=headers,
            json={"bot_qq": "123456", "keywords": "price,plan,price", "reply_all": True},
        ).json()
        assert created["success"] is True
        assert created["data"]["keyword"] == "price,plan"
        assert created["data"]["reply_all"] is True
        duplicate = client.post(
            "/api/ai_trigger_keywords",
            headers=headers,
            json={"bot_qq": "123456", "keywords": "other"},
        ).json()
        assert duplicate["success"] is False

        service = client.app.state.container.ai_trigger_keyword_service

        async def match() -> str | None:
            async with client.app.state.container.database.session_factory() as session:
                return await service.match_event(
                    session,
                    {
                        "post_type": "message",
                        "message_type": "group",
                        "self_id": "123456",
                        "message": [
                            {"type": "text", "data": {"text": "what is the plan"}}
                        ],
                    },
                )

        assert asyncio.run(match()) == "plan"

        async def reply_all_enabled() -> bool:
            async with client.app.state.container.database.session_factory() as session:
                return await service.is_reply_all_enabled(
                    session,
                    {
                        "post_type": "message",
                        "message_type": "group",
                        "self_id": "123456",
                    },
                )

        assert asyncio.run(reply_all_enabled()) is True
        random_updated = client.put(
            f"/api/ai_trigger_keywords/{created['data']['id']}",
            headers=headers,
            json={
                "bot_qq": "123456",
                "keyword": "price,plan",
                "random_reply": True,
                "random_reply_probability": 35,
            },
        ).json()
        assert random_updated["success"] is True
        assert random_updated["data"]["reply_all"] is False
        assert random_updated["data"]["random_reply"] is True
        assert random_updated["data"]["random_reply_probability"] == 35

        async def reply_mode() -> dict:
            async with client.app.state.container.database.session_factory() as session:
                return await service.get_reply_mode(
                    session,
                    {
                        "post_type": "message",
                        "message_type": "group",
                        "self_id": "123456",
                    },
                )

        assert asyncio.run(reply_mode()) == {
            "reply_all": False,
            "random_reply": True,
            "random_reply_probability": 35,
        }
        conflict = client.put(
            f"/api/ai_trigger_keywords/{created['data']['id']}",
            headers=headers,
            json={
                "bot_qq": "123456",
                "keyword": "other",
                "reply_all": True,
                "random_reply": True,
            },
        ).json()
        assert conflict["success"] is False
        deleted = client.post(
            "/api/ai_trigger_keywords/batch-delete",
            headers=headers,
            json={"ids": [created["data"]["id"]]},
        ).json()
        assert deleted["success"] is True
        assert deleted["data"]["deleted_count"] == 1
