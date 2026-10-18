"""机器人 AI 配置、多平台调用和消息触发流程测试。"""

import asyncio
import base64
import hashlib
import hmac
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.common.responses import BusinessError
from app.services.ai_message_intent import DEFAULT_MESSAGE_INTENT_PROMPT
from app.services.ai_provider_service import AiProviderClient
from test_service import build_settings, login


BOT_QQ = "123456"


def _headers(client: TestClient) -> dict[str, str]:
    return {"Authorization": f"Bearer {login(client, 'admin', 'admin-password')}"}


def _create_bot(client: TestClient, headers: dict[str, str], **overrides) -> None:
    payload = {"bot_qq": BOT_QQ, "group_mode": "all", "is_enabled": True}
    payload.update(overrides)
    assert client.post("/api/bots", headers=headers, json=payload).json()["success"]


def _ai_payload(**overrides) -> dict:
    payload = {
        "is_enabled": True,
        "provider_type": "openai_compatible",
        "base_url": "https://ai.example/v1",
        "api_key": "ai-secret",
        "model_name": "test-model",
        "image_model_name": "",
        "system_prompt": "简洁回答",
        "temperature": 0.4,
        "max_tokens": 300,
        "followup_count": 3,
        "history_limit": 10,
        "image_reply_probability": 100,
    }
    payload.update(overrides)
    return payload


def _post_event(client: TestClient, settings, event: dict) -> dict:
    raw = json.dumps(event, ensure_ascii=False).encode("utf-8")
    signature = "sha1=" + hmac.new(
        settings.napcat_token.encode(), raw, hashlib.sha1
    ).hexdigest()
    return client.post(
        "/onebot/v11/events",
        content=raw,
        headers={"x-signature": signature},
    ).json()


def _install_ai_transport(client: TestClient, handler) -> None:
    service = client.app.state.container.bot_ai_service.provider
    client.portal.call(service.http_client.aclose)
    service.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=2
    )


def _is_intent_request(payload: dict) -> bool:
    """判断一次测试模型请求是否为严格消息意图任务。"""
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return False
    content = messages[-1].get("content") if isinstance(messages[-1], dict) else ""
    if isinstance(content, list):
        content = next(
            (
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ),
            "",
        )
    return str(content).startswith("你是 QQ 消息审核与回复形式意图判断器")


def _intent_response(**updates) -> httpx.Response:
    """返回符合新协议的严格意图模型响应。"""
    intent = {
        "mute": False,
        "mute_reason": "",
        "mute_keyword": "",
        "reply_type": "other",
        "emoji_category": "",
        "generate_words": "",
        "reason": "普通文本回复",
    }
    intent.update(updates)
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "content": json.dumps(intent, ensure_ascii=False)
                    }
                }
            ]
        },
    )


def test_ai_network_error_has_diagnostic() -> None:
    """网络异常不再输出空错误，并向调用方返回明确中文提示。"""
    async def run() -> None:
        provider = AiProviderClient()
        await provider.http_client.aclose()
        attempts = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            raise httpx.ReadTimeout("")

        provider.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=1
        )
        with pytest.raises(BusinessError) as caught:
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
        assert "请求超时" in str(caught.value)
        assert "已重试10次" in str(caught.value)
        assert attempts == 11
        await provider.close()

    asyncio.run(run())


def test_ai_http_429_retries_before_success() -> None:
    """AI 平台返回 429 时按 Retry-After 重试，临时限流恢复后继续生成。"""
    async def run() -> None:
        provider = AiProviderClient()
        await provider.http_client.aclose()
        attempts = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                return httpx.Response(
                    429,
                    headers={"Retry-After": "0"},
                    json={"error": {"message": "rate limited"}},
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
        assert attempts == 3
        await provider.close()

    asyncio.run(run())


def test_ai_http_503_waits_after_ten_retries_and_continues() -> None:
    """连续 503 完成一轮十次重试后等待 2 秒，下一轮恢复后继续请求。"""
    async def run() -> None:
        provider = AiProviderClient()
        await provider.http_client.aclose()
        attempts = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts <= 11:
                return httpx.Response(503, json={"error": {"message": "暂时不可用"}})
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "第二轮恢复"}}]},
            )

        provider.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=1
        )
        with patch(
            "app.services.ai_provider_service.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep:
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
        assert result == "第二轮恢复"
        assert attempts == 12
        assert [call.args[0] for call in sleep.await_args_list] == [
            0.5,
            1.0,
            1.5,
            2.0,
            2.5,
            3.0,
            3.5,
            4.0,
            4.5,
            5.0,
            2,
        ]
        await provider.close()

    asyncio.run(run())


def test_ai_http_503_stops_after_two_retry_rounds() -> None:
    """连续 503 达到两轮重试上限后停止请求并返回业务错误。"""
    async def run() -> None:
        provider = AiProviderClient()
        await provider.http_client.aclose()
        attempts = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(503, json={"error": {"message": "暂时不可用"}})

        provider.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=1
        )
        with patch(
            "app.services.ai_provider_service.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            with pytest.raises(BusinessError) as caught:
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
        assert caught.value.code == 50270
        assert "已重试22次" in str(caught.value)
        assert attempts == 22
        await provider.close()

    asyncio.run(run())


def test_ai_retry_refreshes_latest_provider_config() -> None:
    """HTTP 重试前重新读取配置，并使用新的地址、密钥和模型发送请求。"""
    async def run() -> None:
        provider = AiProviderClient()
        await provider.http_client.aclose()
        requests: list[dict[str, object]] = []
        refresh_calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(
                {
                    "url": str(request.url),
                    "authorization": request.headers.get("Authorization", ""),
                    "body": json.loads(request.content),
                }
            )
            if len(requests) == 1:
                return httpx.Response(503, json={"error": {"message": "暂时不可用"}})
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "刷新成功"}}]},
            )

        async def refresh_config() -> dict[str, object]:
            nonlocal refresh_calls
            refresh_calls += 1
            return {
                "provider_type": "openai_compatible",
                "base_url": "https://new.ai.example/v2",
                "api_key": "new-secret",
                "model_name": "new-model",
                "system_prompt": "新提示",
                "temperature": 0.2,
                "max_tokens": 200,
            }

        provider.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=1
        )
        with patch(
            "app.services.ai_provider_service.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await provider.generate(
                {
                    "provider_type": "openai_compatible",
                    "base_url": "https://old.ai.example/v1",
                    "api_key": "old-secret",
                    "model_name": "old-model",
                    "system_prompt": "旧提示",
                    "temperature": 0.7,
                    "max_tokens": 100,
                },
                "测试",
                refresh_config=refresh_config,
            )
        assert result == "刷新成功"
        assert refresh_calls == 1
        assert requests[0]["url"] == "https://old.ai.example/v1/chat/completions"
        assert requests[1]["url"] == "https://new.ai.example/v2/chat/completions"
        assert requests[1]["authorization"] == "Bearer new-secret"
        assert requests[1]["body"]["model"] == "new-model"
        assert requests[0]["body"]["messages"][0]["content"] == "旧提示"
        assert requests[1]["body"]["messages"][0]["content"] == "新提示"
        await provider.close()

    asyncio.run(run())


def test_anthropic_gemini_and_dashscope_generation_protocols() -> None:
    """Anthropic、Gemini 和 DashScope 应用按各自协议发送并提取文本。"""
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": json.loads(request.content),
            }
        )
        if "anthropic" in request.url.host:
            return httpx.Response(200, json={"content": [{"type": "text", "text": "Claude回答"}]})
        if "googleapis" in request.url.host:
            return httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": "Gemini回答"}]}}]},
            )
        return httpx.Response(200, json={"output": {"text": "百炼回答"}})

    async def run() -> list[str]:
        provider = AiProviderClient()
        await provider.http_client.aclose()
        provider.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=2
        )
        common = {
            "api_key": "secret",
            "system_prompt": "系统提示",
            "temperature": 0.6,
            "max_tokens": 200,
        }
        results = [
            await provider.generate(
                {
                    **common,
                    "provider_type": "anthropic",
                    "base_url": "https://api.anthropic.com",
                    "model_name": "claude-test",
                },
                "问题",
            ),
            await provider.generate(
                {
                    **common,
                    "provider_type": "gemini",
                    "base_url": "https://generativelanguage.googleapis.com",
                    "model_name": "gemini-test",
                },
                "问题",
            ),
            await provider.generate(
                {
                    **common,
                    "provider_type": "dashscope_app",
                    "base_url": "https://dashscope.aliyuncs.com/api/v1/apps/app-test/completion",
                    "model_name": "",
                },
                "问题",
            ),
        ]
        await provider.close()
        return results

    assert asyncio.run(run()) == ["Claude回答", "Gemini回答", "百炼回答"]
    assert captured[0]["url"] == "https://api.anthropic.com/v1/messages"
    assert captured[0]["headers"]["x-api-key"] == "secret"
    assert captured[1]["url"].startswith(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent?key=secret"
    )
    assert captured[2]["body"]["input"]["prompt"].endswith("用户：问题")


def test_ai_config_is_masked_and_supports_models_and_test(tmp_path: Path) -> None:
    """AI Key 加密入库且授权配置读取可回显，支持模型查询和连接测试。"""
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
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"data": [{"id": "model-a"}, {"id": "model-b"}]},
            )
        if request.url.path.endswith("/images/generations"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "b64_json": base64.b64encode(
                                b"\x89PNG\r\n\x1a\nimage"
                            ).decode("ascii")
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "测试成功"}}]},
        )

    with TestClient(create_app(build_settings(tmp_path))) as client:
        headers = _headers(client)
        _create_bot(client, headers)
        initial = client.get(f"/api/bots/{BOT_QQ}/ai", headers=headers).json()
        assert initial["success"] is True
        assert initial["data"]["history_limit"] == 10
        assert initial["data"]["image_reply_probability"] == 100
        assert initial["data"]["message_intent_prompt"] == DEFAULT_MESSAGE_INTENT_PROMPT
        saved = client.put(
            f"/api/bots/{BOT_QQ}/ai",
            headers=headers,
            json=_ai_payload(
                followup_count=5,
                history_limit=5,
                image_reply_probability=35,
                image_model_name="vision-model",
                message_intent_prompt="用户要求惊喜时优先选择表情图片。",
            ),
        ).json()
        assert saved["success"] is True
        assert saved["data"]["api_key"] == ""
        assert saved["data"]["has_api_key"] is True

        loaded = client.get(f"/api/bots/{BOT_QQ}/ai", headers=headers).json()
        assert loaded["data"]["api_key"] == "ai-secret"
        assert loaded["data"]["followup_count"] == 5
        assert loaded["data"]["history_limit"] == 5
        assert loaded["data"]["image_reply_probability"] == 35
        assert loaded["data"]["image_model_name"] == "vision-model"
        assert loaded["data"]["message_intent_prompt"] == "用户要求惊喜时优先选择表情图片。"

        _install_ai_transport(client, handler)
        form = _ai_payload(
            api_key="",
            image_provider_type="openai_compatible",
            image_base_url="https://vision.example/v1",
            image_api_key="vision-secret",
            image_model_name="vision-test-model",
        )
        models = client.post(
            f"/api/bots/{BOT_QQ}/ai/models", headers=headers, json=form
        ).json()
        image_models = client.post(
            f"/api/bots/{BOT_QQ}/ai/models",
            headers=headers,
            params={"model_type": "image"},
            json=form,
        ).json()
        tested = client.post(
            f"/api/bots/{BOT_QQ}/ai/test", headers=headers, json=form
        ).json()
        image_tested = client.post(
            f"/api/bots/{BOT_QQ}/ai/test",
            headers=headers,
            params={"model_type": "image"},
            json=form,
        ).json()
        generation_form = _ai_payload(
            api_key="",
            image_generation_provider_type="openai_compatible",
            image_generation_base_url="https://generation.example/v1",
            image_generation_api_key="generation-secret",
            image_generation_model_name="image-test-model",
        )
        generation_models = client.post(
            f"/api/bots/{BOT_QQ}/ai/models",
            headers=headers,
            params={"model_type": "generation"},
            json=generation_form,
        ).json()
        generation_tested = client.post(
            f"/api/bots/{BOT_QQ}/ai/test",
            headers=headers,
            params={"model_type": "generation"},
            json=generation_form,
        ).json()
        assert models["data"] == [
            {"value": "model-a", "label": "model-a"},
            {"value": "model-b", "label": "model-b"},
        ]
        assert image_models["data"] == models["data"]
        assert generation_models["data"] == models["data"]
        assert tested["data"]["reply"] == "测试成功"
        assert image_tested["data"]["reply"] == "测试成功"
        assert generation_tested["data"]["reply"] == "文生图测试成功，已返回 image/png 图片"
        assert generation_tested["data"]["image"]["media_type"] == "image/png"
        assert generation_tested["data"]["image"]["data"]
        assert requests[0]["url"] == "https://ai.example/v1/models"
        assert requests[0]["authorization"] == "Bearer ai-secret"
        assert requests[1]["url"] == "https://vision.example/v1/models"
        assert requests[1]["authorization"] == "Bearer vision-secret"
        assert requests[2]["body"]["model"] == "test-model"
        assert requests[2]["authorization"] == "Bearer ai-secret"
        assert requests[3]["body"]["model"] == "vision-test-model"
        assert requests[3]["authorization"] == "Bearer vision-secret"
        image_content = requests[3]["body"]["messages"][-1]["content"]
        image_part = next(
            item for item in image_content if item.get("type") == "image_url"
        )
        image_data = base64.b64decode(
            image_part["image_url"]["url"].split(",", 1)[1]
        )
        assert image_data[16:24] == (32).to_bytes(4, "big") * 2
        assert requests[4]["url"] == "https://generation.example/v1/models"
        assert requests[4]["authorization"] == "Bearer generation-secret"
        assert requests[5]["body"]["model"] == "image-test-model"
        assert requests[5]["authorization"] == "Bearer generation-secret"


def test_generate_flatten_history_drops_assistant_role() -> None:
    """群聊拼接模式把历史压成单段转录，请求里不再出现 assistant 角色。"""
    async def run() -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content)
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "好的"}}]}
            )

        provider = AiProviderClient()
        await provider.http_client.aclose()
        provider.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=2
        )
        result = await provider.generate(
            {
                "provider_type": "openai_compatible",
                "base_url": "https://ai.example/v1",
                "api_key": "secret",
                "model_name": "test-model",
                "system_prompt": "你叫小美",
                "temperature": 0.7,
                "max_tokens": 100,
            },
            "现在几点了",
            history=[
                {"role": "user", "content": "张三：在吗"},
                {"role": "assistant", "content": "在的哦"},
            ],
            flatten_history=True,
        )
        await provider.close()
        assert result == "好的"
        messages = captured["payload"]["messages"]
        roles = [item["role"] for item in messages]
        # 只保留 system 与当前 user 两个真实角色，不得回灌 assistant。
        assert roles == ["system", "user"]
        last = messages[-1]["content"]
        assert "我：在的哦" in last
        assert "张三：在吗" in last
        assert last.endswith("现在几点了")

    asyncio.run(run())


def test_generate_default_keeps_dual_role_history() -> None:
    """默认（私聊）模式保持 user/assistant 双角色历史不变。"""
    async def run() -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content)
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "嗯"}}]}
            )

        provider = AiProviderClient()
        await provider.http_client.aclose()
        provider.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=2
        )
        await provider.generate(
            {
                "provider_type": "openai_compatible",
                "base_url": "https://ai.example/v1",
                "api_key": "secret",
                "model_name": "test-model",
                "temperature": 0.7,
                "max_tokens": 100,
            },
            "在吗",
            history=[{"role": "assistant", "content": "上次回答"}],
        )
        await provider.close()
        roles = [item["role"] for item in captured["payload"]["messages"]]
        assert "assistant" in roles

    asyncio.run(run())


def test_history_strips_image_cq_segments() -> None:
    """历史文本过滤掉图片 CQ 码，仅保留文字语境。"""
    from app.services.chat_message_service import _strip_history_images

    assert _strip_history_images("看这张[CQ:image,file=a.jpg,url=http://x]图") == "看这张图"
    assert _strip_history_images("[CQ:image,file=b.png]") == ""
    assert _strip_history_images("纯文字") == "纯文字"


def test_http_rule_priority_and_ai_group_triggers(tmp_path: Path) -> None:
    """规则优先于 AI，群 AI 仅处理 @机器人或引用机器人消息。"""
    settings = build_settings(tmp_path)
    ai_prompts: list[str] = []

    def ai_handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        prompt = payload["messages"][-1]["content"]
        if _is_intent_request(payload):
            return _intent_response()
        ai_prompts.append(prompt)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "AI回答"}}]},
        )

    def napcat_handler(request: httpx.Request) -> httpx.Response:
        message_id = json.loads(request.content)["message_id"]
        sender_id = BOT_QQ if str(message_id) == "10" else "9988"
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "retcode": 0,
                "data": {
                    "sender": {"user_id": sender_id},
                    "message": [{"type": "text", "data": {"text": "之前的回答"}}],
                },
            },
        )

    with TestClient(create_app(settings)) as client:
        headers = _headers(client)
        _create_bot(
            client,
            headers,
            connection_mode="http",
            http_url="http://napcat.example:3000",
        )
        client.put(f"/api/bots/{BOT_QQ}/ai", headers=headers, json=_ai_payload())
        client.post(
            "/api/auto_replies",
            headers=headers,
            json={
                "keyword": "固定规则",
                "bot_qq": BOT_QQ,
                "reply_content": "规则回答",
            },
        )
        _install_ai_transport(client, ai_handler)
        onebot = client.app.state.container.onebot_service
        client.portal.call(onebot.http_client.aclose)
        onebot.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(napcat_handler), timeout=2
        )

        base = {
            "post_type": "message",
            "message_type": "group",
            "self_id": int(BOT_QQ),
            "user_id": 88,
            "group_id": 99,
        }
        rule_event = {
            **base,
            "raw_message": "固定规则",
            "message": [
                {"type": "text", "data": {"text": "固定规则"}},
            ],
        }
        assert _post_event(client, settings, rule_event) == {"reply": "规则回答"}
        assert ai_prompts == []

        plain_event = {
            **base,
            "raw_message": "普通群消息",
            "message": [{"type": "text", "data": {"text": "普通群消息"}}],
        }
        assert _post_event(client, settings, plain_event) == {}
        assert ai_prompts == []

        at_event = {
            **base,
            "message": [
                {"type": "at", "data": {"qq": BOT_QQ}},
                {"type": "text", "data": {"text": "  请回答  "}},
            ],
        }
        assert _post_event(client, settings, at_event) == {"reply": "AI回答"}
        # 群聊历史被拼成转录前缀，当前消息仍在提示词末尾。
        assert len(ai_prompts) == 1
        assert ai_prompts[-1].endswith("请回答")

        quote_event = {
            **base,
            "message": [
                {"type": "reply", "data": {"id": "10"}},
                {"type": "text", "data": {"text": "继续说"}},
            ],
        }
        assert _post_event(client, settings, quote_event) == {"reply": "AI回答"}
        assert ai_prompts[-1].endswith("继续说")

        other_quote = {
            **base,
            "message": [
                {"type": "reply", "data": {"id": "11"}},
                {"type": "text", "data": {"text": "不是机器人"}},
            ],
        }
        assert _post_event(client, settings, other_quote) == {}
        assert len(ai_prompts) == 2

        private_event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": int(BOT_QQ),
            "user_id": 88,
            "message": [{"type": "text", "data": {"text": "私聊问题"}}],
        }
        assert _post_event(client, settings, private_event) == {"reply": "AI回答"}
        assert ai_prompts[-1] == "私聊问题"


def test_group_mention_without_text_triggers_ai(tmp_path: Path) -> None:
    """仅 @ 机器人而没有正文时，使用默认提示词触发 AI。"""
    calls: list[dict] = []

    def ai_handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "不应出现"}}]},
        )

    settings = build_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = _headers(client)
        _create_bot(client, headers)
        client.put(f"/api/bots/{BOT_QQ}/ai", headers=headers, json=_ai_payload())
        _install_ai_transport(client, ai_handler)
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": int(BOT_QQ),
            "user_id": 88,
            "group_id": 99,
            "raw_message": f"[CQ:at,qq={BOT_QQ}]",
            "message": [
                {"type": "at", "data": {"qq": BOT_QQ}},
                {"type": "text", "data": {"text": " \u200b"}},
            ],
        }
        assert _post_event(client, settings, event) == {"reply": "不应出现"}
        assert len(calls) == 1


def test_websocket_quoted_bot_ai_reply_does_not_block_receive_loop(
    tmp_path: Path,
) -> None:
    """WS 引用校验在后台执行，get_msg 响应可被主收包循环正常关联。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        headers = _headers(client)
        _create_bot(client, headers)
        client.put(f"/api/bots/{BOT_QQ}/ai", headers=headers, json=_ai_payload())

        def ai_handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "WS AI回答"}}]},
            )

        _install_ai_transport(client, ai_handler)
        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer napcat-secret", "X-Self-ID": BOT_QQ},
        ) as napcat:
            napcat.send_json(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "self_id": int(BOT_QQ),
                    "user_id": 88,
                    "group_id": 99,
                    "message_id": 21,
                    "message": [
                        {"type": "reply", "data": {"id": "20"}},
                        {"type": "text", "data": {"text": "继续"}},
                    ],
                }
            )
            get_msg = napcat.receive_json()
            assert get_msg["action"] == "get_msg"
            napcat.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {
                        "sender": {"user_id": int(BOT_QQ)},
                        "message": [{"type": "text", "data": {"text": "上文"}}],
                    },
                    "echo": get_msg["echo"],
                }
            )
            reply = napcat.receive_json()
            assert reply["action"] == "send_group_msg"
            assert reply["params"]["message"] == [
                {"type": "reply", "data": {"id": "21"}},
                {"type": "text", "data": {"text": "WS AI回答"}},
            ]
            napcat.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"message_id": 21},
                    "echo": reply["echo"],
                }
            )


def test_websocket_poke_bot_ai_reply(tmp_path: Path) -> None:
    """WS 戳一戳机器人事件会进入 AI 回复并发送到原群。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        headers = _headers(client)
        _create_bot(client, headers)
        client.put(f"/api/bots/{BOT_QQ}/ai", headers=headers, json=_ai_payload())

        def ai_handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "收到啦"}}]},
            )

        _install_ai_transport(client, ai_handler)
        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer napcat-secret", "X-Self-ID": BOT_QQ},
        ) as napcat:
            napcat.send_json(
                {
                    "post_type": "notice",
                    "notice_type": "notify",
                    "sub_type": "poke",
                    "self_id": int(BOT_QQ),
                    "user_id": 88,
                    "group_id": 99,
                    "target_id": int(BOT_QQ),
                }
            )
            reply = napcat.receive_json()
            assert reply["action"] == "send_group_msg"
            assert reply["params"] == {"group_id": 99, "message": "收到啦"}
            napcat.send_json(
                {"status": "ok", "retcode": 0, "data": {}, "echo": reply["echo"]}
            )



def test_websocket_followup_ai_reply_does_not_quote_current_message(tmp_path: Path) -> None:
    """Redis 免触发的后续 AI 回复不引用当前群消息。"""
    with TestClient(create_app(build_settings(tmp_path))) as client:
        headers = _headers(client)
        _create_bot(client, headers)
        client.put(f"/api/bots/{BOT_QQ}/ai", headers=headers, json=_ai_payload())
        service = client.app.state.container.onebot_service
        service.ai_followup_service.consume = AsyncMock(return_value=True)

        def ai_handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "继续回答"}}]},
            )

        _install_ai_transport(client, ai_handler)
        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer napcat-secret", "X-Self-ID": BOT_QQ},
        ) as napcat:
            napcat.send_json(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "self_id": int(BOT_QQ),
                    "user_id": 88,
                    "group_id": 99,
                    "message_id": 22,
                    "message": [{"type": "text", "data": {"text": "继续"}}],
                }
            )
            reply = napcat.receive_json()
            assert reply["action"] == "send_group_msg"
            assert reply["params"]["message"] == "继续回答"
            napcat.send_json(
                {"status": "ok", "retcode": 0, "data": {}, "echo": reply["echo"]}
            )
