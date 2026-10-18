"""插件审核流转、信任名单、安装启停与事件钩子测试。"""

import hashlib
import hmac
import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.application import create_app
from app.models.plugin import PluginInstallation
from test_plugins import (
    VALID_MANIFEST,
    build_package,
    build_plugin_settings,
    submit_package,
)
from test_service import build_settings, login, register_user


BOT_QQ = "123456"

HIGH_RISK_MANIFEST: dict = {
    **VALID_MANIFEST,
    "id": "com.example.moderator",
    "name": "群管助手",
    "capabilities": ["msg.send", "group.kick"],
    "allowed_domains": [],
}

CONFIG_MANIFEST: dict = {
    **VALID_MANIFEST,
    "id": "com.example.config",
    "name": "配置演示",
    "capabilities": ["msg.send", "group.kick", "group.mute"],
    "allowed_domains": [],
    "config_schema": [
        {"key": "city", "label": "默认城市", "type": "text", "required": True},
        {
            "key": "api_key",
            "label": "服务密钥",
            "type": "password",
            "sensitive": True,
        },
        {"key": "limit", "label": "次数上限", "type": "number"},
        {"key": "mode", "label": "回复模式", "type": "select", "options": ["a", "b"]},
    ],
}

EVENT: dict = {
    "post_type": "message",
    "message_type": "group",
    "self_id": int(BOT_QQ),
    "user_id": 88,
    "group_id": 99,
    "message_id": 321,
    "raw_message": "插件事件测试",
    "message": [{"type": "text", "data": {"text": "插件事件测试"}}],
    "sender": {"user_id": 88, "nickname": "测试用户", "role": "member"},
}


class FakeRuntime:
    """记录分发事件的假执行单元，用于验证钩子是否按启用状态分发。"""

    def __init__(self) -> None:
        """初始化事件记录容器。"""
        self.events: list[tuple[dict, list, str]] = []

    async def handle_event(self, event, installations, source) -> None:
        """记录一次事件分发。"""
        self.events.append((event, installations, source))

    async def close(self) -> None:
        """无需释放资源。"""
        return None


def _headers(token: str) -> dict[str, str]:
    """构造带令牌的请求头。"""
    return {"Authorization": f"Bearer {token}"}


def _create_bot(client: TestClient, token: str, bot_qq: str = BOT_QQ) -> None:
    """创建并启用测试机器人。"""
    response = client.post(
        "/api/bots",
        headers=_headers(token),
        json={"bot_qq": bot_qq, "group_mode": "all", "is_enabled": True},
    )
    assert response.json()["success"] is True


def _submit_review_publish(client: TestClient, token: str, manifest: dict, granted=None) -> dict:
    """提交插件包、审核通过并上架，返回审核后的版本信息。"""
    submitted = submit_package(client, token, build_package(manifest))
    assert submitted["success"] is True, submitted
    approved = client.post(
        "/api/plugin_admin/versions/approve",
        headers=_headers(token),
        json={
            "plugin_id": manifest["id"],
            "version": manifest["version"],
            "granted_capabilities": granted,
            "comment": "测试审核",
        },
    ).json()
    assert approved["success"] is True, approved
    published = client.post(
        "/api/plugin_admin/publish",
        headers=_headers(token),
        json={"plugin_id": manifest["id"]},
    ).json()
    assert published["success"] is True, published
    return approved["data"]["version"]


def _install(
    client: TestClient, token: str, plugin_id: str, bot_qqs: list[str] | None = None
) -> dict:
    """为当前账号安装插件并分配机器人。"""
    return client.post(
        "/api/plugins/installations",
        headers=_headers(token),
        json={"plugin_id": plugin_id, "bot_qqs": bot_qqs or [BOT_QQ]},
    ).json()


def _user_id(client: TestClient, token: str, username: str) -> int:
    """按用户名查出用户编号。"""
    listed = client.get("/api/users", headers=_headers(token)).json()
    assert listed["success"] is True, listed
    return next(item["id"] for item in listed["data"]["items"] if item["username"] == username)


def _post_event(client: TestClient, settings, event: dict) -> dict:
    """按 NapCat 签名规则上报一条 HTTP 事件。"""
    raw = json.dumps(event, ensure_ascii=False).encode()
    signature = "sha1=" + hmac.new(
        settings.napcat_token.encode(), raw, hashlib.sha1
    ).hexdigest()
    response = client.post(
        "/onebot/v11/events?source=napcat",
        content=raw,
        headers={"x-signature": signature},
    )
    return response.json()


def test_review_approve_publish_install_and_enable_flow(tmp_path: Path) -> None:
    """审核通过只批准非高风险能力，上架后可安装，启用前必须填写必填配置。"""
    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        token = login(client, "admin", "admin-password")
        headers = _headers(token)
        version = _submit_review_publish(client, token, VALID_MANIFEST)
        # 清单声明 msg.send 与 http.outbound，其中 http.outbound 属高风险，默认不批准。
        assert version["granted_capabilities"] == ["msg.send"]
        assert version["is_current"] is True

        detail = client.get("/api/plugins/com.example.weather", headers=headers).json()
        assert detail["data"]["plugin"]["is_published"] is True
        assert detail["data"]["plugin"]["current_version"]["version"] == "1.0.0"

        _create_bot(client, token)
        installed = _install(client, token, "com.example.weather")
        assert installed["success"] is True, installed
        assert installed["data"]["is_enabled"] is False
        assert installed["data"]["granted_capabilities"] == ["msg.send"]
        install_id = installed["data"]["id"]

        listed = client.get("/api/plugins/installations", headers=headers).json()
        assert listed["data"]["total"] == 1
        assert listed["data"]["items"][0]["plugin_name"] == "天气查询"

        blocked = client.put(
            f"/api/plugins/installations/{install_id}",
            headers=headers,
            json={"is_enabled": True},
        ).json()
        assert blocked["success"] is False
        assert blocked["code"] == 40110
        assert "默认城市" in blocked["message"]

        enabled = client.put(
            f"/api/plugins/installations/{install_id}",
            headers=headers,
            json={"is_enabled": True, "config": {"city": "北京"}},
        ).json()
        assert enabled["success"] is True, enabled
        assert enabled["data"]["is_enabled"] is True
        assert enabled["data"]["config"]["city"] == "北京"

        logs = client.get("/api/plugin_admin/review-logs", headers=headers).json()
        actions = [item["action"] for item in logs["data"]["items"]]
        assert actions[:3] == ["publish", "approve", "submit"]
        assert "批准能力：msg.send；未批准：http.outbound" in logs["data"]["items"][1]["comment"]

        unpublished = client.post(
            "/api/plugin_admin/unpublish",
            headers=headers,
            json={"plugin_id": "com.example.weather"},
        ).json()
        assert unpublished["success"] is True
        again = client.put(
            f"/api/plugins/installations/{install_id}",
            headers=headers,
            json={"is_enabled": True},
        ).json()
        assert again["success"] is False
        assert again["code"] == 40112

        removed = client.post(
            f"/api/plugins/installations/{install_id}/delete", headers=headers
        ).json()
        assert removed["success"] is True
        remaining = client.get(
            f"/api/plugins/installations?plugin_id=com.example.weather", headers=headers
        ).json()
        assert remaining["data"]["total"] == 0


def test_high_risk_capability_requires_explicit_selection(tmp_path: Path) -> None:
    """高风险能力默认不批准，管理员显式勾选后生效，未申请的能力不能批准。"""
    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        token = login(client, "admin", "admin-password")
        headers = _headers(token)
        first = _submit_review_publish(client, token, HIGH_RISK_MANIFEST)
        assert first["granted_capabilities"] == ["msg.send"]

        second_manifest = {**HIGH_RISK_MANIFEST, "version": "1.0.1"}
        submit_package(client, token, build_package(second_manifest))
        second = client.post(
            "/api/plugin_admin/versions/approve",
            headers=headers,
            json={
                "plugin_id": "com.example.moderator",
                "version": "1.0.1",
                "granted_capabilities": ["msg.send", "group.kick"],
            },
        ).json()
        assert second["success"] is True, second
        assert second["data"]["version"]["granted_capabilities"] == [
            "msg.send",
            "group.kick",
        ]
        # 已上架插件换版本需要显式上架，未上架时当前生效版本保持不变。
        assert second["data"]["version"]["is_current"] is False

        third_manifest = {**HIGH_RISK_MANIFEST, "version": "1.0.2"}
        submit_package(client, token, build_package(third_manifest))
        illegal = client.post(
            "/api/plugin_admin/versions/approve",
            headers=headers,
            json={
                "plugin_id": "com.example.moderator",
                "version": "1.0.2",
                "granted_capabilities": ["group.mute"],
            },
        ).json()
        assert illegal["success"] is False
        assert illegal["code"] == 40091

        switched = client.post(
            "/api/plugin_admin/publish",
            headers=headers,
            json={"plugin_id": "com.example.moderator", "version": "1.0.1"},
        ).json()
        assert switched["success"] is True
        detail = client.get("/api/plugins/com.example.moderator", headers=headers).json()
        assert detail["data"]["plugin"]["current_version"]["version"] == "1.0.1"


def test_reject_requires_reason_and_can_be_reapproved(tmp_path: Path) -> None:
    """驳回必须填写原因，驳回后仍可重新审核通过。"""
    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        token = login(client, "admin", "admin-password")
        headers = _headers(token)
        submitted = submit_package(client, token, build_package(VALID_MANIFEST))
        assert submitted["success"] is True

        missing = client.post(
            "/api/plugin_admin/versions/reject",
            headers=headers,
            json={
                "plugin_id": "com.example.weather",
                "version": "1.0.0",
                "comment": "",
            },
        ).json()
        assert missing["success"] is False
        assert missing["code"] == 40002

        rejected = client.post(
            "/api/plugin_admin/versions/reject",
            headers=headers,
            json={
                "plugin_id": "com.example.weather",
                "version": "1.0.0",
                "comment": "请补充出网域名说明",
            },
        ).json()
        assert rejected["success"] is True, rejected
        assert rejected["data"]["version"]["review_status"] == "rejected"
        assert rejected["data"]["version"]["granted_capabilities"] == []

        again = client.post(
            "/api/plugin_admin/versions/reject",
            headers=headers,
            json={
                "plugin_id": "com.example.weather",
                "version": "1.0.0",
                "comment": "重复驳回",
            },
        ).json()
        assert again["success"] is False
        assert again["code"] == 40121

        approved = client.post(
            "/api/plugin_admin/versions/approve",
            headers=headers,
            json={"plugin_id": "com.example.weather", "version": "1.0.0"},
        ).json()
        assert approved["success"] is True
        assert approved["data"]["version"]["review_status"] == "approved"

        unknown = client.post(
            "/api/plugin_admin/versions/approve",
            headers=headers,
            json={"plugin_id": "com.example.weather", "version": "9.9.9"},
        ).json()
        assert unknown["success"] is False
        assert unknown["code"] == 40482


def test_review_and_trust_endpoints_require_admin(tmp_path: Path) -> None:
    """非管理员不能进入审核台与信任名单。"""
    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        register_user(client, "plain_user", "plain-pass1", "plain@example.com", "普通用户")
        token = login(client, "plain_user", "plain-pass1")
        headers = _headers(token)
        for path in ("/api/plugin_admin/versions", "/api/plugin_admin/review-logs", "/api/plugin_admin/trusted-authors"):
            denied = client.get(path, headers=headers).json()
            assert denied["success"] is False
            assert denied["code"] == 40384
        denied = client.post(
            "/api/plugin_admin/publish",
            headers=headers,
            json={"plugin_id": "com.example.weather"},
        ).json()
        assert denied["code"] == 40384


def test_trust_grant_enables_upload_and_revoke_blocks(tmp_path: Path) -> None:
    """信任名单授权后可上传代码插件，撤销后立即被拒。"""
    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        admin_token = login(client, "admin", "admin-password")
        admin_headers = _headers(admin_token)
        register_user(client, "author_one", "author-pass1", "author@example.com", "插件作者")
        user_token = login(client, "author_one", "author-pass1")

        denied = submit_package(client, user_token, build_package(VALID_MANIFEST))
        assert denied["success"] is False
        assert denied["code"] == 40381

        author_id = _user_id(client, admin_token, "author_one")
        granted = client.post(
            "/api/plugin_admin/trusted-authors",
            headers=admin_headers,
            json={"user_id": author_id, "reason": "完成实名与代码规范确认"},
        ).json()
        assert granted["success"] is True, granted
        assert granted["data"]["is_active"] is True

        allowed = submit_package(client, user_token, build_package(VALID_MANIFEST))
        assert allowed["success"] is True, allowed

        duplicated = client.post(
            "/api/plugin_admin/trusted-authors",
            headers=admin_headers,
            json={"user_id": author_id, "reason": "重复授权"},
        ).json()
        assert duplicated["success"] is False
        assert duplicated["code"] == 40123

        admin_id = _user_id(client, admin_token, "admin")
        admin_denied = client.post(
            "/api/plugin_admin/trusted-authors",
            headers=admin_headers,
            json={"user_id": admin_id, "reason": "管理员无需授权"},
        ).json()
        assert admin_denied["code"] == 40123

        revoked = client.post(
            f"/api/plugin_admin/trusted-authors/{granted['data']['id']}/revoke",
            headers=admin_headers,
        ).json()
        assert revoked["success"] is True
        blocked = submit_package(
            client,
            user_token,
            build_package({**VALID_MANIFEST, "version": "1.1.0"}),
        )
        assert blocked["success"] is False
        assert blocked["code"] == 40381

        listed = client.get("/api/plugin_admin/trusted-authors", headers=admin_headers).json()
        assert listed["data"]["total"] == 1
        assert listed["data"]["items"][0]["is_active"] is False
        assert listed["data"]["items"][0]["username"] == "author_one"


def test_config_encryption_validation_and_capability_narrowing(tmp_path: Path) -> None:
    """插件配置按清单校验、敏感项加密入库，生效能力只能收窄。"""
    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        token = login(client, "admin", "admin-password")
        headers = _headers(token)
        _create_bot(client, token)
        _submit_review_publish(
            client,
            token,
            CONFIG_MANIFEST,
            granted=["msg.send", "group.kick", "group.mute"],
        )
        installed = _install(client, token, "com.example.config")
        assert installed["success"] is True, installed
        install_id = installed["data"]["id"]
        assert len(installed["data"]["config_schema"]) == 4

        bad_number = client.put(
            f"/api/plugins/installations/{install_id}",
            headers=headers,
            json={"config": {"city": "上海", "limit": "abc"}},
        ).json()
        assert bad_number["success"] is False
        assert "必须填写数字" in bad_number["message"]

        bad_select = client.put(
            f"/api/plugins/installations/{install_id}",
            headers=headers,
            json={"config": {"city": "上海", "mode": "c"}},
        ).json()
        assert bad_select["success"] is False
        assert "只能填写：a、b" in bad_select["message"]

        saved = client.put(
            f"/api/plugins/installations/{install_id}",
            headers=headers,
            json={
                "is_enabled": True,
                "config": {"city": "上海", "api_key": "top-secret", "limit": 5, "mode": "a"},
            },
        ).json()
        assert saved["success"] is True, saved
        assert saved["data"]["config"]["api_key"] == "top-secret"
        assert saved["data"]["config"]["limit"] == 5

        async def load_raw_config() -> str:
            async with client.app.state.container.database.session_factory() as session:
                row = await session.scalar(
                    select(PluginInstallation).where(
                        PluginInstallation.plugin_id == "com.example.config"
                    )
                )
                return row.config_json

        stored = json.loads(client.portal.call(load_raw_config))
        assert stored["api_key"].startswith("enc:")
        assert "top-secret" not in stored["api_key"]

        masked = client.put(
            f"/api/plugins/installations/{install_id}",
            headers=headers,
            json={"config": {"api_key": "******"}},
        ).json()
        assert masked["data"]["config"]["api_key"] == "top-secret"

        narrowed = client.put(
            f"/api/plugins/installations/{install_id}",
            headers=headers,
            json={"granted_capabilities": ["msg.send"]},
        ).json()
        assert narrowed["success"] is True
        assert narrowed["data"]["granted_capabilities"] == ["msg.send"]
        assert narrowed["data"]["approved_capabilities"] == [
            "msg.send",
            "group.kick",
            "group.mute",
        ]

        widened = client.put(
            f"/api/plugins/installations/{install_id}",
            headers=headers,
            json={"granted_capabilities": ["friend.info"]},
        ).json()
        assert widened["success"] is False
        assert widened["code"] == 40110
        assert "未批准的能力" in widened["message"]


def test_install_requires_published_and_approved_plugin(tmp_path: Path) -> None:
    """未上架或未通过审核的插件不能安装。"""
    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        token = login(client, "admin", "admin-password")
        headers = _headers(token)
        _create_bot(client, token)
        submit_package(client, token, build_package(VALID_MANIFEST))

        not_published = _install(client, token, "com.example.weather")
        assert not_published["success"] is False
        assert not_published["code"] == 40112

        approved = client.post(
            "/api/plugin_admin/versions/approve",
            headers=headers,
            json={"plugin_id": "com.example.weather", "version": "1.0.0"},
        ).json()
        assert approved["success"] is True
        still_blocked = _install(client, token, "com.example.weather")
        assert still_blocked["code"] == 40112

        client.post(
            "/api/plugin_admin/publish",
            headers=headers,
            json={"plugin_id": "com.example.weather"},
        )
        duplicated = _install(client, token, "com.example.weather")
        assert duplicated["success"] is True
        again = _install(client, token, "com.example.weather")
        assert again["success"] is False
        assert again["code"] == 40111

        unknown = _install(client, token, "com.example.missing")
        assert unknown["code"] == 40480


def test_event_hook_dispatches_only_enabled_installations(tmp_path: Path) -> None:
    """事件主干只把事件分发给已启用的安装，插件功能关闭时完全不分发。"""
    settings = build_plugin_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        token = login(client, "admin", "admin-password")
        headers = _headers(token)
        _create_bot(client, token)
        _submit_review_publish(client, token, VALID_MANIFEST)
        _install(client, token, "com.example.weather")

        hooks = client.app.state.container.plugin_hooks
        runtime = FakeRuntime()
        hooks.runtime = runtime

        _post_event(client, settings, EVENT)
        client.portal.call(hooks.wait_pending)
        assert runtime.events == []

        listed = client.get("/api/plugins/installations", headers=headers).json()
        install_id = listed["data"]["items"][0]["id"]
        client.put(
            f"/api/plugins/installations/{install_id}",
            headers=headers,
            json={"is_enabled": True, "config": {"city": "北京"}},
        )

        _post_event(client, settings, EVENT)
        client.portal.call(hooks.wait_pending)
        assert len(runtime.events) == 1
        _, installations, source = runtime.events[0]
        assert source == "OneBot HTTP"
        assert installations[0]["plugin_id"] == "com.example.weather"
        assert installations[0]["bot_qq"] == BOT_QQ
        assert installations[0]["granted_capabilities"] == ["msg.send"]

    off_dir = tmp_path / "off"
    off_dir.mkdir(exist_ok=True)
    off_settings = build_settings(off_dir)
    with TestClient(create_app(off_settings)) as client:
        hooks = client.app.state.container.plugin_hooks
        runtime = FakeRuntime()
        hooks.runtime = runtime
        _post_event(client, off_settings, EVENT)
        client.portal.call(hooks.wait_pending)
        assert runtime.events == []


def test_action_logs_endpoint_is_admin_only(tmp_path: Path) -> None:
    """审计查询接口对管理员开放，尚未产生调用时返回空列表。"""
    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        admin = login(client, "admin", "admin-password")
        response = client.get(
            "/api/plugin_admin/action-logs", headers=_headers(admin)
        ).json()
        assert response["success"] is True, response
        assert response["data"]["total"] == 0
        assert response["data"]["items"] == []


def test_install_is_account_scoped_and_shared_by_bots(tmp_path: Path) -> None:
    """同一账号只安装一次，多台机器人共用同一份安装与配置。"""
    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        token = login(client, "admin", "admin-password")
        headers = _headers(token)
        _create_bot(client, token, BOT_QQ)
        _create_bot(client, token, "654321")
        _submit_review_publish(client, token, VALID_MANIFEST)

        installed = _install(
            client, token, "com.example.weather", [BOT_QQ, "654321"]
        )
        assert installed["success"] is True, installed
        assert installed["data"]["bot_qqs"] == sorted([BOT_QQ, "654321"])
        install_id = installed["data"]["id"]

        listed = client.get("/api/plugins/installations", headers=headers).json()
        assert listed["data"]["total"] == 1

        # 调整生效机器人只改分配关系，不会再产生一条安装记录。
        narrowed = client.put(
            f"/api/plugins/installations/{install_id}",
            headers=headers,
            json={"bot_qqs": [BOT_QQ]},
        ).json()
        assert narrowed["success"] is True, narrowed
        assert narrowed["data"]["bot_qqs"] == [BOT_QQ]
        again = client.get("/api/plugins/installations", headers=headers).json()
        assert again["data"]["total"] == 1

        # 取消全部机器人后不允许开启，避免启用了一个没有生效范围的插件。
        emptied = client.put(
            f"/api/plugins/installations/{install_id}",
            headers=headers,
            json={"bot_qqs": []},
        ).json()
        assert emptied["success"] is True, emptied
        blocked = client.put(
            f"/api/plugins/installations/{install_id}",
            headers=headers,
            json={"is_enabled": True},
        ).json()
        assert blocked["success"] is False
        assert blocked["code"] == 40110
        assert "生效机器人" in blocked["message"]

        # 补齐必填配置并指定机器人后可以开启；机器人被全部取消时安装自动停用。
        client.put(
            f"/api/plugins/installations/{install_id}",
            headers=headers,
            json={"config": {"city": "北京"}},
        )
        enabled = client.put(
            f"/api/plugins/installations/{install_id}",
            headers=headers,
            json={"is_enabled": True, "bot_qqs": [BOT_QQ]},
        ).json()
        assert enabled["success"] is True, enabled
        assert enabled["data"]["is_enabled"] is True
        cleared = client.put(
            f"/api/plugins/installations/{install_id}",
            headers=headers,
            json={"bot_qqs": []},
        ).json()
        assert cleared["success"] is True, cleared
        assert cleared["data"]["is_enabled"] is False


def test_installation_is_isolated_between_accounts(tmp_path: Path) -> None:
    """不同账号安装同一插件互不可见，也不能分配到别人的机器人。"""
    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        admin_token = login(client, "admin", "admin-password")
        _create_bot(client, admin_token, BOT_QQ)
        _submit_review_publish(client, admin_token, VALID_MANIFEST)
        assert _install(client, admin_token, "com.example.weather")["success"] is True

        register_user(client, "user_a", "user-pass1", "usera@example.com", "用户甲")
        register_user(client, "user_b", "user-pass2", "userb@example.com", "用户乙")
        token_a = login(client, "user_a", "user-pass1")
        token_b = login(client, "user_b", "user-pass2")
        _create_bot(client, token_a, "111111")
        _create_bot(client, token_b, "222222")

        install_a = _install(client, token_a, "com.example.weather", ["111111"])
        install_b = _install(client, token_b, "com.example.weather", ["222222"])
        assert install_a["success"] is True, install_a
        assert install_b["success"] is True, install_b
        assert install_a["data"]["id"] != install_b["data"]["id"]
        assert install_a["data"]["owner_id"] != install_b["data"]["owner_id"]

        # 各自的列表只包含自己的安装与自己的机器人。
        list_a = client.get(
            "/api/plugins/installations", headers=_headers(token_a)
        ).json()
        assert list_a["data"]["total"] == 1
        assert list_a["data"]["items"][0]["bot_qqs"] == ["111111"]
        list_b = client.get(
            "/api/plugins/installations", headers=_headers(token_b)
        ).json()
        assert list_b["data"]["total"] == 1
        assert list_b["data"]["items"][0]["bot_qqs"] == ["222222"]

        # 想把插件分配给别人名下的机器人时直接拒绝。
        stolen = _install(client, token_b, "com.example.weather", ["111111"])
        assert stolen["success"] is False
        assert stolen["code"] == 40383


def test_review_versions_collapse_to_latest_and_filter_by_name(tmp_path: Path) -> None:
    """同一插件只占一行审核条目，且筛选支持插件名称与标识片段。"""
    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        token = login(client, "admin", "admin-password")
        headers = _headers(token)

        # 同一插件先后提交两个版本，审核台只应保留最新那条。
        first = submit_package(client, token, build_package(VALID_MANIFEST))
        assert first["success"] is True, first
        second_manifest = {**VALID_MANIFEST, "version": "1.1.0"}
        second = submit_package(client, token, build_package(second_manifest))
        assert second["success"] is True, second

        listed = client.get("/api/plugin_admin/versions", headers=headers).json()
        assert listed["success"] is True, listed
        assert listed["data"]["total"] == 1, listed["data"]["items"]
        assert listed["data"]["items"][0]["version"] == "1.1.0"

        # 名称片段、标识片段、关键词都能筛到同一个插件。
        for query in (
            {"plugin_id": "天气"},
            {"plugin_id": "weather"},
            {"keyword": "天气"},
            {"keyword": "1.1.0"},
        ):
            hit = client.get(
                "/api/plugin_admin/versions", headers=headers, params=query
            ).json()
            assert hit["data"]["total"] == 1, (query, hit["data"]["items"])

        # 不相关的关键字不应命中任何条目。
        miss = client.get(
            "/api/plugin_admin/versions", headers=headers, params={"keyword": "不存在的插件"}
        ).json()
        assert miss["data"]["total"] == 0

        # 审核流水同样支持按名称筛选，并返回插件名称而不是标识。
        log = client.get(
            "/api/plugin_admin/review-logs",
            headers=headers,
            params={"plugin_id": "天气"},
        ).json()
        assert log["data"]["total"] >= 1, log["data"]
        assert log["data"]["items"][0]["plugin_name"] == "天气查询"
