"""群聊仅 @ 机器人时的 AI 触发测试。"""

import asyncio

from app.services.onebot_service import OneBotService


class _ImageStub:
    """避免测试触发真实图片下载。"""

    http_client = None

    @staticmethod
    def has_candidates(_event: dict) -> bool:
        return False

    async def collect(self, _event: dict, _http_url: str, _http_token: str) -> list:
        return []


class _NicknameSession:
    """返回测试机器人名称的异步数据库会话替身。"""

    async def __aenter__(self) -> "_NicknameSession":
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def scalar(self, _statement):
        return "青青云"


class _NicknameDatabase:
    """提供机器人名称查询的测试数据库替身。"""

    def session_factory(self) -> _NicknameSession:
        return _NicknameSession()


class _FollowupStub:
    """名称触发时不应消费 Redis 免触发额度。"""

    async def consume(self, *_args, **_kwargs) -> bool:
        raise AssertionError("名称触发不应消费免触发额度")


class _FollowupResultStub:
    """按预设结果返回免触发额度消费，并记录调用次数。"""

    def __init__(self, result: bool) -> None:
        self.result = result
        self.calls = 0

    async def consume(self, *_args, **_kwargs) -> bool:
        self.calls += 1
        return self.result


class _TriggerSession:
    """提供 AI 触发模式测试所需的最小异步会话。"""

    async def __aenter__(self) -> "_TriggerSession":
        return self

    async def __aexit__(self, *_args) -> None:
        return None


class _TriggerDatabase:
    """提供关键词规则查询所需的最小数据库替身。"""

    def session_factory(self) -> _TriggerSession:
        return _TriggerSession()


class _TriggerService:
    """返回固定随机回复配置且不命中关键词的规则替身。"""

    async def get_reply_mode(self, *_args, **_kwargs) -> dict:
        return {
            "reply_all": False,
            "random_reply": True,
            "random_reply_probability": 35,
        }

    async def match_event(self, *_args, **_kwargs) -> None:
        return None


def test_group_at_without_text_triggers_ai_prompt() -> None:
    """@ 段后只有空格时仍应生成默认 AI 提示词。"""
    service = object.__new__(OneBotService)
    service.ai_image_service = _ImageStub()
    service.http_client = object()

    async def connection_settings(_bot_qq: str) -> tuple[str, str, str]:
        return "auto", "", ""

    service._connection_settings = connection_settings
    event = {
        "message_type": "group",
        "group_id": "100",
        "user_id": "200",
        "message": [
            {"type": "at", "data": {"qq": "123456"}},
            {"type": "text", "data": {"text": " "}},
        ],
    }

    prompt = asyncio.run(
        service._prepare_ai_prompt(event, "123456", None, followup_count=3)
    )

    assert prompt is not None
    assert prompt[0] == "有人@了你，请回复。"
    assert prompt[1:] == ("", False, [])


def test_group_random_reply_uses_configured_probability(monkeypatch) -> None:
    """随机回复模式命中概率时触发，未命中且无免触发额度时不回复。"""
    service = object.__new__(OneBotService)
    service.ai_image_service = _ImageStub()
    service.http_client = object()
    service.database = _TriggerDatabase()
    service.ai_trigger_keyword_service = _TriggerService()
    service.ai_followup_service = _FollowupResultStub(False)

    async def connection_settings(_bot_qq: str) -> tuple[str, str, str]:
        return "auto", "", ""

    async def bot_nickname(_bot_qq: str) -> str:
        return ""

    service._connection_settings = connection_settings
    service._bot_nickname = bot_nickname
    event = {
        "message_type": "group",
        "group_id": "100",
        "user_id": "200",
        "message": [{"type": "text", "data": {"text": "普通消息"}}],
    }

    monkeypatch.setattr("app.services.onebot_service.random.random", lambda: 0.2)
    hit = asyncio.run(
        service._prepare_ai_prompt(event, "123456", None, followup_count=3)
    )
    assert hit is not None
    assert hit[0] == "普通消息"
    # 命中随机概率时不消费免触发额度，命中本身即为触发。
    assert service.ai_followup_service.calls == 0

    monkeypatch.setattr("app.services.onebot_service.random.random", lambda: 0.9)
    miss = asyncio.run(
        service._prepare_ai_prompt(event, "123456", None, followup_count=3)
    )
    assert miss is None
    # 未命中随机概率时应尝试消费免触发额度，无额度才最终不回复。
    assert service.ai_followup_service.calls == 1


def test_group_random_reply_miss_uses_followup_allowance(monkeypatch) -> None:
    """随机概率未命中时，若用户仍有免触发额度则照常触发 AI。"""
    service = object.__new__(OneBotService)
    service.ai_image_service = _ImageStub()
    service.http_client = object()
    service.database = _TriggerDatabase()
    service.ai_trigger_keyword_service = _TriggerService()
    service.ai_followup_service = _FollowupResultStub(True)

    async def connection_settings(_bot_qq: str) -> tuple[str, str, str]:
        return "auto", "", ""

    async def bot_nickname(_bot_qq: str) -> str:
        return ""

    service._connection_settings = connection_settings
    service._bot_nickname = bot_nickname
    event = {
        "message_type": "group",
        "group_id": "100",
        "user_id": "200",
        "message": [{"type": "text", "data": {"text": "普通消息"}}],
    }

    # 随机概率未命中，但仍有免触发额度可消费。
    monkeypatch.setattr("app.services.onebot_service.random.random", lambda: 0.9)
    prompt = asyncio.run(
        service._prepare_ai_prompt(event, "123456", None, followup_count=3)
    )

    assert prompt is not None
    assert prompt[0] == "普通消息"
    # followup_used 位应为 True，表示本次消费了免触发额度。
    assert prompt[2] is True
    assert service.ai_followup_service.calls == 1



def test_group_face_random_reply_uses_face_description(monkeypatch) -> None:
    """QQ 内置表情应作为文字描述交给 AI，不能按图片分析。"""
    service = object.__new__(OneBotService)
    service.ai_image_service = _ImageStub()
    service.http_client = object()
    service.database = _TriggerDatabase()
    service.ai_trigger_keyword_service = _TriggerService()
    service.ai_followup_service = _FollowupStub()

    async def connection_settings(_bot_qq: str) -> tuple[str, str, str]:
        return "auto", "", ""

    async def bot_nickname(_bot_qq: str) -> str:
        return ""

    service._connection_settings = connection_settings
    service._bot_nickname = bot_nickname
    event = {
        "message_type": "group",
        "group_id": "718700344",
        "user_id": "531779708",
        "raw_message": '[CQ:face,id=271,raw={"faceText":"/吃瓜"}]',
        "message": [
            {
                "type": "face",
                "data": {
                    "id": "271",
                    "raw": {"faceIndex": 271, "faceText": "/吃瓜", "faceType": 2},
                },
            }
        ],
    }

    monkeypatch.setattr("app.services.onebot_service.random.random", lambda: 0.2)
    prompt = asyncio.run(
        service._prepare_ai_prompt(event, "123456", None, followup_count=3)
    )

    assert prompt is not None
    assert prompt[0] == "QQ 表情：/吃瓜"
    assert prompt[0] != "请分析图片内容。"
    assert prompt[3] == []


def test_group_face_without_description_uses_generic_prompt(monkeypatch) -> None:
    """缺少表情名称时也只能按普通消息回复，不能要求 AI 分析图片。"""
    service = object.__new__(OneBotService)
    service.ai_image_service = _ImageStub()
    service.http_client = object()
    service.database = _TriggerDatabase()
    service.ai_trigger_keyword_service = _TriggerService()
    service.ai_followup_service = _FollowupStub()

    async def connection_settings(_bot_qq: str) -> tuple[str, str, str]:
        return "auto", "", ""

    async def bot_nickname(_bot_qq: str) -> str:
        return ""

    service._connection_settings = connection_settings
    service._bot_nickname = bot_nickname
    event = {
        "message_type": "group",
        "group_id": "718700344",
        "user_id": "531779708",
        "message": [{"type": "face", "data": {"id": "271"}}],
    }

    monkeypatch.setattr("app.services.onebot_service.random.random", lambda: 0.2)
    prompt = asyncio.run(
        service._prepare_ai_prompt(event, "123456", None, followup_count=3)
    )

    assert prompt is not None
    assert prompt[0] == "请回复这条消息。"
    assert prompt[3] == []


def test_group_bot_nickname_triggers_ai_prompt_without_consuming_followup() -> None:
    """消息包含机器人管理名称时应触发 AI，且不消费已有免触发额度。"""
    service = object.__new__(OneBotService)
    service.ai_image_service = _ImageStub()
    service.http_client = object()
    service.database = _NicknameDatabase()
    service.ai_followup_service = _FollowupStub()

    async def connection_settings(_bot_qq: str) -> tuple[str, str, str]:
        return "auto", "", ""

    service._connection_settings = connection_settings
    event = {
        "message_type": "group",
        "group_id": "100",
        "user_id": "200",
        "message": [{"type": "text", "data": {"text": "青青云，帮我查一下"}}],
    }

    prompt = asyncio.run(
        service._prepare_ai_prompt(event, "123456", None, followup_count=3)
    )

    assert prompt is not None
    assert prompt[0] == "青青云，帮我查一下"
    assert prompt[1:] == ("", False, [])
