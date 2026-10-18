"""插件市场「自动探测仓库最新版本」测试。

覆盖：
1. 探测顺序：最新 release → 最新 tag → 默认分支最新 commit；
2. tag 归一化为不可变引用（版本号 tag 直接用，其他写法转提交哈希）；
3. 提交时留空 source_ref 即自动探测；
4. 已登记插件的「检查更新」对比结论。

说明：GitHub 接口与压缩包下载都用假 httpx.AsyncClient 按 URL 分流，不联网。
"""

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.common.responses import BusinessError
from app.services.plugin_source_service import PluginSourceService, RepoLatest
from test_plugins import VALID_MANIFEST, build_package, build_plugin_settings
from test_service import login

REPO_URL = "https://github.com/owner/repo"
API_BASE = "https://api.github.com/repos/owner/repo"


def _api(path: str = "") -> str:
    """拼出假 GitHub 接口地址。"""
    return API_BASE + path


def make_source(tmp_path: Path) -> PluginSourceService:
    """构造只用于单元调用的来源服务实例。"""
    from test_plugins import build_plugin_settings

    return PluginSourceService(build_plugin_settings(tmp_path))


def install_fake_github(
    monkeypatch: pytest.MonkeyPatch,
    *,
    release: dict | None = None,
    tags: list | None = None,
    commits: list | None = None,
    commit_shas: dict[str, str] | None = None,
    package: bytes | None = None,
) -> list[str]:
    """
    把假 GitHub 探测结果注入来源服务，不联网、不碰 httpx。

    只替换 ``_github_json`` 与 ``download_repo_archive`` 两个方法，
    避免影响应用自身的 HTTP 客户端。返回记录到的请求地址列表。
    """
    requested: list[str] = []
    package = package or build_package(VALID_MANIFEST)

    async def fake_json(self: Any, url: str) -> Any:
        requested.append(url)
        if url == _api("/releases/latest"):
            return release
        if url.startswith(_api("/tags")):
            return tags
        if url.startswith(_api("/commits")):
            for tag, sha in (commit_shas or {}).items():
                if url.endswith(quote(tag, safe="")):
                    return {"sha": sha}
            return commits
        return None

    async def fake_download(self: Any, repo_url: str, source_ref: str) -> bytes:
        requested.append(f"archive:{source_ref}")
        return package

    monkeypatch.setattr(PluginSourceService, "_github_json", fake_json)
    monkeypatch.setattr(PluginSourceService, "download_repo_archive", fake_download)
    return requested


def manifest_with(version: str, plugin_id: str = "com.example.repo") -> dict:
    """复制一份清单并改掉版本号，避免用例之间互相污染。"""
    manifest = json.loads(json.dumps(VALID_MANIFEST))
    manifest["id"] = plugin_id
    manifest["version"] = version
    manifest["name"] = f"仓库插件 {version}"
    return manifest


def repo_package(version: str) -> bytes:
    """打包一个指定版本的插件压缩包。"""
    return build_package(manifest_with(version))


# ---------------------------------------------------------------- 探测顺序


def test_probe_prefers_latest_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """有 release 时优先取 release 的 tag，不再往下找 tag 列表。"""
    requested = install_fake_github(
        monkeypatch,
        release={"tag_name": "v2.0.0", "published_at": "2026-10-01T00:00:00Z"},
        tags=[{"name": "v9.9.9"}],
    )
    latest = _run(make_source(tmp_path).resolve_latest_ref(REPO_URL))
    assert latest.kind == "release"
    assert latest.tag == "v2.0.0"
    assert latest.ref == "v2.0.0"
    assert latest.published_at == "2026-10-01T00:00:00Z"
    # 已经拿到 release，就不必再查 tag 列表。
    assert not any(url.startswith(_api("/tags")) for url in requested)


def test_probe_falls_back_to_latest_tag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """没有 release 时取最新 tag，接口返回顺序即新旧顺序。"""
    install_fake_github(
        monkeypatch, tags=[{"name": "1.4.2"}, {"name": "1.4.1"}]
    )
    latest = _run(make_source(tmp_path).resolve_latest_ref(REPO_URL))
    assert latest.kind == "tag"
    assert latest.tag == "1.4.2"
    assert latest.ref == "1.4.2"


def test_probe_falls_back_to_default_branch_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一个 tag 都没打时退到默认分支最新提交，用提交哈希保证不可变。"""
    sha = "a" * 40
    install_fake_github(monkeypatch, commits=[{"sha": sha}])
    latest = _run(make_source(tmp_path).resolve_latest_ref(REPO_URL))
    assert latest.kind == "commit"
    assert latest.tag == ""
    assert latest.ref == sha


def test_probe_normalizes_tag_with_slash_to_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """release/1.0 这类带斜杠的 tag 会转成提交哈希，避免破坏归档地址。"""
    sha = "b" * 40
    install_fake_github(
        monkeypatch, tags=[{"name": "release/1.0"}], commit_shas={"release/1.0": sha}
    )
    latest = _run(make_source(tmp_path).resolve_latest_ref(REPO_URL))
    assert latest.tag == "release/1.0"
    assert latest.ref == sha


def test_probe_rejects_empty_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """仓库既无 release、tag 也无提交时给出可操作的提示。"""
    install_fake_github(monkeypatch)
    with pytest.raises(BusinessError) as error:
        _run(make_source(tmp_path).resolve_latest_ref(REPO_URL))
    assert "探测" in str(error.value)


def test_probe_rejects_non_github_url(tmp_path: Path) -> None:
    """只支持 GitHub 仓库地址，其他平台直接拒绝而不是猜。"""
    with pytest.raises(BusinessError) as error:
        _run(make_source(tmp_path).resolve_latest_ref("https://gitlab.com/owner/repo"))
    assert error.value.code == 40094


# ---------------------------------------------------------------- 探测结果


def test_make_source_isolates_plugin_data_dir(tmp_path: Path) -> None:

    source = make_source(tmp_path)
    assert str(tmp_path) in str(source.settings.plugin_data_dir)


def test_probe_latest_reads_manifest_without_persisting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """探测结果要带上清单里的标识与版本号，且不往磁盘写任何东西。"""
    install_fake_github(
        monkeypatch,
        release={"tag_name": "v3.1.0", "published_at": "2026-09-09T00:00:00Z"},
        package=repo_package("3.1.0"),
    )
    latest, manifest = _run(make_source(tmp_path).fetch_repo_latest(REPO_URL))
    assert latest.ref == "v3.1.0"
    assert manifest["id"] == "com.example.repo"
    assert manifest["version"] == "3.1.0"
    # 探测过程只读元数据与清单，不创建任何插件包目录。
    assert not (tmp_path / "plugin-data").exists()


# ---------------------------------------------------------------- 提交接口


def submit_repo(client: TestClient, token: str, **payload: Any) -> dict:
    """走仓库提交接口。"""
    body = {"repo_url": REPO_URL}
    body.update(payload)
    return client.post(
        "/api/plugins/repo",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    ).json()


def probe_repo(client: TestClient, token: str, **payload: Any) -> dict:
    """走仓库探测接口。"""
    body = {"repo_url": REPO_URL}
    body.update(payload)
    return client.post(
        "/api/plugins/repo/probe",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    ).json()


def test_submit_repo_without_ref_auto_detects_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """留空版本引用也能提交，版本号取自仓库探测结果。"""
    install_fake_github(
        monkeypatch,
        release={"tag_name": "v2.5.0", "published_at": "2026-10-02T00:00:00Z"},
        package=repo_package("2.5.0"),
    )
    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        token = login(client, "admin", "admin-password")
        result = submit_repo(client, token)
        assert result["success"] is True, result
        version = result["data"]["version"]
        assert version["version"] == "2.5.0"
        # 引用必须是探测到的 tag，而不是分支名。
        assert version["source_ref"] == "v2.5.0"
        assert result["data"]["plugin"]["source_type"] == "repo"
        assert result["data"]["plugin"]["repo_url"] == REPO_URL


def test_submit_repo_without_ref_hints_when_version_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重复提交同一版本时提示去仓库打新 tag，而不是让作者盲目改清单。"""
    install_fake_github(
        monkeypatch,
        release={"tag_name": "v2.5.0", "published_at": "2026-10-02T00:00:00Z"},
        package=repo_package("2.5.0"),
    )
    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        token = login(client, "admin", "admin-password")
        assert submit_repo(client, token)["success"] is True
        again = submit_repo(client, token)
        assert again["success"] is False
        assert again["code"] == 40097
        assert "打新的 tag" in again["message"]


def test_submit_repo_with_explicit_ref_still_rejects_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """显式填分支名依然被拒绝，不会因为支持自动探测就放开校验。"""
    install_fake_github(monkeypatch, package=repo_package("1.0.0"))
    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        token = login(client, "admin", "admin-password")
        result = submit_repo(client, token, source_ref="main")
        assert result["success"] is False
        assert result["code"] == 40094


def test_probe_endpoint_requires_upload_permission(tmp_path: Path) -> None:
    """探测接口同样要求上传资格，避免被当成免费的 GitHub 代理使用。"""
    from test_service import register_user

    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        register_user(
            client, "probe_user", "probe-pass1", "probe@example.com", "探测用户"
        )
        token = login(client, "probe_user", "probe-pass1")
        result = probe_repo(client, token)
        assert result["success"] is False
        assert result["code"] == 40381


def test_probe_endpoint_returns_detected_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上传前的探测接口返回将要提交的版本与引用来源。"""
    install_fake_github(
        monkeypatch,
        release={"tag_name": "v4.0.1", "published_at": "2026-10-05T00:00:00Z"},
        package=repo_package("4.0.1"),
    )
    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        token = login(client, "admin", "admin-password")
        result = probe_repo(client, token)
        assert result["success"] is True, result
        assert result["data"]["version"] == "4.0.1"
        assert result["data"]["plugin_id"] == "com.example.repo"
        assert result["data"]["kind"] == "release"


# ---------------------------------------------------------------- 检查更新


def check_update(client: TestClient, token: str, plugin_id: str) -> dict:
    """调用检查更新接口。"""
    return client.post(
        f"/api/plugins/{plugin_id}/check-update",
        headers={"Authorization": f"Bearer {token}"},
    ).json()


def approve_and_publish(client: TestClient, token: str, version: str) -> None:
    """审核通过并上架指定版本，让插件进入生效状态。"""
    headers = {"Authorization": f"Bearer {token}"}
    approved = client.post(
        "/api/plugin_admin/versions/approve",
        headers=headers,
        json={
            "plugin_id": "com.example.repo",
            "version": version,
            "granted_capabilities": ["msg.send", "http.outbound"],
            "comment": "测试审核",
        },
    ).json()
    assert approved["success"] is True, approved
    published = client.post(
        "/api/plugin_admin/publish",
        headers=headers,
        json={"plugin_id": "com.example.repo", "version": version},
    ).json()
    assert published["success"] is True, published


def register_repo_plugin(
    client: TestClient,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
    version: str,
    tag: str,
) -> None:
    """按仓库提交一个插件版本，模拟作者已经上传过 Git 插件。"""
    install_fake_github(
        monkeypatch,
        release={"tag_name": tag, "published_at": "2026-10-01T00:00:00Z"},
        package=repo_package(version),
    )
    assert submit_repo(client, token)["success"] is True


def test_check_update_reports_new_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """仓库里出现新 tag 时检查更新报告有新版本，并带上版本号。"""
    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        token = login(client, "admin", "admin-password")
        register_repo_plugin(client, token, monkeypatch, "1.0.0", "v1.0.0")
        approve_and_publish(client, token, "1.0.0")
        # 仓库里已经推进到 1.1.0。
        install_fake_github(
            monkeypatch,
            release={"tag_name": "v1.1.0", "published_at": "2026-10-06T00:00:00Z"},
            package=repo_package("1.1.0"),
        )
        result = check_update(client, token, "com.example.repo")
        assert result["success"] is True, result
        assert result["data"]["has_new_version"] is True
        assert result["data"]["version"] == "1.1.0"
        assert result["data"]["current_version"] == "1.0.0"
        assert "新版本" in result["message"]


def test_check_update_reports_no_new_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """仓库还停在已登记版本时明确告诉用户没有新版本。"""
    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        token = login(client, "admin", "admin-password")
        register_repo_plugin(client, token, monkeypatch, "1.0.0", "v1.0.0")
        approve_and_publish(client, token, "1.0.0")
        install_fake_github(
            monkeypatch,
            release={"tag_name": "v1.0.0", "published_at": "2026-10-01T00:00:00Z"},
            package=repo_package("1.0.0"),
        )
        result = check_update(client, token, "com.example.repo")
        assert result["data"]["has_new_version"] is False
        assert "暂未有新版本" in result["message"]


def test_check_update_rejects_package_source(tmp_path: Path) -> None:
    """压缩包来源没有关联仓库，检查更新直接说明原因。"""
    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        token = login(client, "admin", "admin-password")
        upload = client.post(
            "/api/plugins/package",
            content=build_package(VALID_MANIFEST),
            headers={
                "Authorization": f"Bearer {token}",
                "content-type": "application/zip",
            },
        ).json()
        assert upload["success"] is True
        result = check_update(client, token, VALID_MANIFEST["id"])
        assert result["success"] is False
        assert result["code"] == 40099
        assert "压缩包来源" in result["message"]


def test_check_update_reports_current_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """已上架插件要回报当前生效版本，方便前端展示「1.0.0 → 1.1.0」。"""
    with TestClient(create_app(build_plugin_settings(tmp_path))) as client:
        token = login(client, "admin", "admin-password")
        register_repo_plugin(client, token, monkeypatch, "1.0.0", "v1.0.0")
        approve_and_publish(client, token, "1.0.0")
        install_fake_github(
            monkeypatch,
            release={"tag_name": "v1.1.0", "published_at": "2026-10-06T00:00:00Z"},
            package=repo_package("1.1.0"),
        )
        result = check_update(client, token, "com.example.repo")
        assert result["data"]["current_version"] == "1.0.0"


def _run(coro: Any) -> Any:
    """在同步测试里执行协程。"""
    import asyncio

    return asyncio.run(coro)
