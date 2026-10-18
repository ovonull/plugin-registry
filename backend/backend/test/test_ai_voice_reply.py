"""AI 回复随机语音发送与文本回退测试。"""

import asyncio
from types import SimpleNamespace

from app.services.onebot_service import OneBotService


class _ReverseManager:
    """记录反向 WebSocket 动作调用的测试替身。"""

    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    async def call(self, action: str, params: dict, **_kwargs) -> dict:
        self.calls.append((action, params))
        return self.responses.pop(0) if self.responses else {"status": "ok"}


def _service(responses: list[dict]) -> tuple[OneBotService, _ReverseManager]:
    service = object.__new__(OneBotService)
    manager = _ReverseManager(responses)
    service.reverse_manager = manager

    async def call_action(action: str, params: dict, **_kwargs) -> dict:
        return await manager.call(action, params)

    service.call_action = call_action
    return service, manager


def test_random_voice_reply_uses_group_ai_record(monkeypatch) -> None:
    """命中概率时群聊 AI 回复使用 send_group_ai_record。"""
    service, manager = _service(
        [
            {
                "status": "ok",
                "data": [
                    {
                        "characters": [
                            {"character_id": "lucy-voice-f38"},
                            {"character_id": "lucy-voice-houge"},
                        ]
                    }
                ],
            },
            {"status": "ok", "retcode": 0},
        ]
    )
    monkeypatch.setattr("app.services.onebot_service.random.random", lambda: 0.1)

    async def run() -> tuple[str, dict, bool]:
        return await service.send_ai_reply(
            {"message_type": "group", "group_id": "100"},
            "你好[CQ:face,id=1]",
            "123456",
            config={
                "voice_reply_enabled": True,
                "voice_reply_probability": 20,
                "voice_character": "lucy-voice-f38",
            },
        )

    action, response, used_voice = asyncio.run(run())
    assert action == "send_group_ai_record"
    assert response["status"] == "ok"
    assert used_voice is True
    assert manager.calls == [
        ("get_ai_characters", {"group_id": "100"}),
        (
            "send_group_ai_record",
            {
                "character": "lucy-voice-f38",
                "group_id": "100",
                "text": "你好",
            },
        ),
    ]


def test_random_voice_reply_miss_sends_text(monkeypatch) -> None:
    """未命中概率时群聊 AI 回复继续发送文本。"""
    service, manager = _service([{"status": "ok", "retcode": 0}])
    monkeypatch.setattr("app.services.onebot_service.random.random", lambda: 0.9)

    async def run() -> tuple[str, dict, bool]:
        return await service.send_ai_reply(
            {"message_type": "group", "group_id": "100"},
            "你好",
            "123456",
            config={"voice_reply_enabled": True, "voice_reply_probability": 20},
        )

    action, response, used_voice = asyncio.run(run())
    assert action == "send_group_msg"
    assert response["status"] == "ok"
    assert used_voice is False
    assert manager.calls[0] == (
        "send_group_msg",
        {"group_id": "100", "message": "你好"},
    )


def test_voice_reply_failure_falls_back_to_text(monkeypatch) -> None:
    """语音动作失败时自动补发文本消息。"""
    service, manager = _service(
        [
            {
                "status": "ok",
                "data": [
                    {
                        "characters": [
                            {"character_id": "lucy-voice-laibixiaoxin"},
                        ]
                    }
                ],
            },
            {"status": "failed", "retcode": 1200, "message": "voice unavailable"},
            {
                "status": "ok",
                "data": [
                    {
                        "characters": [
                            {"character_id": "lucy-voice-houge"},
                        ]
                    }
                ],
            },
            {"status": "failed", "retcode": 1200, "message": "voice unavailable"},
            {"status": "ok", "retcode": 0},
        ]
    )
    monkeypatch.setattr("app.services.onebot_service.random.random", lambda: 0.1)

    async def run() -> tuple[str, dict, bool]:
        return await service.send_ai_reply(
            {"message_type": "group", "group_id": "100"},
            "你好",
            "123456",
            config={"voice_reply_enabled": True, "voice_reply_probability": 100},
        )

    action, response, used_voice = asyncio.run(run())
    assert action == "send_group_msg"
    assert response["status"] == "ok"
    assert used_voice is False
    assert [item[0] for item in manager.calls] == [
        "get_ai_characters",
        "send_group_ai_record",
        "get_ai_characters",
        "send_group_ai_record",
        "send_group_msg",
    ]


def test_private_ai_reply_never_uses_group_voice(monkeypatch) -> None:
    """私聊即使命中概率也只能发送私聊文本。"""
    service, manager = _service([{"status": "ok", "retcode": 0}])
    monkeypatch.setattr("app.services.onebot_service.random.random", lambda: 0.0)

    async def run() -> tuple[str, dict, bool]:
        return await service.send_ai_reply(
            {"message_type": "private", "user_id": "99"},
            "你好",
            "123456",
            config={"voice_reply_enabled": True, "voice_reply_probability": 100},
        )

    action, response, used_voice = asyncio.run(run())
    assert action == "send_private_msg"
    assert response["status"] == "ok"
    assert used_voice is False
    assert manager.calls == [("send_private_msg", {"user_id": "99", "message": "你好"})]


def test_image_reply_probability_controls_group_image_attempt(monkeypatch) -> None:
    """图片概率为 0 时不发送，命中 100% 时允许发送。"""
    event = {"message_type": "group", "group_id": "100"}
    monkeypatch.setattr("app.services.onebot_service.random.random", lambda: 0.5)
    assert OneBotService._should_use_image(
        {"image_reply_probability": 0}, event
    ) is False
    assert OneBotService._should_use_image(
        {"image_reply_probability": 100}, event
    ) is True


def test_websocket_ai_sends_text_before_image_classification() -> None:
    """群聊 AI 正文发送不应等待动画表情分类。"""
    service = object.__new__(OneBotService)
    service._reply_tasks = set()
    service.database = object()
    service.bot_ai_service = object()
    calls: list[str] = []

    class _SessionContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

    class _Database:
        def session_factory(self):
            return _SessionContext()

    class _BotAi:
        async def active_runtime_config(self, *_args):
            return {"image_reply_probability": 100}

    service.database = _Database()
    service.bot_ai_service = _BotAi()

    async def generate(*_args, **_kwargs):
        return "AI 正文", False

    async def send(*_args, **kwargs):
        assert kwargs["config"]["image_reply_probability"] == 0
        calls.append("text")
        return "send_group_msg", {"status": "ok"}, False

    async def image(*_args, **_kwargs):
        calls.append("image")

    service._generate_ai_reply = generate
    service._should_use_image = lambda *_args, **_kwargs: True
    service.send_ai_reply = send
    service._handle_background_reply_image = image

    async def run() -> None:
        await service._handle_websocket_ai_reply(
            {"message_type": "group", "group_id": "100"},
            SimpleNamespace(self_id="123456"),
        )
        assert calls == ["text"]
        await asyncio.gather(*tuple(service._reply_tasks))

    asyncio.run(run())
    assert calls == ["text", "image"]


def test_websocket_generate_image_intent_sends_only_generated_image() -> None:
    """显式文生图意图成功时应只发送图片，不先发送 AI 文本。"""
    service = object.__new__(OneBotService)
    service._reply_tasks = set()
    calls: list[str] = []

    class _SessionContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

    class _Database:
        def session_factory(self):
            return _SessionContext()

    class _BotAi:
        async def active_runtime_config(self, *_args):
            return {"image_reply_probability": 100}

    service.database = _Database()
    service.bot_ai_service = _BotAi()

    async def generate(*_args, **_kwargs):
        return "不应发送的文本", False

    async def resolve_image(*_args, **_kwargs):
        calls.append("resolve_image")
        return {"media_type": "image/png", "data": "aW1hZ2U="}

    async def send(*_args, **kwargs):
        assert kwargs["image_only"] is True
        assert kwargs["reply_image"] == {
            "media_type": "image/png",
            "data": "aW1hZ2U=",
        }
        calls.append("send_image")
        return "send_group_msg", {"status": "ok"}, False

    async def background_image(*_args, **_kwargs):
        calls.append("unexpected_background_image")

    service._generate_ai_reply = generate
    service._resolve_intent_reply_image = resolve_image
    service.send_ai_reply = send
    service._handle_background_reply_image = background_image

    intent = {
        "mute": False,
        "mute_reason": "",
        "reply_type": "generate_image",
        "emoji_category": "",
        "generate_words": "可爱少女自拍",
        "reason": "用户要求自拍",
    }

    async def run() -> None:
        await service._handle_websocket_ai_reply(
            {"message_type": "group", "group_id": "100"},
            SimpleNamespace(self_id="123456"),
            intent,
        )
        await asyncio.gather(*tuple(service._reply_tasks))

    asyncio.run(run())
    assert calls == ["resolve_image", "send_image"]


def test_explicit_image_reply_uses_single_onebot_action() -> None:
    """仅图片模式应通过一次 OneBot 动作发送图片。"""
    service, manager = _service([{"status": "ok", "retcode": 0}])

    async def run() -> tuple[str, dict, bool]:
        return await service.send_ai_reply(
            {"message_type": "group", "group_id": "100"},
            "不应发送的文本",
            "123456",
            reply_image={"media_type": "image/png", "data": "aW1hZ2U="},
            image_only=True,
        )

    action, response, used_voice = asyncio.run(run())
    assert action == "send_group_msg"
    assert response["status"] == "ok"
    assert used_voice is False
    assert manager.calls == [
        (
            "send_group_msg",
            {
                "group_id": "100",
                "message": [
                    {
                        "type": "image",
                        "data": {"file": "base64://aW1hZ2U="},
                    }
                ],
            },
        )
    ]


def test_text_reply_can_append_base64_animated_emoji() -> None:
    """文本回复成功后应单独发送选中的 Base64 动画表情。"""
    service, manager = _service(
        [{"status": "ok", "retcode": 0}, {"status": "ok", "retcode": 0}]
    )

    async def resolve_image(*_args, **_kwargs) -> dict[str, str]:
        return {"id": "1", "category": "搞笑", "data": "YWJj", "media_type": "image/jpeg"}

    service._resolve_reply_image = resolve_image

    async def run() -> tuple[str, dict, bool]:
        return await service.send_ai_reply(
            {"message_type": "group", "group_id": "100"},
            "你好",
            "123456",
            config={"voice_reply_enabled": False},
        )

    action, response, used_voice = asyncio.run(run())
    assert action == "send_group_msg"
    assert response["status"] == "ok"
    assert used_voice is False
    assert manager.calls == [
        ("send_group_msg", {"group_id": "100", "message": "你好"}),
        (
            "send_group_msg",
            {"group_id": "100", "message": [{"type": "image", "data": {"file": "base64://YWJj"}}]},
        ),
    ]


def test_voice_reply_sends_base64_animated_emoji_after_voice(monkeypatch) -> None:
    """语音成功后应额外发送一条不引用原消息的 Base64 图片。"""
    service, manager = _service(
        [
            {
                "status": "ok",
                "data": [{"characters": [{"character_id": "lucy-voice-f38"}]}],
            },
            {"status": "ok", "retcode": 0},
            {"status": "ok", "retcode": 0},
        ]
    )

    async def resolve_image(*_args, **_kwargs) -> dict[str, str]:
        return {"id": "2", "category": "开心", "data": "ZGVm", "media_type": "image/png"}

    service._resolve_reply_image = resolve_image
    monkeypatch.setattr("app.services.onebot_service.random.random", lambda: 0.0)

    async def run() -> tuple[str, dict, bool]:
        return await service.send_ai_reply(
            {"message_type": "group", "group_id": "100"},
            "你好",
            "123456",
            config={"voice_reply_enabled": True, "voice_reply_probability": 100},
        )

    action, response, used_voice = asyncio.run(run())
    assert action == "send_group_ai_record"
    assert response["status"] == "ok"
    assert used_voice is True
    assert manager.calls[-1] == (
        "send_group_msg",
        {"group_id": "100", "message": [{"type": "image", "data": {"file": "base64://ZGVm"}}]},
    )
