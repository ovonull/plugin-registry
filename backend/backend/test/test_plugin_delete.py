"""插件删除测试：作者删自己的、管理员删任意、其他人无权删除。"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.application import create_app
from test_plugins import (
    VALID_MANIFEST,
    build_package,
    build_plugin_settings,
    submit_package,
)
from test_service import login, register_user


PLUGIN_ID = str(VALID_MANIFEST["id"])
VERSION = str(VALID_MANIFEST["version"])


def _headers(token: str) -> dict[str, str]:
    """构造带登录令牌的请求头。"""
    return {"Authorization": f"Bearer {token}"}


def _user_id(client: TestClient, token: str, username: str) -> int:
    """按用户名查出用户编号，用于信任名单授权。"""
    listed = client.get("/api/users", headers=_headers(token)).json()
    assert listed["success"] is True, listed
    return next(
        item["id"] for item in listed["data"]["items"] if item["username"] == username
    )


def _grant_trust(client: TestClient, admin_token: str, user_id: int) -> None:
    """把用户加进代码插件信任名单，允许其提交插件。"""
    granted = client.post(
        "/api/plugin_admin/trusted-authors",
        headers=_headers(admin_token),
        json={"user_id": user_id, "reason": "删除测试需要上传权限"},
    ).json()
    assert granted["success"] is True, granted


def _approve_and_publish(client: TestClient, admin_token: str) -> None:
    """把唯一版本审核通过并上架。"""
    approved = client.post(
        "/api/plugin_admin/versions/approve",
        headers=_headers(admin_token),
        json={
            "plugin_id": PLUGIN_ID,
            "version": VERSION,
            "granted_capabilities": ["msg.send", "http.outbound"],
            "comment": "删除测试",
        },
    ).json()
    assert approved["success"] is True, approved
    published = client.post(
        "/api/plugin_admin/publish",
        headers=_headers(admin_token),
        json={"plugin_id": PLUGIN_ID, "version": VERSION},
    ).json()
    assert published["success"] is True, published


def test_author_can_delete_own_plugin(tmp_path: Path) -> None:
    """作者删除自己的插件：版本、安装记录、包目录一起清掉，同版本号可重新提交。"""
    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        admin_token = login(client, "admin", "admin-password")
        register_user(client, "author_one", "author-pass1", "author@example.com", "插件作者")
        author_token = login(client, "author_one", "author-pass1")
        _grant_trust(
            client, admin_token, _user_id(client, admin_token, "author_one")
        )

        submitted = submit_package(client, author_token, build_package(VALID_MANIFEST))
        assert submitted["success"] is True, submitted
        _approve_and_publish(client, admin_token)

        created_bot = client.post(
            "/api/bots",
            headers=_headers(author_token),
            json={"bot_qq": "20001", "group_mode": "all", "is_enabled": True},
        ).json()
        assert created_bot["success"] is True, created_bot
        installed = client.post(
            "/api/plugins/installations",
            headers=_headers(author_token),
            json={"plugin_id": PLUGIN_ID, "bot_qqs": ["20001"]},
        ).json()
        assert installed["success"] is True, installed

        package_dir = tmp_path / "plugin-data" / "packages" / PLUGIN_ID
        assert package_dir.is_dir(), "提交后包目录应已落盘"

        deleted = client.post(
            f"/api/plugins/{PLUGIN_ID}/delete", headers=_headers(author_token)
        ).json()
        assert deleted["success"] is True, deleted
        assert deleted["data"]["plugin_id"] == PLUGIN_ID
        assert deleted["data"]["deleted_versions"] == 1
        assert deleted["data"]["affected_installations"] == 1
        assert not package_dir.exists(), "包目录必须一并删除"

        # 插件与安装记录都从列表里消失。
        listed = client.get("/api/plugins", headers=_headers(author_token)).json()
        assert all(item["plugin_id"] != PLUGIN_ID for item in listed["data"]["items"])
        remaining = client.get(
            "/api/plugins/installations", headers=_headers(author_token)
        ).json()
        assert remaining["data"]["total"] == 0, remaining["data"]["items"]

        # 删除动作要留痕。
        logs = client.get(
            "/api/plugin_admin/review-logs",
            headers=_headers(admin_token),
            params={"action": "delete"},
        ).json()
        assert logs["data"]["total"] >= 1, logs["data"]

        # 版本记录是物删除的，同一个版本号可以重新提交。
        resubmitted = submit_package(
            client, author_token, build_package(VALID_MANIFEST)
        )
        assert resubmitted["success"] is True, resubmitted


def test_only_author_or_admin_can_delete(tmp_path: Path) -> None:
    """其他用户删除他人插件被拒，管理员可以删任意插件。"""
    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        admin_token = login(client, "admin", "admin-password")
        register_user(client, "author_two", "author-pass2", "author2@example.com", "插件作者")
        register_user(client, "other_user", "other-pass2", "other@example.com", "路人用户")
        author_token = login(client, "author_two", "author-pass2")
        other_token = login(client, "other_user", "other-pass2")
        _grant_trust(
            client, admin_token, _user_id(client, admin_token, "author_two")
        )

        submitted = submit_package(client, author_token, build_package(VALID_MANIFEST))
        assert submitted["success"] is True, submitted
        _approve_and_publish(client, admin_token)

        denied = client.post(
            f"/api/plugins/{PLUGIN_ID}/delete", headers=_headers(other_token)
        ).json()
        assert denied["success"] is False, denied
        assert denied["code"] == 40382, denied

        # 被拒后插件原样保留，仍对普通用户可见。
        still_there = client.get("/api/plugins", headers=_headers(other_token)).json()
        assert any(item["plugin_id"] == PLUGIN_ID for item in still_there["data"]["items"])

        removed = client.post(
            f"/api/plugins/{PLUGIN_ID}/delete", headers=_headers(admin_token)
        ).json()
        assert removed["success"] is True, removed
        assert removed["data"]["deleted_versions"] == 1
        assert removed["data"]["affected_installations"] == 0

        listed = client.get("/api/plugins", headers=_headers(admin_token)).json()
        assert all(item["plugin_id"] != PLUGIN_ID for item in listed["data"]["items"])
