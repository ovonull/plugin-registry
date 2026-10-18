"""聊天记录和 AI 回复记录查询、导出与删除接口测试。"""

import base64
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.application import create_app
from app.common.time_utils import beijing_now
from app.models.entities import AiProactiveReplyMessage, ChatMessage, User
from test_service import build_settings, login, register_user


def _owner_ids(client: TestClient) -> tuple[int, int]:
    """读取两名测试用户的主键。"""

    async def owners() -> tuple[int, int]:
        async with client.app.state.container.database.session_factory() as session:
            rows = await session.scalars(
                select(User).where(User.username.in_(["alice", "bob"]))
            )
            users = {item.username: item.id for item in rows}
            return users["alice"], users["bob"]

    return client.portal.call(owners)


def _seed_records(client: TestClient, owner_ids: tuple[int, int]) -> None:
    """写入两名用户的聊天和 AI 处理记录。"""
    first_owner, second_owner = owner_ids
    now = beijing_now()

    async def seed() -> None:
        async with client.app.state.container.database.session_factory() as session:
            session.add_all(
                [
                    ChatMessage(
                        owner_id=first_owner, bot_qq="10001", group_id="20001",
                        message_id="chat-old", user_id="30001", sender_user_id="30001",
                        sender_nickname="甲", message_text="较早消息", post_type="message",
                        event_time=now - timedelta(minutes=2),
                    ),
                    ChatMessage(
                        owner_id=first_owner, bot_qq="10001", group_id="20001",
                        message_id="chat-new", user_id="30002", sender_user_id="30002",
                        sender_nickname="乙", message_text="较新消息", post_type="message",
                        event_time=now,
                    ),
                    ChatMessage(
                        owner_id=second_owner, bot_qq="10002", group_id="20002",
                        message_id="chat-other", user_id="30003", sender_user_id="30003",
                        sender_nickname="丙", message_text="其他用户消息", post_type="message",
                        event_time=now + timedelta(minutes=1),
                    ),
                    AiProactiveReplyMessage(
                        owner_id=first_owner, rule_id=11, bot_qq="10001", group_id="20001",
                        message_id="chat-old", message_key="10001:20001:chat-old",
                        created_at=now - timedelta(minutes=1),
                    ),
                    AiProactiveReplyMessage(
                        owner_id=second_owner, rule_id=12, bot_qq="10002", group_id="20002",
                        message_id="chat-other", message_key="10002:20002:chat-other",
                        created_at=now,
                    ),
                ]
            )
            await session.commit()

    client.portal.call(seed)


def test_record_lists_are_scoped_paginated_and_ordered(tmp_path: Path) -> None:
    """管理员查看全部记录，普通用户只能查看自己的记录，且结果按时间倒序。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        first = register_user(client, "alice", "alice-pass1", "alice@example.com", "甲")
        second = register_user(client, "bob", "bob-pass1", "bob@example.com", "乙")

        async def owners() -> tuple[int, int]:
            async with client.app.state.container.database.session_factory() as session:
                rows = await session.scalars(
                    select(User).where(User.username.in_(["alice", "bob"]))
                )
                users = {item.username: item.id for item in rows}
                return users["alice"], users["bob"]

        owner_ids = client.portal.call(owners)
        _seed_records(client, owner_ids)

        admin_headers = {"Authorization": f"Bearer {login(client, 'admin', 'admin-password')}"}
        alice_headers = {"Authorization": f"Bearer {first['data']['token']}"}

        admin_messages = client.get(
            "/api/chat_records/messages?page=1&page_size=20", headers=admin_headers
        ).json()
        assert admin_messages["success"] is True
        assert admin_messages["data"]["total"] == 3
        assert [item["message_id"] for item in admin_messages["data"]["items"]] == [
            "chat-other", "chat-new", "chat-old"
        ]

        alice_messages = client.get(
            "/api/chat_records/messages?page=1&page_size=20", headers=alice_headers
        ).json()
        assert alice_messages["data"]["total"] == 2
        assert {item["message_id"] for item in alice_messages["data"]["items"]} == {
            "chat-old", "chat-new"
        }

        admin_ai = client.get(
            "/api/chat_records/ai_replies?page=1&page_size=20", headers=admin_headers
        ).json()
        assert admin_ai["data"]["total"] == 2
        assert [item["message_id"] for item in admin_ai["data"]["items"]] == [
            "chat-other", "chat-old"
        ]
        assert admin_ai["data"]["items"][1]["source_message"] == "较早消息"

        alice_ai = client.get(
            "/api/chat_records/ai_replies?page=1&page_size=20", headers=alice_headers
        ).json()
        assert alice_ai["data"]["total"] == 1
        assert alice_ai["data"]["items"][0]["source_sender"] == "甲"


def _decode_csv(payload: dict) -> str:
    """把导出接口返回的 Base64 内容还原为 CSV 文本。"""
    return base64.b64decode(payload["data"]["content"]).decode("utf-8")


def test_chat_message_export_follows_filters_and_owner_scope(tmp_path: Path) -> None:
    """导出范围与列表查询一致，普通用户只能导出自己的记录。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        first = register_user(client, "alice", "alice-pass1", "alice@example.com", "甲")
        register_user(client, "bob", "bob-pass1", "bob@example.com", "乙")
        _seed_records(client, _owner_ids(client))
        admin_headers = {"Authorization": f"Bearer {login(client, 'admin', 'admin-password')}"}
        alice_headers = {"Authorization": f"Bearer {first['data']['token']}"}

        exported = client.get(
            "/api/chat_records/messages/export", headers=admin_headers
        ).json()
        assert exported["success"] is True
        assert exported["data"]["total"] == 3
        assert exported["data"]["file_name"].endswith(".csv")
        assert exported["data"]["media_type"] == "text/csv"
        content = _decode_csv(exported)
        # 带 UTF-8 BOM，Excel 打开中文不乱码。
        assert content.startswith("﻿事件时间,方向,机器人QQ")
        assert len([line for line in content.splitlines() if line.strip()]) == 4
        assert "较新消息" in content and "其他用户消息" in content
        assert "已处理" in content or "未处理" in content

        alice_exported = client.get(
            "/api/chat_records/messages/export", headers=alice_headers
        ).json()
        assert alice_exported["data"]["total"] == 2
        assert "其他用户消息" not in _decode_csv(alice_exported)

        filtered = client.get(
            "/api/chat_records/messages/export?keyword=较早", headers=admin_headers
        ).json()
        assert filtered["data"]["total"] == 1
        assert "较早消息" in _decode_csv(filtered)

        empty = client.get(
            "/api/chat_records/messages/export?bot_qq=99999", headers=admin_headers
        ).json()
        assert empty["success"] is False
        assert empty["message"] == "当前筛选条件下没有可导出的聊天记录"


def test_chat_message_export_escapes_formula_prefix(tmp_path: Path) -> None:
    """以等号等符号开头的消息导出时必须转成文本，避免 Excel 当公式执行。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        headers = {"Authorization": f"Bearer {login(client, 'admin', 'admin-password')}"}

        async def seed() -> None:
            async with client.app.state.container.database.session_factory() as session:
                session.add(
                    ChatMessage(
                        bot_qq="10001", group_id="20001", message_id="risky",
                        user_id="30001", sender_user_id="30001", sender_nickname="甲",
                        message_text="=1+1", post_type="message", event_time=beijing_now(),
                    )
                )
                await session.commit()

        client.portal.call(seed)
        content = _decode_csv(
            client.get("/api/chat_records/messages/export", headers=headers).json()
        )
        assert "'=1+1" in content


def test_chat_message_batch_delete_is_scoped_and_soft(tmp_path: Path) -> None:
    """勾选删除只能删自己范围内的记录，且只做软删除。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        first = register_user(client, "alice", "alice-pass1", "alice@example.com", "甲")
        register_user(client, "bob", "bob-pass1", "bob@example.com", "乙")
        _seed_records(client, _owner_ids(client))
        admin_headers = {"Authorization": f"Bearer {login(client, 'admin', 'admin-password')}"}
        alice_headers = {"Authorization": f"Bearer {first['data']['token']}"}

        alice_items = client.get(
            "/api/chat_records/messages?page=1&page_size=20", headers=alice_headers
        ).json()["data"]["items"]
        admin_items = client.get(
            "/api/chat_records/messages?page=1&page_size=20", headers=admin_headers
        ).json()["data"]["items"]
        others_id = next(
            item["id"] for item in admin_items if item["message_id"] == "chat-other"
        )
        own_id = next(
            item["id"] for item in alice_items if item["message_id"] == "chat-old"
        )

        denied = client.post(
            "/api/chat_records/messages/batch-delete",
            headers=alice_headers,
            json={"ids": [own_id, others_id]},
        ).json()
        assert denied["success"] is False
        assert denied["message"] == "部分聊天记录不存在或无权访问"
        assert client.get(
            "/api/chat_records/messages?page=1&page_size=20", headers=alice_headers
        ).json()["data"]["total"] == 2

        deleted = client.post(
            "/api/chat_records/messages/batch-delete",
            headers=alice_headers,
            json={"ids": [own_id]},
        ).json()
        assert deleted["success"] is True
        assert deleted["data"]["deleted_count"] == 1
        assert client.get(
            "/api/chat_records/messages?page=1&page_size=20", headers=alice_headers
        ).json()["data"]["total"] == 1
        assert client.get(
            "/api/chat_records/messages?page=1&page_size=20", headers=admin_headers
        ).json()["data"]["total"] == 2
        assert "较早消息" not in _decode_csv(
            client.get("/api/chat_records/messages/export", headers=admin_headers).json()
        )

        empty = client.post(
            "/api/chat_records/messages/batch-delete",
            headers=alice_headers,
            json={"ids": []},
        ).json()
        assert empty["success"] is False

        async def check_soft_delete() -> bool:
            async with client.app.state.container.database.session_factory() as session:
                record = await session.get(ChatMessage, own_id)
                return record is not None and record.is_deleted

        # 数据必须仍在库中，只是被标记为已删除。
        assert client.portal.call(check_soft_delete) is True
