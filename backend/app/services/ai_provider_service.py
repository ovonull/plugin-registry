"""多平台 AI 模型查询、连接测试与文本生成客户端。"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.common.responses import BusinessError
from app.services.ai_image_service import AiImage
from app.services.ai_multimodal import (
    build_openai_content,
    gemini_parts,
    messages_have_images,
    to_anthropic_message,
)


logger = logging.getLogger("uvicorn.error")
DEFAULT_BASE_URLS = {
    "openai_compatible": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com",
    "dashscope_app": "https://dashscope.aliyuncs.com/api/v1/apps/{app_id}/completion",
}
PROVIDER_TYPES = frozenset(DEFAULT_BASE_URLS)
MAX_NETWORK_RETRIES = 10
MAX_STATUS_RETRY_ROUNDS = 2
MAX_EMPTY_CONTENT_RETRIES = 2
RETRY_BACKOFF_SECONDS = 0.5
RETRY_BATCH_WAIT_SECONDS = 2.0
RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
MAX_RETRY_AFTER_SECONDS = 30.0
ConfigRefresher = Callable[[], Awaitable[dict[str, Any]]]
RequestRefresher = Callable[
    [], Awaitable[tuple[str, dict[str, str], dict[str, Any]]]
]


def openai_url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    for suffix in ("/chat/completions", "/images/generations", "/models"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return f"{base}/{path.lstrip('/')}"


def versioned_url(base_url: str, version: str, path: str) -> str:
    base = base_url.rstrip("/")
    path = path.lstrip("/")
    if base.endswith(f"/{path}"):
        return base
    if base.endswith(f"/{version}"):
        return f"{base}/{path}"
    return f"{base}/{version}/{path}"


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return _truncate(response.text or f"HTTP {response.status_code}")
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return _truncate(str(error.get("message") or error.get("type") or error))
        if error:
            return _truncate(str(error))
        for key in ("message", "msg", "detail"):
            if body.get(key):
                return _truncate(str(body[key]))
    return _truncate(str(body))


def _truncate(value: str, limit: int = 2000) -> str:
    """限制日志和错误响应长度，避免第三方返回内容撑爆日志。"""
    value = str(value)
    if len(value) <= limit:
        return value
    return f"{value[:limit]}…"


def _network_error_message(error: httpx.HTTPError) -> str:
    """将 HTTP 客户端异常转换为明确的中文诊断。"""
    if isinstance(error, httpx.TimeoutException):
        return "请求超时"
    if isinstance(error, httpx.ConnectError):
        return "无法连接"
    if isinstance(error, httpx.NetworkError):
        return "网络通信失败"
    return "请求失败"


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    """计算 HTTP 状态码重试等待时间，优先遵循 Retry-After。"""
    retry_after = response.headers.get("Retry-After", "").strip()
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), MAX_RETRY_AFTER_SECONDS))
        except ValueError:
            pass
    return RETRY_BACKOFF_SECONDS * (attempt + 1)


def _request_log_context(
    config: dict[str, Any], messages: list[dict[str, Any]]
) -> str:
    """构造 AI 请求日志上下文，把每一次请求（含重试）归属到具体机器人和请求规模。

    用于排查“同一 AI 接口、不同机器人结果不一致”一类问题：定位到底是哪个
    机器人、哪个模型、多大的请求（是否带图）在反复失败。

    Args:
        config: 当前运行时 AI 配置，至少包含 bot_qq、model_name。
        messages: OpenAI 兼容格式的消息列表（纯文本为 str，多模态为内容块列表）。
    Returns:
        形如 "bot=xxx，model=xxx，消息数=n，文本字符=n，图片数=n(约nKB)" 的日志片段。
    """
    bot_qq = str(config.get("bot_qq") or "").strip() or "-"
    model = str(config.get("model_name") or "").strip() or "-"
    text_chars = 0
    image_count = 0
    image_b64_chars = 0
    for item in messages:
        content = item.get("content")
        if isinstance(content, str):
            text_chars += len(content)
            continue
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type == "text":
                text_chars += len(str(part.get("text") or ""))
            elif part_type in {"image_url", "image"}:
                image_count += 1
                image_url = part.get("image_url")
                if isinstance(image_url, dict):
                    url = str(image_url.get("url") or "")
                    # base64 数据长度约为字节数的 4/3，据此估算图片体积
                    image_b64_chars += len(url.partition(";base64,")[2])
    image_kb = image_b64_chars * 3 // 4 // 1024
    return (
        f"bot={bot_qq}，model={model}，消息数={len(messages)}，"
        f"文本字符={text_chars}，图片数={image_count}(约{image_kb}KB)"
    )


def _clean_content(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [
            str(item.get("text", ""))
            for item in value
            if isinstance(item, dict) and item.get("text")
        ]
        return "".join(parts).strip()
    return ""


def _messages_with_current_system_prompt(
    messages: list[dict[str, Any]], config: dict[str, Any]
) -> list[dict[str, Any]]:
    """按最新配置替换系统提示词，保留本次调用的用户消息和历史。"""
    non_system = [item for item in messages if item.get("role") != "system"]
    system_prompt = str(config.get("system_prompt") or "").strip()
    if not system_prompt:
        return non_system
    return [{"role": "system", "content": system_prompt}, *non_system]


def _build_history_transcript(history: list[dict[str, Any]]) -> str:
    """把多角色历史压成单段群聊转录文本，机器人自身发言用“我：”标注。

    群聊里 user 角色无法区分不同群友，assistant 历史又会被部分中转平台
    错误地转成 output_text 而导致上游 400，因此群场景统一拼成一段转录，
    只保留 system 与当前消息两个真实角色。

    Args:
        history: 已按 user/assistant 标注、时间正序的历史消息。
    Returns:
        每行“说话人：内容”的转录文本，历史为空时返回空字符串。
    """
    lines: list[str] = []
    for item in history:
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        # 历史正文已包含发送者资料，机器人自身仍加“我：”标记。
        lines.append(f"我：{content}" if item.get("role") == "assistant" else content)
    return "\n".join(lines)


class AiProviderClient:
    """按配置的平台协议调用 AI，并统一输出纯文本。"""

    def __init__(self) -> None:
        self.http_client = httpx.AsyncClient(timeout=60)

    async def close(self) -> None:
        """关闭第三方 AI HTTP 连接池。"""
        await self.http_client.aclose()

    @staticmethod
    def validate(config: dict[str, Any], require_model: bool = True) -> None:
        """检查一次第三方调用所需的完整配置。"""
        provider = str(config.get("provider_type") or "")
        base_url = str(config.get("base_url") or "").strip()
        api_key = str(config.get("api_key") or "").strip()
        model = str(config.get("model_name") or "").strip()
        if provider not in PROVIDER_TYPES:
            raise BusinessError("AI 平台类型不支持", 40071)
        missing = []
        if not base_url:
            missing.append("API 地址")
        if not api_key:
            missing.append("API Key")
        if require_model and provider != "dashscope_app" and not model:
            missing.append("模型名称")
        if missing:
            raise BusinessError(f"请先填写{'、'.join(missing)}", 40072)
        if provider == "dashscope_app" and (
            "{app_id}" in base_url or "/apps/" not in base_url
        ):
            raise BusinessError("请填写包含实际 app_id 的 DashScope 应用地址", 40073)

    async def list_models(self, config: dict[str, Any]) -> list[dict[str, str]]:
        """按平台协议读取模型列表并规范化为下拉选项。"""
        self.validate(config, require_model=False)
        provider = config["provider_type"]
        if provider == "dashscope_app":
            raise BusinessError("DashScope 应用不支持获取模型列表", 40074)
        key = config["api_key"]
        if provider == "anthropic":
            response = await self.request(
                "GET",
                versioned_url(config["base_url"], "v1", "models"),
                "Anthropic",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            )
            body = response.json()
            rows = body.get("data", []) if isinstance(body, dict) else []
        elif provider == "gemini":
            response = await self.request(
                "GET",
                versioned_url(config["base_url"], "v1beta", "models"),
                "Gemini",
                params={"key": key},
            )
            body = response.json()
            rows = body.get("models", []) if isinstance(body, dict) else []
            rows = [
                item for item in rows
                if not isinstance(item, dict)
                or "supportedGenerationMethods" not in item
                or "generateContent" in item.get("supportedGenerationMethods", [])
            ]
        else:
            response = await self.request(
                "GET",
                openai_url(config["base_url"], "models"),
                "OpenAI 兼容",
                headers={"Authorization": f"Bearer {key}"},
            )
            body = response.json()
            rows = body.get("data", body.get("models", [])) if isinstance(body, dict) else body
        return self._model_options(rows)

    async def test(
        self,
        config: dict[str, Any],
        images: list[AiImage] | None = None,
    ) -> str:
        """使用最小输出生成一次测试回复，可选验证多模态输入。"""
        tested = dict(config)
        tested["max_tokens"] = min(int(config.get("max_tokens") or 100), 100)
        prompt = "请简短描述这张测试图片。" if images else "你好，请回复测试成功。"
        return await self.generate(tested, prompt, images=images)

    async def generate(
        self,
        config: dict[str, Any],
        user_text: str,
        quoted_text: str = "",
        images: list[AiImage] | None = None,
        history: list[dict[str, str]] | None = None,
        refresh_config: ConfigRefresher | None = None,
        flatten_history: bool = False,
    ) -> str:
        """生成一条 QQ 回复，附带历史消息、引用内容和图片输入。

        Args:
            flatten_history: 为真时把历史压成单段群聊转录（仅 system 与当前消息
                两个真实角色），用于群聊并规避部分中转把 assistant 历史转成
                output_text 导致的上游 400；私聊保持原双角色传法。
        """
        self.validate(config)
        provider = config["provider_type"]
        image_items = images or []
        messages: list[dict[str, Any]] = []
        if flatten_history:
            current_text = user_text
            if quoted_text:
                current_text = f"（引用消息）：{quoted_text}\n{current_text}"
            transcript = _build_history_transcript(history or [])
            if transcript:
                current_text = (
                    "以下是群聊最近的聊天记录（“我：”开头的是你本人之前的发言，仅作背景参考）：\n"
                    f"{transcript}\n\n"
                    "请以设定的人物身份，只回复下面这条最新消息，不要复述上面的记录：\n"
                    f"{current_text}"
                )
            messages.append(
                {
                    "role": "user",
                    "content": build_openai_content(current_text, image_items),
                }
            )
        else:
            for item in history or []:
                role = item.get("role")
                content = str(item.get("content") or "").strip()
                if role in {"user", "assistant"} and content:
                    messages.append({"role": role, "content": content})
            if quoted_text:
                messages.append({"role": "assistant", "content": quoted_text})
            messages.append(
                {
                    "role": "user",
                    "content": build_openai_content(user_text, image_items),
                }
            )
        messages = _messages_with_current_system_prompt(messages, config)
        for attempt in range(MAX_EMPTY_CONTENT_RETRIES + 1):
            if provider == "anthropic":
                result = await self._anthropic(config, messages, refresh_config)
            elif provider == "gemini":
                result = await self._gemini(config, messages, refresh_config)
            elif provider == "dashscope_app":
                result = await self._dashscope(config, messages, refresh_config)
            else:
                result = await self._openai(config, messages, refresh_config)
            if result:
                return result
            if attempt < MAX_EMPTY_CONTENT_RETRIES:
                logger.warning(
                    "AI 平台返回空内容，将进行第 %s/%s 次重试，provider=%s",
                    attempt + 1,
                    MAX_EMPTY_CONTENT_RETRIES,
                    provider,
                )
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                if refresh_config:
                    config = await refresh_config()
                    self.validate(config)
                    provider = config["provider_type"]
                    messages = _messages_with_current_system_prompt(messages, config)
        raise BusinessError(
            f"AI 平台返回了空内容，已重试{MAX_EMPTY_CONTENT_RETRIES}次", 50272
        )

    async def _openai(
        self,
        config: dict[str, Any],
        messages: list[dict[str, Any]],
        refresh_config: ConfigRefresher | None = None,
    ) -> str:
        def build_request(current: dict[str, Any]) -> tuple[str, dict[str, str], dict[str, Any]]:
            current_messages = _messages_with_current_system_prompt(messages, current)
            return (
                openai_url(current["base_url"], "chat/completions"),
                {"Authorization": f"Bearer {current['api_key']}"},
                {"json": self._generation_payload(current, current_messages)},
            )

        request_state = build_request(config)

        async def refresh_request() -> tuple[str, dict[str, str], dict[str, Any]]:
            nonlocal request_state
            current = await refresh_config() if refresh_config else config
            request_state = build_request(current)
            return request_state

        allowed_error_statuses = (
            set() if messages_have_images(messages) else {400}
        )
        log_context = _request_log_context(config, messages)
        response = await self.request(
            "POST",
            request_state[0],
            "OpenAI 兼容",
            headers=request_state[1],
            **request_state[2],
            allowed_error_statuses=allowed_error_statuses,
            refresh_request=refresh_request if refresh_config else None,
            log_context=log_context,
        )
        if response.status_code == 400 and not messages_have_images(messages):
            request_state[2]["json"]["messages"] = [
                {**item, "content": [{"type": "text", "text": item["content"]}]}
                for item in messages
            ]
            response = await self.request(
                "POST",
                request_state[0],
                "OpenAI 兼容",
                headers=request_state[1],
                **request_state[2],
                refresh_request=refresh_request if refresh_config else None,
                log_context=log_context,
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise BusinessError(
                "OpenAI 兼容接口响应格式不正确：返回内容不是 JSON，请检查 API 地址是否填写为兼容接口（通常以 /v1 结尾）",
                50273,
            ) from exc
        try:
            choice = body["choices"][0]
            content = _clean_content(choice["message"]["content"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise BusinessError("OpenAI 兼容接口响应格式不正确", 50273) from exc
        finish_reason = str(choice.get("finish_reason") or "").strip().lower()
        if config.get("_require_complete_response") and finish_reason in {
            "length",
            "max_tokens",
        }:
            logger.warning(
                "OpenAI 兼容接口输出被截断，将丢弃不完整内容并重试，模型=%s，finish_reason=%s，内容长度=%s",
                config.get("model_name") or "",
                finish_reason,
                len(content),
            )
            return ""
        return content

    async def _anthropic(
        self,
        config: dict[str, Any],
        messages: list[dict[str, Any]],
        refresh_config: ConfigRefresher | None = None,
    ) -> str:
        current_messages = _messages_with_current_system_prompt(messages, config)
        system = "\n".join(item["content"] for item in current_messages if item["role"] == "system")
        payload = self._generation_payload(
            config,
            [
                to_anthropic_message(item)
                for item in current_messages
                if item["role"] != "system"
            ],
        )
        if system:
            payload["system"] = system
        request_state = (
            versioned_url(config["base_url"], "v1", "messages"),
            {
                "x-api-key": config["api_key"],
                "anthropic-version": "2023-06-01",
            },
            {"json": payload},
        )

        async def refresh_request() -> tuple[str, dict[str, str], dict[str, Any]]:
            current = await refresh_config() if refresh_config else config
            current_messages = _messages_with_current_system_prompt(messages, current)
            current_system = "\n".join(
                item["content"]
                for item in current_messages
                if item["role"] == "system"
            )
            current_payload = self._generation_payload(
                current,
                [
                    to_anthropic_message(item)
                    for item in current_messages
                    if item["role"] != "system"
                ],
            )
            if current_system:
                current_payload["system"] = current_system
            return (
                versioned_url(current["base_url"], "v1", "messages"),
                {
                    "x-api-key": current["api_key"],
                    "anthropic-version": "2023-06-01",
                },
                {"json": current_payload},
            )

        response = await self.request(
            "POST",
            request_state[0],
            "Anthropic",
            headers=request_state[1],
            **request_state[2],
            refresh_request=refresh_request if refresh_config else None,
            log_context=_request_log_context(config, messages),
        )
        try:
            return _clean_content(response.json()["content"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BusinessError("Anthropic 接口响应格式不正确", 50274) from exc

    async def _gemini(
        self,
        config: dict[str, Any],
        messages: list[dict[str, Any]],
        refresh_config: ConfigRefresher | None = None,
    ) -> str:
        role_map = {"assistant": "model", "user": "user"}
        def build_request(current: dict[str, Any]) -> tuple[str, dict[str, str], dict[str, Any]]:
            current_messages = _messages_with_current_system_prompt(messages, current)
            current_system = "\n".join(
                item["content"]
                for item in current_messages
                if item["role"] == "system"
            )
            current_payload: dict[str, Any] = {
                "contents": [
                    {
                        "role": role_map[item["role"]],
                        "parts": gemini_parts(item["content"]),
                    }
                    for item in current_messages
                    if item["role"] in role_map
                ],
                "generationConfig": {
                    "temperature": current["temperature"],
                    "maxOutputTokens": current["max_tokens"],
                },
            }
            if current_system:
                current_payload["systemInstruction"] = {"parts": [{"text": current_system}]}
            return (
                versioned_url(
                    current["base_url"], "v1beta",
                    f"models/{current['model_name']}:generateContent",
                ),
                {},
                {"params": {"key": current["api_key"]}, "json": current_payload},
            )

        request_state = build_request(config)

        async def refresh_request() -> tuple[str, dict[str, str], dict[str, Any]]:
            current = await refresh_config() if refresh_config else config
            return build_request(current)

        response = await self.request(
            "POST",
            request_state[0],
            "Gemini",
            headers=request_state[1],
            **request_state[2],
            refresh_request=refresh_request if refresh_config else None,
            log_context=_request_log_context(config, messages),
        )
        try:
            return _clean_content(response.json()["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise BusinessError("Gemini 接口响应格式不正确", 50275) from exc

    async def _dashscope(
        self,
        config: dict[str, Any],
        messages: list[dict[str, Any]],
        refresh_config: ConfigRefresher | None = None,
    ) -> str:
        if messages_have_images(messages):
            raise BusinessError("当前 DashScope 应用平台不支持图片输入", 40075)
        def build_request(current: dict[str, Any]) -> tuple[str, dict[str, str], dict[str, Any]]:
            current_messages = _messages_with_current_system_prompt(messages, current)
            prompt = "\n\n".join(
                f"{('用户' if item['role'] == 'user' else '助手' if item['role'] == 'assistant' else '系统')}：{item['content']}"
                for item in current_messages
            )
            return (
                current["base_url"],
                {"Authorization": f"Bearer {current['api_key']}"},
                {"json": {
                    "input": {"prompt": prompt},
                    "parameters": {
                        "temperature": current["temperature"],
                        "max_tokens": current["max_tokens"],
                    },
                    "debug": {},
                }},
            )

        request_state = build_request(config)

        async def refresh_request() -> tuple[str, dict[str, str], dict[str, Any]]:
            current = await refresh_config() if refresh_config else config
            return build_request(current)

        response = await self.request(
            "POST",
            request_state[0],
            "DashScope 应用",
            headers=request_state[1],
            **request_state[2],
            refresh_request=refresh_request if refresh_config else None,
            log_context=_request_log_context(config, messages),
        )
        try:
            return _clean_content(response.json()["output"]["text"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BusinessError("DashScope 应用响应格式不正确", 50276) from exc

    async def request(self, method: str, url: str, provider: str, **kwargs) -> httpx.Response:
        """发送 AI 平台请求，并统一处理网络、状态码重试和中文错误。"""
        allowed_error_statuses = kwargs.pop("allowed_error_statuses", set())
        refresh_request: RequestRefresher | None = kwargs.pop(
            "refresh_request", None
        )
        # 请求上下文（机器人、模型、请求规模），拼接到每一条重试/成功/失败日志
        log_context = str(kwargs.pop("log_context", "") or "")
        context_note = f"，{log_context}" if log_context else ""
        headers = {"Content-Type": "application/json", **kwargs.pop("headers", {})}
        status_retry_count = 0
        status_batch_retry_count = 0
        status_retry_round = 1
        network_retry_count = 0
        while True:
            try:
                response = await self.http_client.request(
                    method, url, headers=headers, **kwargs
                )
                network_retry_count = 0
                if response.status_code in RETRYABLE_STATUS_CODES:
                    status_retry_count += 1
                    status_batch_retry_count += 1
                    if status_batch_retry_count > MAX_NETWORK_RETRIES:
                        if status_retry_round >= MAX_STATUS_RETRY_ROUNDS:
                            logger.error(
                                "AI 平台连续重试已达到最大重试轮数，停止请求，"
                                "轮数=%s，累计重试=%s，provider=%s，method=%s，"
                                "url=%s，status=%s%s",
                                MAX_STATUS_RETRY_ROUNDS,
                                status_retry_count,
                                provider,
                                method,
                                url,
                                response.status_code,
                                context_note,
                            )
                            break
                        logger.warning(
                            "AI 平台连续重试%s次仍返回可重试状态，等待2秒后开始第%s轮重试，"
                            "provider=%s，method=%s，url=%s，status=%s%s",
                            MAX_NETWORK_RETRIES,
                            status_retry_round + 1,
                            provider,
                            method,
                            url,
                            response.status_code,
                            context_note,
                        )
                        await asyncio.sleep(RETRY_BATCH_WAIT_SECONDS)
                        if refresh_request:
                            url, new_headers, kwargs = await refresh_request()
                            headers = {"Content-Type": "application/json", **new_headers}
                        status_batch_retry_count = 0
                        status_retry_round += 1
                        continue
                    delay = _retry_delay(response, status_batch_retry_count - 1)
                    logger.warning(
                        "AI 平台返回可重试状态，将进行第%s轮第%s/%s次重试，provider=%s，"
                        "method=%s，url=%s，status=%s，等待=%ss%s",
                        status_retry_round,
                        status_batch_retry_count,
                        MAX_NETWORK_RETRIES,
                        provider,
                        method,
                        url,
                        response.status_code,
                        delay,
                        context_note,
                    )
                    await asyncio.sleep(delay)
                    if refresh_request:
                        url, new_headers, kwargs = await refresh_request()
                        headers = {"Content-Type": "application/json", **new_headers}
                    continue
                break
            except httpx.HTTPError as exc:
                retryable = isinstance(
                    exc, (httpx.TimeoutException, httpx.NetworkError)
                )
                if retryable and network_retry_count < MAX_NETWORK_RETRIES:
                    network_retry_count += 1
                    logger.warning(
                        "AI 平台请求失败，将进行第 %s/%s 次重试，provider=%s，"
                        "method=%s，url=%s，错误类型=%s%s",
                        network_retry_count,
                        MAX_NETWORK_RETRIES,
                        provider,
                        method,
                        url,
                        type(exc).__name__,
                        context_note,
                    )
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS * network_retry_count)
                    if refresh_request:
                        url, new_headers, kwargs = await refresh_request()
                        headers = {"Content-Type": "application/json", **new_headers}
                    continue
                detail = _network_error_message(exc)
                raw_detail = str(exc).strip() or "无附加错误信息"
                logger.error(
                    "AI 平台请求异常，provider=%s，method=%s，url=%s，错误类型=%s，错误=%s%s",
                    provider,
                    method,
                    url,
                    type(exc).__name__,
                    raw_detail,
                    context_note,
                )
                raise BusinessError(
                    f"{provider}{detail}，已重试{MAX_NETWORK_RETRIES}次，"
                    "请检查 API 地址和网络连接",
                    50271,
                ) from exc
        logger.info(
            "[AI 平台] provider=%s，method=%s，url=%s，status=%s%s，response=%s",
            provider,
            method,
            url,
            response.status_code,
            context_note,
            _truncate(response.text),
        )
        if response.status_code >= 400 and response.status_code not in allowed_error_statuses:
            retry_detail = f"，已重试{status_retry_count}次" if status_retry_count else ""
            raise BusinessError(
                f"{provider}返回 HTTP {response.status_code}：{_error_text(response)}{retry_detail}",
                50270,
            )
        return response

    @staticmethod
    def _generation_payload(
        config: dict[str, Any], messages: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {
            "model": config["model_name"],
            "messages": messages,
            "temperature": config["temperature"],
            "max_tokens": config["max_tokens"],
        }

    @staticmethod
    def _model_options(rows: Any) -> list[dict[str, str]]:
        if not isinstance(rows, list):
            raise BusinessError("AI 平台返回的模型列表格式不正确", 50277)
        options: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in rows:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or item.get("name") or "")
            if model_id.startswith("models/"):
                model_id = model_id.split("/", 1)[1]
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            label = str(item.get("display_name") or item.get("displayName") or model_id)
            options.append({"value": model_id, "label": label})
        return options
