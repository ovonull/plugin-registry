"""机器人自动同意加群申请测试。"""

import asyncio

from app.services.onebot_service import OneBotService


class _Session:
    """返回固定开关状态的异步数据库会话替身。"""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def scalar(self, _statement):
        return self.enabled


class _Database:
    """提供异步会话工厂的测试数据库替身。"""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def session_factory(self) -> _Session:
        return _Session(self.enabled)


def _service(enabled: bool) -> tuple[OneBotService, list[tuple[str, dict]]]:
    service = object.__new__(OneBotService)
    service.database = _Database(enabled)
    calls: list[tuple[str, dict]] = []

    async def call_action(action: str, params: dict, **_kwargs) -> dict:
        calls.append((action, params))
        return {"status": "ok", "retcode": 0}

    service.call_action = call_action
    return service, calls


def _event() -> dict:
    return {
        "self_id": "123456",
        "post_type": "request",
        "request_type": "group",
        "sub_type": "add",
        "group_id": "9988",
        "user_id": "778899",
        "flag": "request-flag",
    }


def test_group_request_auto_approve_calls_onebot_action() -> None:
    """开启开关时调用 set_group_add_request 并传递完整参数。"""
    service, calls = _service(True)
    asyncio.run(service._handle_group_request(_event()))

    assert calls == [
        (
            "set_group_add_request",
            {
                "flag": "request-flag",
                "sub_type": "add",
                "approve": True,
                "reason": "",
            },
        )
    ]


def test_group_request_auto_approve_disabled_skips_action() -> None:
    """关闭开关时不调用任何同意动作。"""
    service, calls = _service(False)
    asyncio.run(service._handle_group_request(_event()))

    assert calls == []


def test_group_request_params_reject_invalid_sub_type() -> None:
    """缺少 flag 或不支持的请求类型不能调用同意动作。"""
    assert OneBotService._group_request_params({"flag": "x", "sub_type": "unknown"}) is None
    assert OneBotService._group_request_params({"sub_type": "add"}) is None
