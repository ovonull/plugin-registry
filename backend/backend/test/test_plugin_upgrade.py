"""插件安装升级测试：换版本、配置迁移、能力收窄与升级校验。"""

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.services import plugin_sdk


PLUGIN_ID = "com.example.upgrade"


def manifest(version: str, capabilities: list[str], schema: list[dict[str, Any]]) -> dict:
    """构造一份合法清单，用于模拟同一个插件的不同版本。"""
    payload = {
        "manifest_version": 1,
        "id": PLUGIN_ID,
        "name": "升级测试插件",
        "version": version,
        "description": f"用于测试升级的插件 {version}",
        "author": {"name": "测试作者", "url": ""},
        "license": "MIT",
        "homepage": "",
        "entry": "plugin.py",
        "capabilities": capabilities,
        "config_schema": schema,
    }
    # 只有声明了出网能力才允许填域名白名单，否则清单校验会拒绝。
    if "http.outbound" in capabilities:
        payload["allowed_domains"] = ["api.weather.com"]
    return payload


CITY_FIELD = {
    "key": "city",
    "label": "默认城市",
    "type": "text",
    "required": True,
    "sensitive": False,
}
LEGACY_FIELD = {
    "key": "legacy_city",
    "label": "旧版城市",
    "type": "text",
    "required": False,
    "sensitive": True,
}
NEW_FIELD = {
    "key": "unit",
    "label": "温度单位",
    "type": "select",
    "required": False,
    "sensitive": False,
    "options": ["摄氏度", "华氏度"],
}


def setup_client(tmp_path: Path) -> tuple[TestClient, str]:
    """创建启用插件功能的测试客户端，并登录管理员拿到令牌。"""
    from test_plugins import build_plugin_settings
    from test_service import login

    client = TestClient(create_app(build_plugin_settings(tmp_path)))
    client.__enter__()
    token = login(client, "admin", "admin-password")
    created = client.post(
        "/api/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"bot_qq": "10001", "group_mode": "all", "is_enabled": True},
    ).json()
    assert created["success"] is True, created
    return client, token


def upload_and_publish(
    client: TestClient, token: str, version_manifest: dict, capabilities: list[str]
) -> dict:
    """上传一个版本、审核通过并上架，返回审核接口的响应体。"""
    from test_plugins import submit_package

    headers = {"Authorization": f"Bearer {token}"}
    submitted = submit_package(client, token, build_package(version_manifest))
    assert submitted["success"] is True, submitted
    approved = client.post(
        "/api/plugin_admin/versions/approve",
        headers=headers,
        json={
            "plugin_id": PLUGIN_ID,
            "version": version_manifest["version"],
            "granted_capabilities": capabilities,
            "comment": "升级测试审核",
        },
    ).json()
    assert approved["success"] is True, approved
    published = client.post(
        "/api/plugin_admin/publish",
        headers=headers,
        json={"plugin_id": PLUGIN_ID, "version": version_manifest["version"]},
    ).json()
    assert published["success"] is True, published
    return approved


def build_package(manifest_payload: dict) -> bytes:
    """把清单与最小入口文件打包成内存 zip。"""
    from test_plugins import build_package as build

    return build(
        manifest_payload,
        {"plugin.py": "from app.services.plugin_sdk import on_message\n\n\n@on_message\nasync def handle(event, host):\n    return None\n"},
    )


def install_and_configure(client: TestClient, token: str, config: dict) -> dict:
    """安装插件并写入配置、开启，返回安装记录。"""
    headers = {"Authorization": f"Bearer {token}"}
    installed = client.post(
        "/api/plugins/installations",
        headers=headers,
        json={"plugin_id": PLUGIN_ID, "bot_qqs": ["10001"]},
    ).json()
    assert installed["success"] is True, installed
    updated = client.put(
        f"/api/plugins/installations/{installed['data']['id']}",
        headers=headers,
        json={
            "config": config,
            "granted_capabilities": installed["data"]["approved_capabilities"],
            "is_enabled": True,
        },
    ).json()
    assert updated["success"] is True, updated
    return updated["data"]


def test_installation_reports_available_update(tmp_path: Path) -> None:
    """发了新版本后，安装记录会带上可升级标记与新版本已批准能力。"""
    client, token = setup_client(tmp_path)
    try:
        upload_and_publish(
            client,
            token,
            manifest("1.0.0", ["msg.send"], [CITY_FIELD, LEGACY_FIELD]),
            ["msg.send"],
        )
        install_and_configure(client, token, {"city": "北京", "legacy_city": "旧值"})

        upload_and_publish(
            client,
            token,
            manifest("1.1.0", ["msg.send", "http.outbound"], [CITY_FIELD, NEW_FIELD]),
            ["msg.send", "http.outbound"],
        )

        listing = client.get(
            "/api/plugins/installations",
            headers={"Authorization": f"Bearer {token}"},
        ).json()
        row = listing["data"]["items"][0]
        assert row["version"] == "1.0.0"
        assert row["has_update"] is True
        assert row["latest_version"] == "1.1.0"
        assert sorted(row["latest_approved_capabilities"]) == ["http.outbound", "msg.send"]
    finally:
        client.__exit__(None, None, None)


def test_upgrade_keeps_config_and_narrows_capabilities(tmp_path: Path) -> None:
    """升级会换版本、保留同名配置、丢弃已删除项，且不自动授予新增能力。"""
    client, token = setup_client(tmp_path)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        upload_and_publish(
            client,
            token,
            manifest("1.0.0", ["msg.send"], [CITY_FIELD, LEGACY_FIELD]),
            ["msg.send"],
        )
        installed = install_and_configure(
            client, token, {"city": "北京", "legacy_city": "旧值"}
        )
        upload_and_publish(
            client,
            token,
            manifest("1.1.0", ["msg.send", "http.outbound"], [CITY_FIELD, NEW_FIELD]),
            ["msg.send", "http.outbound"],
        )

        upgraded = client.post(
            f"/api/plugins/installations/{installed['id']}/upgrade",
            headers=headers,
            json={},
        ).json()
        assert upgraded["success"] is True, upgraded
        data = upgraded["data"]
        assert data["version"] == "1.1.0"
        assert data["from_version"] == "1.0.0"
        assert data["to_version"] == "1.1.0"
        assert data["has_update"] is False
        # 同名配置保留，新版本已移除的键丢弃，新增键取默认值。
        assert data["config"]["city"] == "北京"
        assert data["config"]["unit"] == ""
        assert "legacy_city" not in data["config"]
        assert data["dropped_config"] == ["legacy_city"]
        # 新增能力不会自动生效，需要用户自己勾选。
        assert data["new_capabilities"] == ["http.outbound"]
        assert data["granted_capabilities"] == ["msg.send"]
        assert data["is_enabled"] is True
        assert data["auto_disabled"] is False
    finally:
        client.__exit__(None, None, None)


def test_upgrade_rejects_when_already_latest(tmp_path: Path) -> None:
    """重复升级会被明确拒绝，而不是静默成功。"""
    client, token = setup_client(tmp_path)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        upload_and_publish(
            client, token, manifest("1.0.0", ["msg.send"], [CITY_FIELD]), ["msg.send"]
        )
        installed = install_and_configure(client, token, {"city": "北京"})
        upload_and_publish(
            client, token, manifest("1.1.0", ["msg.send"], [CITY_FIELD]), ["msg.send"]
        )
        first = client.post(
            f"/api/plugins/installations/{installed['id']}/upgrade",
            headers=headers,
            json={},
        ).json()
        assert first["success"] is True, first

        second = client.post(
            f"/api/plugins/installations/{installed['id']}/upgrade",
            headers=headers,
            json={},
        ).json()
        assert second["success"] is False
        assert "无需升级" in second["message"]
    finally:
        client.__exit__(None, None, None)


def test_upgrade_removes_old_version_so_rollback_is_gone(tmp_path: Path) -> None:
    """升级会把被替换的旧版本删掉，因此之后不能再回滚到该版本。"""
    client, token = setup_client(tmp_path)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        upload_and_publish(
            client, token, manifest("1.0.0", ["msg.send"], [CITY_FIELD]), ["msg.send"]
        )
        install_and_configure(client, token, {"city": "上海"})
        upload_and_publish(
            client, token, manifest("1.1.0", ["msg.send"], [CITY_FIELD]), ["msg.send"]
        )
        upgraded = client.post(
            f"/api/plugins/installations/{installed_id(client, token)}/upgrade",
            headers=headers,
            json={},
        ).json()
        assert upgraded["success"] is True, upgraded
        assert upgraded["data"]["purged_version"] == "1.0.0", upgraded["data"]

        rollback = client.post(
            f"/api/plugins/installations/{installed_id(client, token)}/upgrade",
            headers=headers,
            json={"version": "1.0.0"},
        ).json()
        assert rollback["success"] is False, rollback
        assert "没有 1.0.0" in rollback["message"], rollback
    finally:
        client.__exit__(None, None, None)


def test_upgrade_rejects_unknown_version(tmp_path: Path) -> None:
    """指定不存在的版本号时给出中文提示。"""
    client, token = setup_client(tmp_path)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        upload_and_publish(
            client, token, manifest("1.0.0", ["msg.send"], [CITY_FIELD]), ["msg.send"]
        )
        install_and_configure(client, token, {"city": "成都"})
        install_id = installed_id(client, token)
        result = client.post(
            f"/api/plugins/installations/{install_id}/upgrade",
            headers=headers,
            json={"version": "9.9.9"},
        ).json()
        assert result["success"] is False
        assert "没有 9.9.9" in result["message"]
    finally:
        client.__exit__(None, None, None)


def installed_id(client: TestClient, token: str) -> int:
    """取当前安装记录 ID。"""
    listing = client.get(
        "/api/plugins/installations", headers={"Authorization": f"Bearer {token}"}
    ).json()
    return int(listing["data"]["items"][0]["id"])


def test_runner_reloads_new_version_without_duplicate_handlers(tmp_path: Path) -> None:
    """换版本重新加载时，旧版本的处理函数与子模块必须一起卸载。"""
    from app.services import plugin_runner_main

    versions = {}
    for version, value in (("1.0.0", "v1"), ("1.1.0", "v2")):
        root = tmp_path / version
        root.mkdir()
        (root / "helper.py").write_text(f'VALUE = "{value}"\n', encoding="utf-8")
        (root / "plugin.py").write_text(
            "import helper\n"
            "from app.services.plugin_sdk import on_message\n\n\n"
            "@on_message\n"
            "async def handle(event, host):\n"
            "    host.log(helper.VALUE)\n",
            encoding="utf-8",
        )
        versions[version] = root

    plugin_id = "com.example.reload"
    event = {"post_type": "message", "message_type": "group"}
    try:
        plugin_runner_main._load_plugin(plugin_id, str(versions["1.0.0"]), "plugin.py")
        handlers = plugin_sdk.handlers_for(plugin_id, event)
        assert len(handlers) == 1
        assert handlers[0].__globals__["helper"].VALUE == "v1"

        plugin_runner_main._load_plugin(plugin_id, str(versions["1.1.0"]), "plugin.py")
        handlers = plugin_sdk.handlers_for(plugin_id, event)
        # 没有重复注册，且用的是新版本的子模块。
        assert len(handlers) == 1
        assert handlers[0].__globals__["helper"].VALUE == "v2"
    finally:
        plugin_sdk.reset_plugin(plugin_id)
        sys.modules.pop(plugin_runner_main._module_name(plugin_id), None)
        sys.modules.pop("helper", None)


def test_upgrade_purges_replaced_version_and_package_dir(tmp_path: Path) -> None:
    """升级成功后旧版本记录与磁盘包目录一起删除，同版本号还能重新提交。"""
    from test_plugins import submit_package

    client, token = setup_client(tmp_path)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        upload_and_publish(
            client,
            token,
            manifest("1.0.0", ["msg.send"], [CITY_FIELD, LEGACY_FIELD]),
            ["msg.send"],
        )
        installed = install_and_configure(
            client, token, {"city": "北京", "legacy_city": "旧值"}
        )
        upload_and_publish(
            client, token, manifest("1.1.0", ["msg.send"], [CITY_FIELD]), ["msg.send"]
        )

        old_dir = tmp_path / "plugin-data" / "packages" / PLUGIN_ID / "1.0.0"
        assert old_dir.is_dir(), "旧版本包目录应已落盘"

        upgraded = client.post(
            f"/api/plugins/installations/{installed['id']}/upgrade",
            headers=headers,
            json={},
        ).json()
        assert upgraded["success"] is True, upgraded
        data = upgraded["data"]
        assert data["from_version"] == "1.0.0"
        assert data["to_version"] == "1.1.0"
        assert data["purged_version"] == "1.0.0", data
        assert data["purged_package_dir"], data
        assert data["purge_skipped_reason"] == ""
        assert not Path(data["purged_package_dir"]).exists()
        assert not list(old_dir.glob("*")), "旧版本包目录应被清空"

        # 审核台只剩当前版本，旧版本记录已经不在库里了。
        versions = client.get(
            "/api/plugin_admin/versions",
            headers=headers,
            params={"plugin_id": PLUGIN_ID},
        ).json()
        assert [item["version"] for item in versions["data"]["items"]] == ["1.1.0"]

        # 记录被物理删除后，同一个版本号可以重新提交，不会被唯一约束挡住。
        resubmitted = submit_package(
            client, token, build_package(manifest("1.0.0", ["msg.send"], [CITY_FIELD]))
        )
        assert resubmitted["success"] is True, resubmitted
    finally:
        client.__exit__(None, None, None)


def test_upgrade_keeps_version_that_is_still_current(tmp_path: Path) -> None:
    """旧版本还是插件在线版本时不能删，否则新装和回滚都没有落地版本可用。"""
    from test_plugins import submit_package

    client, token = setup_client(tmp_path)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        upload_and_publish(
            client, token, manifest("1.0.0", ["msg.send"], [CITY_FIELD]), ["msg.send"]
        )
        installed = install_and_configure(client, token, {"city": "北京"})
        # 新版本只审核通过、不切换生效版本，线上仍指向 1.0.0。
        submitted = submit_package(
            client, token, build_package(manifest("1.1.0", ["msg.send"], [CITY_FIELD]))
        )
        assert submitted["success"] is True, submitted
        approved = client.post(
            "/api/plugin_admin/versions/approve",
            headers=headers,
            json={
                "plugin_id": PLUGIN_ID,
                "version": "1.1.0",
                "granted_capabilities": ["msg.send"],
                "comment": "只审核不发布",
            },
        ).json()
        assert approved["success"] is True, approved

        upgraded = client.post(
            f"/api/plugins/installations/{installed['id']}/upgrade",
            headers=headers,
            json={"version": "1.1.0"},
        ).json()
        assert upgraded["success"] is True, upgraded
        data = upgraded["data"]
        assert data["purged_version"] == ""
        assert "当前生效版本" in data["purge_skipped_reason"], data
        assert list(
            (tmp_path / "plugin-data" / "packages" / PLUGIN_ID / "1.0.0").glob("*")
        ), "在线版本的包目录必须保留"

        # 在线版本没被删，回滚入口照常可用；回滚后刚换掉的 1.1.0 反过来满足清理条件。
        rollback = client.post(
            f"/api/plugins/installations/{installed['id']}/upgrade",
            headers=headers,
            json={"version": "1.0.0"},
        ).json()
        assert rollback["success"] is True, rollback
        assert rollback["data"]["version"] == "1.0.0"
        assert rollback["data"]["purged_version"] == "1.1.0", rollback["data"]
    finally:
        client.__exit__(None, None, None)
