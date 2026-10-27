"""插件清单校验、来源归一化与注册表接口测试。"""

import io
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.common.config import Settings
from app.common.responses import BusinessError
from app.services.plugin_gate import resolve_granted_capabilities
from app.services.plugin_manifest import normalize_manifest
from app.services.plugin_source_service import PluginSourceService
from test_service import build_settings, login, register_user


VALID_MANIFEST: dict = {
    "manifest_version": 1,
    "id": "com.example.weather",
    "name": "天气查询",
    "version": "1.0.0",
    "description": "查询城市天气并发送到群聊",
    "author": {"name": "测试作者", "url": "https://example.com"},
    "license": "MIT",
    "homepage": "https://example.com/weather",
    "entry": "plugin.py",
    "icon": "icon.png",
    "capabilities": ["msg.send", "http.outbound"],
    "allowed_domains": ["api.weather.com"],
    "min_host_version": "1.0.0",
    "max_host_version": "2.0.0",
    "dependencies": ["httpx>=0.27"],
    "config_schema": [
        {
            "key": "city",
            "label": "默认城市",
            "type": "text",
            "required": True,
            "sensitive": False,
        }
    ],
}


def build_plugin_settings(tmp_path: Path, enabled: bool = True) -> Settings:
    """构造开启插件功能并隔离插件数据目录的测试配置。"""
    return replace(
        build_settings(tmp_path),
        plugin_enabled=enabled,
        plugin_data_dir=str(tmp_path / "plugin-data"),
    )


def build_package(manifest: dict, files: dict[str, str] | None = None) -> bytes:
    """把清单与代码打包成内存 zip，模拟用户上传的插件包。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        for name, content in (files or {"plugin.py": "print('hello')"}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def submit_package(client: TestClient, token: str, content: bytes) -> dict:
    """以原始字节上传插件压缩包。"""
    return client.post(
        "/api/plugins/package",
        content=content,
        headers={
            "Authorization": f"Bearer {token}",
            "content-type": "application/zip",
        },
    ).json()


def test_normalize_manifest_fills_all_fields() -> None:
    """合法清单通过校验并补齐全部字段。"""
    normalized = normalize_manifest(VALID_MANIFEST)
    assert normalized["id"] == "com.example.weather"
    assert normalized["version"] == "1.0.0"
    assert normalized["capabilities"] == ["msg.send", "http.outbound"]
    assert normalized["allowed_domains"] == ["api.weather.com"]
    assert normalized["author"] == {"name": "测试作者", "url": "https://example.com"}
    assert normalized["config_schema"][0]["key"] == "city"
    assert set(normalized) == {
        "manifest_version",
        "id",
        "name",
        "version",
        "description",
        "author",
        "license",
        "homepage",
        "entry",
        "icon",
        "capabilities",
        "allowed_domains",
        "min_host_version",
        "max_host_version",
        "dependencies",
        "config_schema",
    }


def test_normalize_manifest_keeps_unknown_field() -> None:
    """放开字段白名单后，未知字段不再拒收，而是原样保留。"""
    payload = dict(VALID_MANIFEST, http_endpoints=[{"path": "/hook"}])
    normalized = normalize_manifest(payload)
    assert "http_endpoints" in normalized
    assert normalized["http_endpoints"] == [{"path": "/hook"}]


def test_normalize_manifest_rejects_bad_version_and_plugin_id() -> None:
    """版本号必须三段式，插件标识必须含点或连字符。"""
    with pytest.raises(BusinessError) as version_error:
        normalize_manifest(dict(VALID_MANIFEST, version="1.0"))
    assert version_error.value.code == 40093

    with pytest.raises(BusinessError) as id_error:
        normalize_manifest(dict(VALID_MANIFEST, id="weather"))
    assert id_error.value.code == 40090
    assert "点或连字符" in id_error.value.message


def test_normalize_manifest_rejects_unknown_capability() -> None:
    """能力必须落在白名单内，词表外一个取值就拒收。"""
    payload = dict(VALID_MANIFEST, capabilities=["msg.send", "db.read"])
    with pytest.raises(BusinessError) as error:
        normalize_manifest(payload)
    assert error.value.code == 40091
    assert "db.read" in error.value.message


def test_outbound_capability_requires_domain_whitelist() -> None:
    """声明访问外网能力时必须填写域名白名单，反向也不能多填。"""
    without_domains = dict(VALID_MANIFEST, allowed_domains=[])
    with pytest.raises(BusinessError) as missing:
        normalize_manifest(without_domains)
    assert missing.value.code == 40092

    with pytest.raises(BusinessError) as redundant:
        normalize_manifest(
            dict(
                VALID_MANIFEST,
                capabilities=["msg.send"],
                allowed_domains=["api.weather.com"],
            )
        )
    assert redundant.value.code == 40092
    assert "未声明访问外网能力" in redundant.value.message

    with pytest.raises(BusinessError) as bad_domain:
        normalize_manifest(dict(VALID_MANIFEST, allowed_domains=["http://api.weather.com/x"]))
    assert bad_domain.value.code == 40092


def test_granted_capabilities_exclude_high_risk_by_default() -> None:
    """高风险能力默认不批准，只有管理员显式勾选才生效。"""
    declared = ["msg.send", "group.kick", "http.outbound"]
    assert resolve_granted_capabilities(declared, None) == ["msg.send"]
    assert resolve_granted_capabilities(declared, ["msg.send", "group.kick"]) == [
        "msg.send",
        "group.kick",
    ]
    with pytest.raises(BusinessError) as error:
        resolve_granted_capabilities(declared, ["group.mute"])
    assert error.value.code == 40091


def test_zip_slip_and_missing_manifest_are_rejected(tmp_path: Path) -> None:
    """压缩包目录穿越、缺少清单、缺少入口文件都必须被拒绝。"""
    service = PluginSourceService(build_plugin_settings(tmp_path))

    traversal = io.BytesIO()
    with zipfile.ZipFile(traversal, "w") as archive:
        archive.writestr("../evil.py", "print('evil')")
        archive.writestr("manifest.json", json.dumps(VALID_MANIFEST, ensure_ascii=False))
    with pytest.raises(BusinessError) as slip:
        service.prepare_package(traversal.getvalue())
    assert slip.value.code == 40094
    assert "非法路径" in slip.value.message

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("plugin.py", "print('hello')")
    with pytest.raises(BusinessError) as no_manifest:
        service.prepare_package(buffer.getvalue())
    assert no_manifest.value.code == 40094
    assert "manifest.json" in no_manifest.value.message

    with pytest.raises(BusinessError) as no_entry:
        service.prepare_package(build_package(VALID_MANIFEST, {"other.py": "pass"}))
    assert no_entry.value.code == 40094
    assert "入口文件" in no_entry.value.message


def test_plugin_feature_switch_defaults_on_and_can_be_disabled(tmp_path: Path) -> None:
    """插件功能默认开启，显式关闭时接口直接给出中文提示。"""
    assert Settings().plugin_enabled is True

    on_dir = tmp_path / "on"
    on_dir.mkdir(exist_ok=True)
    with TestClient(create_app(build_settings(on_dir))) as client:
        token = login(client, "admin", "admin-password")
        response = client.get(
            "/api/plugins", headers={"Authorization": f"Bearer {token}"}
        ).json()
        assert response["success"] is True
        assert response["data"]["total"] == 0

    off_dir = tmp_path / "off"
    off_dir.mkdir(exist_ok=True)
    off_settings = replace(build_settings(off_dir), plugin_enabled=False)
    with TestClient(create_app(off_settings)) as client:
        token = login(client, "admin", "admin-password")
        response = client.get(
            "/api/plugins", headers={"Authorization": f"Bearer {token}"}
        ).json()
        assert response["success"] is False
        assert response["code"] == 40380
        assert "PLUGIN_ENABLED" in response["message"]


def test_submit_package_and_query_registry(tmp_path: Path) -> None:
    """上传插件包后登记为待审核，列表、详情与能力目录均可查询。"""
    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        token = login(client, "admin", "admin-password")
        headers = {"Authorization": f"Bearer {token}"}
        submitted = submit_package(client, token, build_package(VALID_MANIFEST))
        assert submitted["success"] is True
        assert submitted["data"]["version"]["review_status"] == "pending"
        assert submitted["data"]["version"]["granted_capabilities"] == []
        assert submitted["data"]["version"]["capabilities"] == [
            "msg.send",
            "http.outbound",
        ]
        assert submitted["data"]["plugin"]["is_published"] is False

        listed = client.get("/api/plugins", headers=headers).json()
        assert listed["success"] is True
        assert listed["data"]["total"] == 1
        assert listed["data"]["items"][0]["plugin_id"] == "com.example.weather"

        detail = client.get("/api/plugins/com.example.weather", headers=headers).json()
        assert detail["success"] is True
        assert len(detail["data"]["versions"]) == 1
        assert detail["data"]["versions"][0]["entry"] == "plugin.py"
        assert detail["data"]["plugin"]["current_version"] is None

        catalog = client.get("/api/plugins/capabilities", headers=headers).json()
        assert catalog["success"] is True
        codes = {item["code"]: item for item in catalog["data"]}
        assert len(codes) == 16
        assert codes["group.kick"]["risk_level"] == "high"
        assert codes["msg.send"]["risk_level"] == "medium"

        dictionaries = client.get(
            "/api/dictionaries?types=plugin_capability,plugin_review_status"
        ).json()
        capability_items = dictionaries["data"]["plugin_capability"]
        assert len(capability_items) == 16
        assert {"value": "group.kick", "label": "踢出群成员"} in capability_items
        status_items = dictionaries["data"]["plugin_review_status"]
        assert {"value": "pending", "label": "待审核"} in status_items

        again = submit_package(client, token, build_package(VALID_MANIFEST))
        assert again["success"] is False
        assert again["code"] == 40097
        assert "已提交过" in again["message"]


def test_repo_source_requires_tag_or_commit(tmp_path: Path) -> None:
    """仓库来源必须使用 tag 或 commit，分支名与非法地址都被拒绝。"""
    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        token = login(client, "admin", "admin-password")
        headers = {"Authorization": f"Bearer {token}"}

        branch = client.post(
            "/api/plugins/repo",
            headers=headers,
            json={"repo_url": "https://github.com/owner/repo", "source_ref": "main"},
        ).json()
        assert branch["success"] is False
        assert branch["code"] == 40094
        assert "不能填写分支名" in branch["message"]

        bad_url = client.post(
            "/api/plugins/repo",
            headers=headers,
            json={"repo_url": "https://gitlab.com/owner/repo", "source_ref": "v1.0.0"},
        ).json()
        assert bad_url["success"] is False
        assert bad_url["code"] == 40094
        assert "github.com" in bad_url["message"]


def test_untrusted_user_cannot_submit_package(tmp_path: Path) -> None:
    """普通用户不在信任名单内时不能上传代码插件。"""
    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        register_user(
            client, "plugin_user", "plugin-pass1", "plugin@example.com", "插件用户"
        )
        token = login(client, "plugin_user", "plugin-pass1")
        denied = submit_package(client, token, build_package(VALID_MANIFEST))
        assert denied["success"] is False
        assert denied["code"] == 40381
        assert "信任名单" in denied["message"]
