"""运行期能力闸门的出网代理测试。

覆盖两个修复点：
1. 插件传入畸形 timeout / headers 时不再把异常抛回调用方；
2. 响应体在读取时就限制体积，而不是先整个解码进内存再截断。
"""
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.services.plugin_action_log_service import PluginActionLogService
from app.services.plugin_gate_runtime import (
    MAX_RESPONSE_BYTES,
    RuntimeGate,
    _as_dict,
    _read_limited,
    _request_timeout,
)
from test_plugins import build_plugin_settings

OUTBOUND = "http.outbound"


class _FakeSession:
    """审计写入的假会话，提交即成功。"""

    async def commit(self) -> None:
        return None


class _FakeSessionFactory:
    """返回假会话的异步上下文管理器工厂。"""

    def __call__(self) -> "_FakeSessionFactory":
        return self

    async def __aenter__(self) -> _FakeSession:
        return _FakeSession()

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _FakeDatabase:
    """只提供 session_factory 的假数据库，避免测试碰真实库。"""

    def __init__(self) -> None:
        self.session_factory = _FakeSessionFactory()


async def _never_called(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """占位的 OneBot 调用入口，出网测试用不到。"""
    raise AssertionError("出网测试不应触发 OneBot 动作调用")


def make_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RuntimeGate:
    """构造只用于单元调用的闸门实例，审计写入被替换成空操作。"""

    async def no_audit(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(PluginActionLogService, "append", no_audit)
    return RuntimeGate(build_plugin_settings(tmp_path), _FakeDatabase(), _never_called)


def installation() -> dict[str, Any]:
    """带出网能力与白名单域名的安装信息。"""
    return {
        "plugin_id": "com.example.gate",
        "bot_qq": "10001",
        "granted_capabilities": [OUTBOUND],
        "allowed_domains": ["api.example.com"],
    }


def _run(coro: Any) -> Any:
    """在同步测试里执行协程。"""
    return asyncio.run(coro)


def _client(handler: Any) -> httpx.AsyncClient:
    """用 MockTransport 造出网客户端，避免测试访问真实网络。"""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------- 参数解析


def test_request_timeout_falls_back_on_any_malformed_value() -> None:
    """畸形 timeout 一律按默认值，绝不能抛异常（修复点 M1）。"""
    for value in (None, "", "abc", "10s", [], {}, float("nan"), -5, 0):
        assert _request_timeout(value) == 10.0, value
    assert _request_timeout("25") == 25.0
    assert _request_timeout(99) == 30.0, "超过上限必须封顶"


def test_as_dict_rejects_non_mapping() -> None:
    """headers/query 不是字典时按空处理，避免 dict() 抛 TypeError。"""
    assert _as_dict({"a": 1}) == {"a": 1}
    for value in (None, [], "text", 3):
        assert _as_dict(value) == {}, value


# ---------------------------------------------------------------- 出网代理


def test_http_accepts_malformed_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """timeout 传字符串时请求照常完成，而不是把 ValueError 抛回调用方。"""
    gate = make_gate(tmp_path, monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.example.com"
        return httpx.Response(200, json={"ok": True})

    gate._http_client = _client(handler)
    ok, data, error = _run(
        gate._handle_http(
            installation(), {"url": "https://api.example.com/x", "timeout": "abc"}, ""
        )
    )
    assert ok is True, error
    assert data["status"] == 200
    assert data["json"] == {"ok": True}


def test_http_rejects_non_mapping_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """headers/query 传成非字典也不能让动作请求挂掉。"""
    gate = make_gate(tmp_path, monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="hi")

    gate._http_client = _client(handler)
    ok, data, error = _run(
        gate._handle_http(
            installation(),
            {"url": "https://api.example.com/x", "headers": ["not", "a", "dict"], "query": 5},
            "",
        )
    )
    assert ok is True, error
    assert data["text"] == "hi"


def test_http_caps_downloaded_body_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """超大响应体必须在读取阶段就断掉，而不是整个解码进内存（修复点 M2）。"""
    gate = make_gate(tmp_path, monkeypatch)
    # 把上限压到 100 字节，避免为了测试真造 2MB 数据。
    monkeypatch.setattr("app.services.plugin_gate_runtime.MAX_RESPONSE_BYTES", 100)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="x" * 5000)

    gate._http_client = _client(handler)
    ok, data, error = _run(
        gate._handle_http(installation(), {"url": "https://api.example.com/big"}, "")
    )
    assert ok is True, error
    # 读满上限即中断，不会把 5000 字节全部收进来。
    assert len(data["text"]) == 100


def test_http_parses_json_from_full_body_not_truncated_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """JSON 要从完整响应体解析，避免大响应被 max_chars 截断后解析失败。"""
    body = json.dumps({"items": ["a" * 50] * 40}).encode()
    gate = make_gate(tmp_path, monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    gate._http_client = _client(handler)
    ok, data, error = _run(
        gate._handle_http(
            installation(), {"url": "https://api.example.com/j", "max_chars": 2000}, ""
        )
    )
    assert ok is True, error
    assert len(data["text"]) == 2000, "返回文本按 max_chars 截断"
    assert data["json"]["items"][0] == "a" * 50, "JSON 仍从完整响应体解析"


def test_http_still_enforces_domain_whitelist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """白名单外的域名依旧拒绝，不能因为改了流式读取就放松。"""
    gate = make_gate(tmp_path, monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("白名单外的请求不应发出")

    gate._http_client = _client(handler)
    ok, _data, error = _run(
        gate._handle_http(installation(), {"url": "https://evil.example.com/x"}, "")
    )
    assert ok is False
    assert "不在白名单" in error


def test_http_requires_outbound_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未批准出网能力时直接拒绝，连请求都不发。"""
    gate = make_gate(tmp_path, monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("未授权时不应发出请求")

    gate._http_client = _client(handler)
    install = installation()
    install["granted_capabilities"] = []
    ok, _data, error = _run(
        gate._handle_http(install, {"url": "https://api.example.com/x"}, "")
    )
    assert ok is False
    assert "未获得访问外网能力" in error


# ---------------------------------------------------------------- 流式读取


def test_read_limited_stops_at_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """超过上限立即中断，不等整个响应体读完。"""
    monkeypatch.setattr("app.services.plugin_gate_runtime.MAX_RESPONSE_BYTES", 100)

    class _Stream:
        def __init__(self, chunks: list[bytes]) -> None:
            self._chunks = chunks

        async def aiter_bytes(self):
            for chunk in self._chunks:
                yield chunk

    # 第一块 60 字节收下，第二块跨过上限只保留前 40 字节，随后中断。
    body = _run(_read_limited(_Stream([b"a" * 60, b"b" * 60, b"c" * 60])))
    assert body == b"a" * 60 + b"b" * 40
    assert len(body) == 100


def test_gate_construction_smoke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """确认闸门可以脱离真实数据库构造，供上面的出网用例使用。"""
    gate = make_gate(tmp_path, monkeypatch)
    assert gate.settings.plugin_enabled is True
    assert gate._http_client is None
