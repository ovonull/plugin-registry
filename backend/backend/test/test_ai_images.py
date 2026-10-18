"""测试 OneBot 图片提取、多模态平台协议和 AI 触发链路。"""

import asyncio
import base64
import hashlib
import hmac
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.common.responses import BusinessError
from app.services.ai_image_service import AiImageService
from app.services.ai_provider_service import AiProviderClient
from test_bot_ai import (
    _ai_payload,
    _create_bot,
    _headers,
    _intent_response,
    _is_intent_request,
)
from test_service import build_settings


BOT_QQ = "223344"


def test_collect_image_from_raw_record_local_path(tmp_path: Path) -> None:
    """从 NapCat raw.records 的 picElement 本地路径读取图片并编码。"""
    image_path = tmp_path / "emoji.jpg"
    image_path.write_bytes(b"fake-image")
    event = {
        "message": [{"type": "text", "data": {"text": "你能看到吗"}}],
        "raw": {
            "records": [
                {
                    "elements": [
                        {
                            "picElement": {
                                "sourcePath": str(image_path),
                                "originImageUrl": "/download?fileid=test",
                            }
                        }
                    ]
                }
            ]
        },
    }

    async def run() -> list[dict[str, str]]:
        async with httpx.AsyncClient() as client:
            return await AiImageService(client).collect(event)

    images = asyncio.run(run())
    assert images == [
        {
            "media_type": "image/jpeg",
            "data": base64.b64encode(b"fake-image").decode("ascii"),
        }
    ]


def test_collect_image_from_onebot_base64_without_http_request() -> None:
    """OneBot 的 base64:// 图片应直接解码，不再请求公网或 NapCat 地址。"""
    payload = base64.b64encode(b"inline-image").decode("ascii")
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(502, text="不应请求图片地址")

    event = {
        "message": [
            {
                "type": "image",
                "data": {
                    "summary": "[动画表情]",
                    "file": f"base64://{payload}",
                    "mime": "image/png",
                    "url": "https://multimedia.example/expired.jpg",
                },
            }
        ]
    }

    async def run() -> list[dict[str, str]]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=2
        ) as client:
            return await AiImageService(client).collect(
                event, "http://127.0.0.1:3000"
            )

    assert asyncio.run(run()) == [
        {
            "media_type": "image/png",
            "data": payload,
        }
    ]
    assert requests == []


def test_image_candidates_merge_message_and_raw_duplicate() -> None:
    """同一图片同时出现在 message 和 raw 时只保留一个候选并合并来源。"""
    event = {
        "message": [
            {
                "type": "image",
                "data": {
                    "summary": "[动画表情]",
                    "file": "emoji.jpg",
                    "url": "https://multimedia.example/emoji.jpg",
                },
            }
        ],
        "raw": {
            "records": [
                {
                    "elements": [
                        {
                            "picElement": {
                                "fileName": "emoji.jpg",
                                "sourcePath": "C:/qq/emoji.jpg",
                            }
                        }
                    ]
                }
            ]
        },
    }
    candidates = AiImageService.candidates(event)
    assert len(candidates) == 1
    assert candidates[0]["path"] == "C:/qq/emoji.jpg"
    assert len(AiImageService.animated_candidates(event)) == 1


def test_qq_face_is_not_an_image_candidate() -> None:
    """QQ 内置 face 表情不应进入多模态图片输入。"""
    event = {
        "post_type": "message",
        "message_format": "array",
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

    assert AiImageService.candidates(event) == []
    assert AiImageService.animated_candidates(event) == []


def test_collect_image_prefers_public_url_and_skips_duplicate_download() -> None:
    """公网图片成功后不再重复请求同图的 NapCat /download 地址。"""
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"png",
        )

    event = {
        "message": [
            {
                "type": "image",
                "data": {
                    "file": "same-image.png",
                    "url": "https://multimedia.example/download?fileid=same",
                },
            }
        ],
        "raw": {
            "records": [
                {
                    "elements": [
                        {
                            "picElement": {
                                "fileName": "same-image.png",
                                "originImageUrl": "/download?fileid=same",
                            }
                        }
                    ]
                }
            ]
        },
    }

    async def run() -> list[dict[str, str]]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=2
        ) as client:
            return await AiImageService(client).collect(
                event, "http://127.0.0.1:3000"
            )

    images = asyncio.run(run())
    assert images == [
        {
            "media_type": "image/png",
            "data": base64.b64encode(b"png").decode("ascii"),
        }
    ]
    assert requests == [
        "https://multimedia.example/download?fileid=same"
    ]


def test_collect_public_image_retries_three_times_before_success() -> None:
    """公网图片遇到临时 HTTP 错误时最多重试三次。"""
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts <= 3:
            return httpx.Response(502, text="upstream unavailable")
        return httpx.Response(
            200,
            headers={"content-type": "image/jpeg"},
            content=b"retry-image",
        )

    event = {
        "message": [
            {
                "type": "image",
                "data": {
                    "file": "retry-image.jpg",
                    "url": "https://multimedia.example/download?fileid=retry",
                },
            }
        ]
    }

    async def run() -> list[dict[str, str]]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=2
        ) as client:
            return await AiImageService(client).collect(event)

    images = asyncio.run(run())
    assert attempts == 4
    assert images == [
        {
            "media_type": "image/jpeg",
            "data": base64.b64encode(b"retry-image").decode("ascii"),
        }
    ]


def test_provider_multimodal_payloads() -> None:
    """OpenAI、Anthropic、Gemini 分别发送各自的图片协议。"""
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.append({"url": str(request.url), "body": body})
        if "anthropic" in request.url.host:
            return httpx.Response(200, json={"content": [{"type": "text", "text": "A"}]})
        if "googleapis" in request.url.host:
            return httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": "G"}]}}]},
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "O"}}]})

    image = {"media_type": "image/jpeg", "data": "AQI="}

    async def run() -> list[str]:
        provider = AiProviderClient()
        await provider.http_client.aclose()
        provider.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=2
        )
        common = {
            "api_key": "secret",
            "system_prompt": "请识别图片",
            "temperature": 0.7,
            "max_tokens": 100,
        }
        results = [
            await provider.generate(
                {
                    **common,
                    "provider_type": "openai_compatible",
                    "base_url": "https://ai.example/v1",
                    "model_name": "vision-model",
                },
                "图片里有什么？",
                images=[image],
            ),
            await provider.generate(
                {
                    **common,
                    "provider_type": "anthropic",
                    "base_url": "https://api.anthropic.com",
                    "model_name": "claude-vision",
                },
                "图片里有什么？",
                images=[image],
            ),
            await provider.generate(
                {
                    **common,
                    "provider_type": "gemini",
                    "base_url": "https://generativelanguage.googleapis.com",
                    "model_name": "gemini-vision",
                },
                "图片里有什么？",
                images=[image],
            ),
        ]
        await provider.close()
        return results

    assert asyncio.run(run()) == ["O", "A", "G"]
    openai_content = captured[0]["body"]["messages"][-1]["content"]
    assert openai_content[1]["type"] == "image_url"
    assert openai_content[1]["image_url"]["url"] == "data:image/jpeg;base64,AQI="
    anthropic_content = captured[1]["body"]["messages"][-1]["content"]
    assert anthropic_content[1]["type"] == "image"
    assert anthropic_content[1]["source"]["data"] == "AQI="
    gemini_parts = captured[2]["body"]["contents"][-1]["parts"]
    assert gemini_parts[1]["inline_data"] == {
        "mime_type": "image/jpeg",
        "data": "AQI=",
    }


def test_ai_empty_content_retries_before_success() -> None:
    """AI 平台成功响应但内容为空时，重新请求并使用后续有效回复。"""
    async def run() -> None:
        provider = AiProviderClient()
        await provider.http_client.aclose()
        attempts = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "finish_reason": "length",
                                "message": {
                                    "content": "",
                                    "reasoning_content": "思考中",
                                },
                            }
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "重试成功"}}]},
            )

        provider.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=1
        )
        result = await provider.generate(
            {
                "provider_type": "openai_compatible",
                "base_url": "https://gpt.api456.me/v1",
                "api_key": "secret",
                "model_name": "test-model",
                "temperature": 0.7,
                "max_tokens": 100,
            },
            "测试",
        )
        assert result == "重试成功"
        assert attempts == 2
        await provider.close()

    asyncio.run(run())


def test_ai_required_complete_response_retries_truncated_content() -> None:
    """严格输出场景即使已有部分正文，遇到 length 也必须重新请求。"""
    async def run() -> None:
        provider = AiProviderClient()
        await provider.http_client.aclose()
        attempts = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "finish_reason": "length",
                                "message": {
                                    "content": '{"reply_type":"generate_image",',
                                    "reasoning_content": "思考中",
                                },
                            }
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": '{"reply_type":"other"}'},
                        }
                    ]
                },
            )

        provider.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=1
        )
        result = await provider.generate(
            {
                "provider_type": "openai_compatible",
                "base_url": "https://gpt.api456.me/v1",
                "api_key": "secret",
                "model_name": "test-model",
                "temperature": 0.0,
                "max_tokens": 2000,
                "_require_complete_response": True,
            },
            "只返回严格 JSON",
        )
        assert result == '{"reply_type":"other"}'
        assert attempts == 2
        await provider.close()

    asyncio.run(run())


def test_ai_empty_content_exhausts_retries() -> None:
    """AI 平台连续返回空内容时，重试耗尽后返回明确业务错误。"""
    async def run() -> None:
        provider = AiProviderClient()
        await provider.http_client.aclose()
        attempts = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": ""}}]},
            )

        provider.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=1
        )
        with pytest.raises(BusinessError, match="空内容") as caught:
            await provider.generate(
                {
                    "provider_type": "openai_compatible",
                    "base_url": "https://gpt.api456.me/v1",
                    "api_key": "secret",
                    "model_name": "test-model",
                    "temperature": 0.7,
                    "max_tokens": 100,
                },
                "测试",
            )
        assert "已重试2次" in str(caught.value)
        assert attempts == 3
        await provider.close()

    asyncio.run(run())


def test_dashscope_image_input_returns_business_error() -> None:
    """DashScope 应用不支持视觉输入时返回明确业务错误。"""
    async def run() -> None:
        provider = AiProviderClient()
        with pytest.raises(BusinessError, match="不支持图片输入"):
            await provider.generate(
                {
                    "provider_type": "dashscope_app",
                    "base_url": "https://dashscope.aliyuncs.com/api/v1/apps/app/completion",
                    "api_key": "secret",
                    "model_name": "",
                    "temperature": 0.7,
                    "max_tokens": 100,
                },
                "分析图片",
                images=[{"media_type": "image/jpeg", "data": "AQI="}],
            )
        await provider.close()

    asyncio.run(run())


def test_triggered_http_event_sends_raw_record_image_to_ai(tmp_path: Path) -> None:
    """开启 AI 且消息 @ 机器人时，将 raw.records 图片一并发送。"""
    image_path = tmp_path / "quoted.jpg"
    image_path.write_bytes(b"event-image")
    settings = build_settings(tmp_path)
    captured: list[dict] = []

    def ai_handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if _is_intent_request(payload):
            return _intent_response()
        captured.append(payload)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "看到了"}}]}
        )

    with TestClient(create_app(settings)) as client:
        headers = _headers(client)
        _create_bot(client, headers, bot_qq=BOT_QQ)
        client.put(
            f"/api/bots/{BOT_QQ}/ai",
            headers=headers,
            json=_ai_payload(image_model_name="vision-model"),
        )
        provider = client.app.state.container.bot_ai_service.provider
        client.portal.call(provider.http_client.aclose)
        provider.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(ai_handler), timeout=2
        )
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": int(BOT_QQ),
            "message_id": 6001,
            "user_id": 8899,
            "group_id": 7788,
            "message": [
                {"type": "at", "data": {"qq": BOT_QQ}},
                {"type": "text", "data": {"text": " 这个图片你能看到吗"}},
            ],
            "raw": {
                "records": [
                    {
                        "elements": [
                            {"picElement": {"sourcePath": str(image_path)}}
                        ]
                    }
                ]
            },
        }
        raw = json.dumps(event, ensure_ascii=False).encode("utf-8")
        signature = "sha1=" + hmac.new(
            settings.napcat_token.encode(), raw, hashlib.sha1
        ).hexdigest()
        response = client.post(
            "/onebot/v11/events",
            content=raw,
            headers={"x-signature": signature},
        ).json()

    assert response == {"reply": "看到了"}
    assert captured[0]["model"] == "vision-model"
    content = captured[0]["messages"][-1]["content"]
    assert content[0] == {"type": "text", "text": "这个图片你能看到吗"}
    assert content[1]["image_url"]["url"].endswith("ZXZlbnQtaW1hZ2U=")
