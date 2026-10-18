"""AI 消息意图严格解析、强制回复形式和文生图测试。"""

import asyncio
import base64
import json
from types import SimpleNamespace

import httpx

from app.services.ai_image_generation_service import AiImageGenerationService
from app.services.ai_message_intent import (
    DEFAULT_MESSAGE_INTENT_PROMPT,
    build_message_intent_prompt,
    parse_message_intent,
    should_skip_reply,
)
from app.services.ai_provider_service import AiProviderClient
from app.services.onebot_service import OneBotService


def _intent(**updates) -> dict:
    """构造完整的测试意图 JSON。"""
    value = {
        "mute": False,
        "mute_reason": "",
        "mute_keyword": "",
        "reply_type": "other",
        "emoji_category": "",
        "generate_words": "",
        "reason": "普通回复",
    }
    value.update(updates)
    return value


def test_parse_message_intent_requires_strict_json_and_available_capability() -> None:
    """意图结果必须字段完整、类型正确且符合当前可用能力。"""
    voice = parse_message_intent(
        json.dumps(_intent(reply_type="voice"), ensure_ascii=False),
        emoji_categories=["开心"],
        voice_available=True,
        image_generation_available=True,
    )
    assert voice is not None
    assert voice["reply_type"] == "voice"

    generated = parse_message_intent(
        json.dumps(
            _intent(
                reply_type="generate_image",
                generate_words="黑色长发女生的室内自拍，柔和自然光",
            ),
            ensure_ascii=False,
        ),
        emoji_categories=["开心"],
        voice_available=True,
        image_generation_available=True,
    )
    assert generated is not None
    assert generated["generate_words"] == "黑色长发女生的室内自拍，柔和自然光"

    legacy = _intent(reply_type="generate_image")
    legacy["image_prompt"] = "兼容旧字段的图片提示词"
    legacy.pop("generate_words")
    legacy.pop("mute_keyword")
    legacy_result = parse_message_intent(
        json.dumps(legacy, ensure_ascii=False),
        emoji_categories=[],
        voice_available=False,
        image_generation_available=True,
    )
    assert legacy_result is not None
    assert legacy_result["generate_words"] == "兼容旧字段的图片提示词"
    assert legacy_result["mute_keyword"] == ""

    invalid_extra = _intent(extra="不允许")
    assert parse_message_intent(
        json.dumps(invalid_extra, ensure_ascii=False),
        emoji_categories=["开心"],
        voice_available=True,
        image_generation_available=True,
    ) is None
    assert parse_message_intent(
        json.dumps(_intent(reply_type="emoji", emoji_category="不存在"), ensure_ascii=False),
        emoji_categories=["开心"],
        voice_available=True,
        image_generation_available=True,
    ) is None
    assert parse_message_intent(
        json.dumps(
            _intent(reply_type="generate_image", generate_words=""),
            ensure_ascii=False,
        ),
        emoji_categories=[],
        voice_available=False,
        image_generation_available=True,
    ) is None


def test_parse_message_intent_captures_and_scopes_mute_keyword() -> None:
    """禁言时保留 AI 摘录的连续违规关键词，未禁言时必须清空。"""
    muted = parse_message_intent(
        json.dumps(
            _intent(mute=True, mute_reason="辱骂", mute_keyword="垃圾东西"),
            ensure_ascii=False,
        ),
        emoji_categories=[],
        voice_available=False,
        image_generation_available=False,
    )
    assert muted is not None
    assert muted["mute"] is True
    assert muted["mute_keyword"] == "垃圾东西"

    # 未禁言但模型误填关键词时应被清空，避免误学习。
    not_muted = parse_message_intent(
        json.dumps(_intent(mute_keyword="不该出现"), ensure_ascii=False),
        emoji_categories=[],
        voice_available=False,
        image_generation_available=False,
    )
    assert not_muted is not None
    assert not_muted["mute"] is False
    assert not_muted["mute_keyword"] == ""

    prompt = build_message_intent_prompt(
        bot_qq="123456",
        group_id="99",
        user_id="88",
        message_text="你这个垃圾东西",
        mute_prompt="辱骂需要禁言",
        emoji_categories=[],
        voice_available=False,
        image_generation_available=False,
        has_images=False,
    )
    assert "mute_keyword 必须逐字摘录" in prompt
    assert '"mute_keyword":""' in prompt


def test_build_message_intent_prompt_uses_bot_custom_classification_prompt() -> None:
    """机器人配置的分类提示词应替换默认分类规则并保留严格协议。"""
    prompt = build_message_intent_prompt(
        bot_qq="123456",
        group_id="99",
        user_id="88",
        message_text="给我一个惊喜",
        mute_prompt="",
        emoji_categories=["开心"],
        voice_available=True,
        image_generation_available=True,
        has_images=False,
        classification_prompt="用户说惊喜时优先选择 emoji。",
        character_prompt="二十岁女生，黑色长发，性格活泼。",
    )

    assert "用户说惊喜时优先选择 emoji。" in prompt
    assert "机器人人物设定：\n二十岁女生，黑色长发，性格活泼。" in prompt
    assert "人物设定只用于完善 generate_words" in prompt
    assert "用户已有明确画面描述时只完善该描述" in prompt
    assert "不要出现“人物设定”“根据设定”等元话术" in prompt
    assert DEFAULT_MESSAGE_INTENT_PROMPT not in prompt
    assert '"reply_type":"other"' in prompt
    assert '"generate_words":""' in prompt


def test_message_intent_retries_invalid_json_with_complete_response_config() -> None:
    """意图模型格式错误时应保持严格配置并进行一次纠正重试。"""
    service = object.__new__(OneBotService)
    generated: list[tuple[dict, str]] = []

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
            return {
                "bot_qq": "123456",
                "history_limit": 10,
                "voice_reply_enabled": False,
                "message_intent_prompt": "自拍请求选择 generate_image。",
                "system_prompt": "黑色长发、活泼可爱的少女。",
            }

        @staticmethod
        def has_image_generation_config(_config: dict) -> bool:
            return True

        async def generate(self, config: dict, prompt: str, **_kwargs) -> str:
            generated.append((dict(config), prompt))
            if len(generated) == 1:
                return '{"mute":false'
            return json.dumps(
                _intent(
                    reply_type="generate_image",
                    generate_words="黑色长发少女的自然光自拍",
                    reason="用户要求自拍",
                ),
                ensure_ascii=False,
            )

    class _ChatMessages:
        @staticmethod
        async def get_ai_history(*_args, **_kwargs):
            return []

    service.database = _Database()
    service.bot_ai_service = _BotAi()
    service.chat_message_service = _ChatMessages()
    service.animated_emoji_service = None

    event = {
        "self_id": "123456",
        "post_type": "message",
        "message_type": "group",
        "group_id": "99",
        "user_id": "88",
        "message_id": "77",
        "message": [{"type": "text", "data": {"text": "发个自拍"}}],
    }
    intent = asyncio.run(service._analyse_message_intent(event, None))

    assert intent["reply_type"] == "generate_image"
    assert intent["generate_words"] == "黑色长发少女的自然光自拍"
    assert len(generated) == 2
    for config, _prompt in generated:
        assert config["max_tokens"] == 2000
        assert config["_require_complete_response"] is True
        assert config["_disable_config_refresh"] is True
    assert "上一次返回无法通过严格 JSON 校验" in generated[1][1]


def test_parse_message_intent_accepts_silent_reply_type() -> None:
    """不回复意图无需任何能力支持，多填的表情或文生图内容应被清空而不是整体作废。"""
    silent = parse_message_intent(
        json.dumps(
            _intent(reply_type="silent", reason="群友互相聊天，与机器人无关"),
            ensure_ascii=False,
        ),
        emoji_categories=[],
        voice_available=False,
        image_generation_available=False,
    )
    assert silent is not None
    assert silent["reply_type"] == "silent"
    assert silent["emoji_category"] == ""
    assert silent["generate_words"] == ""

    silent_with_emoji = parse_message_intent(
        json.dumps(
            _intent(reply_type="silent", emoji_category="开心"),
            ensure_ascii=False,
        ),
        emoji_categories=["开心"],
        voice_available=True,
        image_generation_available=True,
    )
    assert silent_with_emoji is not None
    assert silent_with_emoji["reply_type"] == "silent"
    assert silent_with_emoji["emoji_category"] == ""
    assert should_skip_reply(silent_with_emoji) is True

    silent_with_words = parse_message_intent(
        json.dumps(
            _intent(reply_type="silent", generate_words="一只猫"),
            ensure_ascii=False,
        ),
        emoji_categories=["开心"],
        voice_available=True,
        image_generation_available=True,
    )
    assert silent_with_words is not None
    assert silent_with_words["reply_type"] == "silent"
    assert silent_with_words["generate_words"] == ""
    assert should_skip_reply(silent_with_words) is True

    fenced = parse_message_intent(
        "```json\n"
        + json.dumps(
            _intent(reply_type="silent", reason="话题已经结束"),
            ensure_ascii=False,
        )
        + "\n```",
        emoji_categories=["开心"],
        voice_available=True,
        image_generation_available=True,
    )
    assert fenced is not None
    assert should_skip_reply(fenced) is True

    assert should_skip_reply(silent) is True
    assert should_skip_reply(_intent()) is False
    assert should_skip_reply(None) is False

    prompt = build_message_intent_prompt(
        bot_qq="123456",
        group_id="99",
        user_id="88",
        message_text="你们继续聊",
        mute_prompt="",
        emoji_categories=[],
        voice_available=False,
        image_generation_available=False,
        has_images=False,
    )
    assert "voice、emoji、generate_image、silent、other" in prompt
    assert "选择 silent，系统会跳过本次回复、不发送任何内容" in prompt
    assert "消息不需要机器人参与时选择 silent" in prompt


def test_silent_intent_skips_ai_reply_generation() -> None:
    """意图判定为不回复时不得生成内容，只标记消息已处理且不续期免触发。"""
    service = object.__new__(OneBotService)
    generated: list[str] = []
    marked: list[str] = []
    followups: list[str] = []

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
            return {"bot_qq": "123456", "followup_count": 3, "history_limit": 10}

        async def generate(self, *_args, **_kwargs) -> str:
            generated.append("called")
            return "不应该生成的回复"

    async def mark_ai_triggered(event: dict, _bot_qq: str) -> None:
        marked.append(str(event.get("message_id") or ""))

    async def get_ai_history(*_args, **_kwargs) -> list:
        return []

    async def activate(*_args, **_kwargs) -> None:
        followups.append("activated")

    async def prepare_prompt(*_args, **_kwargs):
        return "在吗", "", False, []

    async def ensure_reply_claim(*_args, **_kwargs) -> bool:
        return True

    async def analyse_message_intent(*_args, **_kwargs) -> dict:
        return _intent(reply_type="silent", reason="群友互聊，无需机器人参与")

    service.database = _Database()
    service.bot_ai_service = _BotAi()
    service.chat_message_service = SimpleNamespace(
        mark_ai_triggered=mark_ai_triggered,
        get_ai_history=get_ai_history,
    )
    service.ai_followup_service = SimpleNamespace(activate=activate)
    service._prepare_ai_prompt = prepare_prompt
    service._ensure_reply_claim = ensure_reply_claim
    service._analyse_message_intent = analyse_message_intent

    event = {
        "self_id": "123456",
        "post_type": "message",
        "message_type": "group",
        "group_id": "99",
        "user_id": "88",
        "message_id": "77",
        "message": [{"type": "text", "data": {"text": "你们继续聊"}}],
    }

    assert asyncio.run(service._generate_ai_reply(event, "123456")) is None
    assert generated == []
    assert marked == ["77"]
    assert followups == []
    assert event["_ai_message_intent"]["reply_type"] == "silent"


def test_openai_compatible_image_generation_returns_base64_image() -> None:
    """OpenAI 兼容文生图响应应转换为统一 Base64 图片结构。"""
    requests: list[dict] = []
    png = b"\x89PNG\r\n\x1a\nimage-content"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(
            {
                "url": str(request.url),
                "authorization": request.headers.get("authorization"),
                "body": json.loads(request.content),
            }
        )
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(png).decode("ascii")}]},
        )

    async def run() -> dict:
        provider = AiProviderClient()
        await provider.http_client.aclose()
        provider.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=2
        )
        try:
            return await AiImageGenerationService(provider).generate(
                {
                    "provider_type": "openai_compatible",
                    "base_url": "https://ai.example/v1",
                    "api_key": "secret",
                    "model_name": "image-model",
                },
                "一只蓝色纸飞机",
            )
        finally:
            await provider.close()

    image = asyncio.run(run())
    assert image == {
        "media_type": "image/png",
        "data": base64.b64encode(png).decode("ascii"),
    }
    assert requests == [
        {
            "url": "https://ai.example/v1/images/generations",
            "authorization": "Bearer secret",
            "body": {
                "model": "image-model",
                "prompt": "一只蓝色纸飞机",
                "n": 1,
                "response_format": "b64_json",
            },
        }
    ]


def test_relay_qwen_image_uses_openai_images_endpoint() -> None:
    """支持 OpenAI 图片协议的中转可直接生成 qwen-image 图片。"""
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "authorization": request.headers.get("authorization"),
                "body": json.loads(request.content or b"{}"),
            }
        )
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "b64_json": base64.b64encode(
                            b"\x89PNG\r\n\x1a\nqwen-image"
                        ).decode("ascii")
                    }
                ]
            },
        )

    async def run() -> dict[str, str]:
        provider = AiProviderClient()
        await provider.http_client.aclose()
        provider.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=2
        )
        try:
            return await AiImageGenerationService(provider).generate(
                {
                    "provider_type": "openai_compatible",
                    "base_url": "https://relay.example/compatible-mode/v1",
                    "api_key": "dashscope-secret",
                    "model_name": "qwen-image-2.0",
                },
                "一只蓝色纸飞机",
            )
        finally:
            await provider.close()

    image = asyncio.run(run())
    assert image == {
        "media_type": "image/png",
        "data": base64.b64encode(b"\x89PNG\r\n\x1a\nqwen-image").decode("ascii"),
    }
    assert requests[0] == {
        "method": "POST",
        "url": "https://relay.example/compatible-mode/v1/images/generations",
        "authorization": "Bearer dashscope-secret",
        "body": {
            "model": "qwen-image-2.0",
            "prompt": "一只蓝色纸飞机",
            "n": 1,
            "response_format": "b64_json",
        },
    }


def test_qwen_image_falls_back_to_native_protocol_when_images_endpoint_is_unsupported() -> None:
    """OpenAI 图片接口不支持 Qwen-Image 时应回退到百炼原生接口。"""
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "body": json.loads(request.content or b"{}"),
            }
        )
        if request.url.path.endswith("/images/generations"):
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "images endpoint requires an image model, got qwen-image-2.0"
                    }
                },
            )
        if request.method == "GET":
            return httpx.Response(
                200,
                headers={"Content-Type": "image/png"},
                content=b"\x89PNG\r\n\x1a\nqwen-image",
            )
        return httpx.Response(
            200,
            json={
                "output": {
                    "choices": [
                        {
                            "message": {
                                "content": [{"image": "https://image.example/qwen.png"}]
                            }
                        }
                    ]
                }
            },
        )

    async def run() -> dict[str, str]:
        provider = AiProviderClient()
        await provider.http_client.aclose()
        provider.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=2
        )
        try:
            return await AiImageGenerationService(provider).generate(
                {
                    "provider_type": "openai_compatible",
                    "base_url": "https://relay.example/compatible-mode/v1",
                    "api_key": "relay-secret",
                    "model_name": "qwen-image-2.0",
                },
                "一只蓝色纸飞机",
            )
        finally:
            await provider.close()

    image = asyncio.run(run())
    assert image == {
        "media_type": "image/png",
        "data": base64.b64encode(b"\x89PNG\r\n\x1a\nqwen-image").decode("ascii"),
    }
    assert requests[0]["url"] == "https://relay.example/compatible-mode/v1/images/generations"
    assert requests[1]["url"] == (
        "https://relay.example/api/v1/services/aigc/multimodal-generation/generation"
    )
    assert requests[2]["method"] == "GET"


def test_intent_voice_forces_existing_voice_logic_and_private_image_is_supported() -> None:
    """语音意图应强制命中现有概率逻辑，私聊也应支持 Base64 图片。"""
    config = OneBotService._intent_reply_config(
        {"voice_reply_enabled": True, "voice_reply_probability": 10},
        _intent(reply_type="voice"),
    )
    assert config["voice_reply_probability"] == 100
    assert config["image_reply_probability"] == 0

    action = OneBotService._build_reply_action(
        {"message_type": "private", "user_id": "99"},
        "图片已生成",
        image={"media_type": "image/png", "data": "YWJj"},
    )
    assert action == (
        "send_private_msg",
        {
            "user_id": "99",
            "message": [
                {"type": "text", "data": {"text": "图片已生成"}},
                {"type": "image", "data": {"file": "base64://YWJj"}},
            ],
        },
    )


def test_generate_image_intent_uses_bot_image_generation_config() -> None:
    """文生图意图应把严格 JSON 中的提示词交给机器人文生图服务。"""
    service = object.__new__(OneBotService)
    calls: list[tuple[dict, str]] = []

    async def generate_image(config: dict, prompt: str) -> dict[str, str]:
        calls.append((config, prompt))
        return {"media_type": "image/png", "data": "YWJj"}

    service.bot_ai_service = SimpleNamespace(generate_image=generate_image)
    config = {"bot_qq": "123456"}

    async def run() -> dict[str, str] | None:
        return await service._resolve_intent_reply_image(
            config,
            _intent(
                reply_type="generate_image",
                generate_words="雨夜中的未来城市，霓虹灯倒影",
            ),
            "123456",
            {"message_type": "private", "user_id": "88"},
        )

    assert asyncio.run(run()) == {"media_type": "image/png", "data": "YWJj"}
    assert calls == [(config, "雨夜中的未来城市，霓虹灯倒影")]
