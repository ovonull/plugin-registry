"""好友列表同步与查询接口测试。"""

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.application import create_app
from app.common.time_utils import beijing_now
from app.models.entities import ChatMessage
from test_service import build_settings, login, register_user


FIRST_BATCH = [
    {
        "user_id": 10001,
        "nickname": "老王",
        "remark": "同事老王",
        "sex": "male",
        "age": 30,
        "level": 16,
        "qid": "wang",
        "longNick": "今天也要加油",
        "categoryId": 1,
        "birthday_year": 1995,
        "birthday_month": 3,
        "birthday_day": 8,
    },
    {
        "user_id": 10002,
        "nick": "小美",
        "remark": "",
        "sex": "female",
        "level": 8,
        "categoryId": 2,
    },
    # 缺少 user_id 的脏数据必须被忽略。
    {"nickname": "无效好友"},
]
SECOND_BATCH = [
    {
        "user_id": 10001,
        "nickname": "老王改名",
        "remark": "同事老王",
        "sex": "male",
        "categoryId": 2,
    },
    {"user_id": 10003, "nickname": "新朋友", "sex": "unknown", "categoryId": 1},
]
CATEGORIES = [
    {"categoryId": 1, "categoryName": "我的好友"},
    {"categoryId": 2, "categoryName": "同事"},
]


def _headers(token: str) -> dict[str, str]:
    """构造 Bearer 请求头。"""
    return {"Authorization": f"Bearer {token}"}


def _create_bot(client: TestClient, headers: dict[str, str], bot_qq: str) -> None:
    """创建测试机器人。"""
    response = client.post(
        "/api/bots",
        headers=headers,
        json={"bot_qq": bot_qq, "group_mode": "all", "is_enabled": True},
    )
    assert response.json()["success"] is True


def _mock_actions(client: TestClient, friends: list[dict[str, Any]]) -> list[str]:
    """把 OneBot 动作替换为固定好友数据，并记录调用过的动作。"""
    calls: list[str] = []

    async def fake_call_action(action: str, params: dict, **_kwargs) -> dict:
        calls.append(action)
        if action == "get_friend_list":
            return {"status": "ok", "retcode": 0, "data": friends}
        if action == "get_friends_with_category":
            return {"status": "ok", "retcode": 0, "data": CATEGORIES}
        return {"status": "failed", "retcode": 1404, "data": None}

    client.app.state.container.onebot_service.call_action = fake_call_action
    return calls


def _history_messages(count: int, bot_qq: str, friend_qq: str) -> list[dict[str, Any]]:
    """构造按 message_seq 递增的私聊历史，其中每 10 条包含一条机器人发出的消息。"""
    messages: list[dict[str, Any]] = []
    for seq in range(1, count + 1):
        from_bot = seq % 10 == 0
        speaker = bot_qq if from_bot else friend_qq
        listener = friend_qq if from_bot else bot_qq
        messages.append(
            {
                "post_type": "message",
                "message_type": "private",
                "sub_type": "friend",
                "self_id": int(bot_qq),
                "message_seq": seq,
                "message_id": 1000 + seq,
                "time": 1700000000 + seq * 60,
                "user_id": int(speaker),
                "target_id": int(listener),
                "sender": {
                    "user_id": int(speaker),
                    "nickname": "机器人" if from_bot else "老王",
                },
                "message": [{"type": "text", "data": {"text": f"第 {seq} 条消息"}}],
                "raw_message": f"第 {seq} 条消息",
            }
        )
    return messages


def _mock_history(
    client: TestClient, friends: list[dict[str, Any]], history: list[dict[str, Any]]
) -> list[dict]:
    """按 message_seq 游标分页返回历史消息，模拟 NapCat 的翻页语义。"""
    requests: list[dict] = []

    async def fake_call_action(action: str, params: dict, **_kwargs) -> dict:
        if action == "get_friend_list":
            return {"status": "ok", "retcode": 0, "data": friends}
        if action == "get_friends_with_category":
            return {"status": "ok", "retcode": 0, "data": CATEGORIES}
        if action == "get_friend_msg_history":
            requests.append(dict(params))
            pool = sorted(history, key=lambda item: item["message_seq"])
            cursor = int(params.get("message_seq") or 0)
            if cursor:
                pool = [item for item in pool if item["message_seq"] < cursor]
            count = int(params.get("count") or 20)
            return {"status": "ok", "retcode": 0, "data": {"messages": pool[-count:]}}
        return {"status": "failed", "retcode": 1404, "data": None}

    client.app.state.container.onebot_service.call_action = fake_call_action
    return requests


def test_friend_sync_creates_updates_and_invalidates(tmp_path: Path) -> None:
    """两次同步分别完成新增、更新和失效标记，并写入好友资料与分组名称。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        headers = _headers(login(client, "admin", "admin-password"))
        _create_bot(client, headers, "123456")
        calls = _mock_actions(client, FIRST_BATCH)

        first = client.post("/api/friends/123456/sync", headers=headers).json()
        assert first["success"] is True
        assert first["data"]["total_count"] == 2
        assert first["data"]["created_count"] == 2
        assert first["data"]["invalid_count"] == 0
        assert "get_friends_with_category" in calls

        listed = client.get("/api/friends?page=1&page_size=20", headers=headers).json()
        assert listed["data"]["total"] == 2
        wang = next(
            item for item in listed["data"]["items"] if item["friend_qq"] == "10001"
        )
        assert wang["nickname"] == "老王"
        assert wang["remark"] == "同事老王"
        assert wang["category_name"] == "我的好友"
        assert wang["sex"] == "male"
        assert wang["level"] == 16
        assert wang["long_nick"] == "今天也要加油"
        assert wang["birthday"] == "1995-03-08"
        assert wang["synced_at"]
        mei = next(
            item for item in listed["data"]["items"] if item["friend_qq"] == "10002"
        )
        assert mei["nickname"] == "小美"
        assert mei["category_name"] == "同事"
        assert mei["birthday"] == ""

        _mock_actions(client, SECOND_BATCH)
        second = client.post("/api/friends/123456/sync", headers=headers).json()
        assert second["data"]["total_count"] == 2
        assert second["data"]["created_count"] == 1
        assert second["data"]["updated_count"] == 1
        assert second["data"]["invalid_count"] == 1

        after = client.get("/api/friends?page=1&page_size=20", headers=headers).json()
        assert {item["friend_qq"] for item in after["data"]["items"]} == {"10001", "10003"}
        renamed = next(
            item for item in after["data"]["items"] if item["friend_qq"] == "10001"
        )
        assert renamed["nickname"] == "老王改名"
        assert renamed["category_name"] == "同事"


def test_friend_filters_and_owner_isolation(tmp_path: Path) -> None:
    """普通用户只能查询和同步自己机器人的好友，筛选条件按后端生效。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        admin_headers = _headers(login(client, "admin", "admin-password"))
        registered = register_user(
            client, "alice", "alice-pass1", "alice@example.com", "甲"
        )
        alice_headers = _headers(registered["data"]["token"])
        _create_bot(client, admin_headers, "123456")
        _create_bot(client, alice_headers, "654321")
        _mock_actions(client, FIRST_BATCH)

        assert client.post("/api/friends/123456/sync", headers=admin_headers).json()["success"] is True
        assert client.post("/api/friends/654321/sync", headers=alice_headers).json()["success"] is True

        admin_listed = client.get(
            "/api/friends?page=1&page_size=20", headers=admin_headers
        ).json()
        assert admin_listed["data"]["total"] == 4

        alice_listed = client.get(
            "/api/friends?page=1&page_size=20", headers=alice_headers
        ).json()
        assert alice_listed["data"]["total"] == 2
        assert {item["bot_qq"] for item in alice_listed["data"]["items"]} == {"654321"}

        denied = client.post("/api/friends/123456/sync", headers=alice_headers).json()
        assert denied["success"] is False
        assert denied["message"] == "机器人账号不存在或无权访问"

        by_keyword = client.get(
            "/api/friends?page=1&page_size=20&keyword=小美", headers=admin_headers
        ).json()
        assert by_keyword["data"]["total"] == 2
        by_remark = client.get(
            "/api/friends?page=1&page_size=20&keyword=同事老王&bot_qq=123456",
            headers=admin_headers,
        ).json()
        assert by_remark["data"]["total"] == 1
        by_category = client.get(
            "/api/friends?page=1&page_size=20&category_name=同事", headers=admin_headers
        ).json()
        assert by_category["data"]["total"] == 2
        by_sex = client.get(
            "/api/friends?page=1&page_size=20&sex=female&bot_qq=654321",
            headers=admin_headers,
        ).json()
        assert by_sex["data"]["total"] == 1


def test_friend_sync_rejects_invalid_napcat_response(tmp_path: Path) -> None:
    """NapCat 返回异常数据时必须报错，不允许写入假数据。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        headers = _headers(login(client, "admin", "admin-password"))
        _create_bot(client, headers, "123456")

        async def broken_call_action(action: str, params: dict, **_kwargs) -> dict:
            return {"status": "failed", "retcode": 1404, "data": None}

        client.app.state.container.onebot_service.call_action = broken_call_action
        failed = client.post("/api/friends/123456/sync", headers=headers).json()
        assert failed["success"] is False
        assert failed["message"] == "NapCat 未返回有效好友列表"

        _mock_actions(client, [])
        empty = client.post("/api/friends/123456/sync", headers=headers).json()
        assert empty["success"] is False
        assert empty["message"] == "该机器人当前没有可同步的好友"
        assert client.get("/api/friends", headers=headers).json()["data"]["total"] == 0


def test_friend_sex_dictionary_is_initialized(tmp_path: Path) -> None:
    """性别枚举必须来自数据字典，供前端转换中文名称。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        response = client.get("/api/dictionaries?types=friend_sex").json()
        assert response["success"] is True
        assert response["data"]["friend_sex"] == [
            {"value": "male", "label": "男"},
            {"value": "female", "label": "女"},
            {"value": "unknown", "label": "未知"},
        ]


def test_friend_message_sync_pages_and_deduplicates(tmp_path: Path) -> None:
    """历史同步按游标翻页拉全量，重复同步不会产生重复记录。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        headers = _headers(login(client, "admin", "admin-password"))
        _create_bot(client, headers, "123456")
        history = _history_messages(60, "123456", "10001")
        requests = _mock_history(client, FIRST_BATCH, history)
        assert client.post("/api/friends/123456/sync", headers=headers).json()["success"] is True

        first = client.post(
            "/api/friends/123456/10001/messages/sync", headers=headers
        ).json()
        assert first["success"] is True
        assert first["data"]["page_count"] == 2
        assert first["data"]["fetched_count"] == 60
        assert first["data"]["created_count"] == 60
        assert first["data"]["existing_count"] == 0
        # 第一页从最新开始，第二页用本页最早的 message_seq 继续往前翻。
        assert [item["message_seq"] for item in requests] == [0, 11]

        listed = client.get(
            "/api/chat_records/messages?page=1&page_size=20&bot_qq=123456"
            "&user_qq=10001&private_only=true",
            headers=headers,
        ).json()
        assert listed["data"]["total"] == 60
        assert listed["data"]["items"][0]["message_id"] == "1060"
        assert listed["data"]["items"][0]["direction"] == "机器人发送"
        assert listed["data"]["items"][1]["message_id"] == "1059"
        assert listed["data"]["items"][1]["direction"] == "用户发送"
        assert listed["data"]["items"][0]["source"] == "history"

        second = client.post(
            "/api/friends/123456/10001/messages/sync", headers=headers
        ).json()
        assert second["data"]["created_count"] == 0
        assert second["data"]["existing_count"] == 60
        again = client.get(
            "/api/chat_records/messages?page=1&page_size=20&bot_qq=123456"
            "&user_qq=10001&private_only=true",
            headers=headers,
        ).json()
        assert again["data"]["total"] == 60


def test_friend_message_query_excludes_group_messages(tmp_path: Path) -> None:
    """按好友查询只返回私聊记录，同一个人的群消息不会混入。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        headers = _headers(login(client, "admin", "admin-password"))
        _create_bot(client, headers, "123456")
        _mock_history(client, FIRST_BATCH, _history_messages(3, "123456", "10001"))
        assert client.post("/api/friends/123456/sync", headers=headers).json()["success"] is True
        assert client.post(
            "/api/friends/123456/10001/messages/sync", headers=headers
        ).json()["data"]["created_count"] == 3

        async def seed_group_message() -> None:
            async with client.app.state.container.database.session_factory() as session:
                session.add(
                    ChatMessage(
                        bot_qq="123456", group_id="9988", message_id="group-1",
                        user_id="10001", sender_user_id="10001", sender_nickname="老王",
                        message_text="群里发言", post_type="message",
                        event_time=beijing_now(),
                    )
                )
                await session.commit()

        client.portal.call(seed_group_message)

        private_only = client.get(
            "/api/chat_records/messages?page=1&page_size=20&user_qq=10001&private_only=true",
            headers=headers,
        ).json()
        assert private_only["data"]["total"] == 3
        assert all(not item["group_id"] for item in private_only["data"]["items"])

        all_records = client.get(
            "/api/chat_records/messages?page=1&page_size=20&user_qq=10001",
            headers=headers,
        ).json()
        assert all_records["data"]["total"] == 4


def test_friend_message_sync_requires_synced_friend_and_permission(tmp_path: Path) -> None:
    """好友未同步或不属于当前用户时，历史同步必须报错。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        admin_headers = _headers(login(client, "admin", "admin-password"))
        registered = register_user(
            client, "alice", "alice-pass1", "alice@example.com", "甲"
        )
        alice_headers = _headers(registered["data"]["token"])
        _create_bot(client, admin_headers, "123456")
        _mock_history(client, FIRST_BATCH, _history_messages(3, "123456", "10001"))

        missing = client.post(
            "/api/friends/123456/99999/messages/sync", headers=admin_headers
        ).json()
        assert missing["success"] is False
        assert missing["message"] == "好友不存在或无权访问，请先同步好友列表"

        assert client.post("/api/friends/123456/sync", headers=admin_headers).json()["success"] is True
        denied = client.post(
            "/api/friends/123456/10001/messages/sync", headers=alice_headers
        ).json()
        assert denied["success"] is False
        assert denied["message"] == "机器人账号不存在或无权访问"


def test_friend_block_toggle_and_skips_message_processing(tmp_path: Path) -> None:
    """屏蔽好友后其群聊/私聊消息被直接跳过，取消屏蔽后恢复正常处理。"""
    settings = build_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = _headers(login(client, "admin", "admin-password"))
        _create_bot(client, headers, "123456")
        _mock_actions(client, FIRST_BATCH)
        assert client.post("/api/friends/123456/sync", headers=headers).json()["success"] is True

        listed = client.get("/api/friends", headers=headers).json()
        wang = next(
            item for item in listed["data"]["items"] if item["friend_qq"] == "10001"
        )
        assert wang["is_blocked"] is False
        friend_id = wang["id"]

        # 配置违禁词规则：命中即撤回并禁言，用于观测消息是否被实际处理。
        assert client.post(
            "/api/forbidden_words",
            headers=headers,
            json={
                "bot_qq": "123456",
                "group_id": "9988",
                "keywords": "违规词",
                "mute_duration": 120,
            },
        ).json()["success"] is True

        calls: list[tuple[str, dict]] = []

        async def fake_call_action(action: str, params: dict, **_kwargs):
            calls.append((action, params))
            return {"status": "ok", "retcode": 0}

        client.app.state.container.onebot_service.call_action = fake_call_action

        def post_group_event(message_id: int) -> dict:
            event = {
                "post_type": "message",
                "message_type": "group",
                "self_id": 123456,
                "user_id": 10001,
                "group_id": 9988,
                "message_id": message_id,
                "raw_message": "这是违规词内容",
                "sender": {"user_id": 10001, "role": "member"},
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

        # 未屏蔽时命中违禁词会撤回并禁言。
        assert post_group_event(501) == {}
        assert calls == [
            ("delete_msg", {"message_id": 501}),
            ("set_group_ban", {"group_id": 9988, "user_id": 10001, "duration": 120}),
        ]

        # 屏蔽该好友后，同样的群消息被直接跳过，不再触发任何动作。
        blocked = client.post(
            f"/api/friends/{friend_id}/block", headers=headers
        ).json()
        assert blocked["success"] is True
        assert blocked["data"]["is_blocked"] is True
        assert post_group_event(502) == {}
        assert len(calls) == 2

        # 私聊消息同样因屏蔽被判定为跳过。
        async def check_private_blocked() -> bool:
            return await client.app.state.container.onebot_service._is_sender_blocked(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "self_id": 123456,
                    "user_id": 10001,
                    "sender": {"user_id": 10001},
                }
            )

        assert client.portal.call(check_private_blocked) is True

        # 取消屏蔽后恢复处理，命中违禁词再次撤回并禁言。
        unblocked = client.post(
            f"/api/friends/{friend_id}/unblock", headers=headers
        ).json()
        assert unblocked["success"] is True
        assert unblocked["data"]["is_blocked"] is False
        assert post_group_event(503) == {}
        assert calls[-2:] == [
            ("delete_msg", {"message_id": 503}),
            ("set_group_ban", {"group_id": 9988, "user_id": 10001, "duration": 120}),
        ]


def test_friend_block_requires_permission(tmp_path: Path) -> None:
    """普通用户不能屏蔽他人机器人的好友。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        admin_headers = _headers(login(client, "admin", "admin-password"))
        registered = register_user(
            client, "alice", "alice-pass1", "alice@example.com", "甲"
        )
        alice_headers = _headers(registered["data"]["token"])
        _create_bot(client, admin_headers, "123456")
        _mock_actions(client, FIRST_BATCH)
        assert client.post("/api/friends/123456/sync", headers=admin_headers).json()["success"] is True
        friend_id = client.get("/api/friends", headers=admin_headers).json()["data"]["items"][0]["id"]

        denied = client.post(
            f"/api/friends/{friend_id}/block", headers=alice_headers
        ).json()
        assert denied["success"] is False
        assert denied["message"] == "好友不存在或无权访问"


def test_friend_message_sync_rejects_invalid_history_response(tmp_path: Path) -> None:
    """NapCat 历史返回格式异常时必须报错，不写入任何记录。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        headers = _headers(login(client, "admin", "admin-password"))
        _create_bot(client, headers, "123456")
        _mock_actions(client, FIRST_BATCH)
        assert client.post("/api/friends/123456/sync", headers=headers).json()["success"] is True

        failed = client.post(
            "/api/friends/123456/10001/messages/sync", headers=headers
        ).json()
        assert failed["success"] is False
        assert failed["message"] == "NapCat 未返回有效聊天记录"
        assert client.get(
            "/api/chat_records/messages?page=1&page_size=20", headers=headers
        ).json()["data"]["total"] == 0
