"""文生图平台调用与统一 Base64 图片结果解析服务。

功能：
1. 调用 OpenAI 兼容或 Gemini 文生图模型。
2. 将 Base64 或临时图片地址统一转换为 OneBot 可发送的图片结构。
"""

import base64
import binascii
from typing import Any
from urllib.parse import urlsplit

from app.common.responses import BusinessError
from app.services.ai_image_service import AiImage
from app.services.ai_provider_service import (
    AiProviderClient,
    openai_url,
    versioned_url,
)


SUPPORTED_IMAGE_GENERATION_PROVIDERS = frozenset({"openai_compatible", "gemini"})
MAX_IMAGE_PROMPT_LENGTH = 2000
DASHSCOPE_QWEN_IMAGE_PATH = "/api/v1/services/aigc/multimodal-generation/generation"


class AiImageGenerationService:
    """按机器人配置调用文生图模型并返回 Base64 图片。"""

    def __init__(self, provider_client: AiProviderClient) -> None:
        self.provider_client = provider_client

    @staticmethod
    def validate(config: dict[str, Any]) -> None:
        """校验文生图配置和当前已实现的平台协议。"""
        AiProviderClient.validate(config)
        provider = str(config.get("provider_type") or "")
        if provider not in SUPPORTED_IMAGE_GENERATION_PROVIDERS:
            raise BusinessError(
                "文生图当前仅支持 OpenAI 兼容和 Gemini 平台", 40076
            )

    async def generate(self, config: dict[str, Any], prompt: str) -> AiImage:
        """根据提示词生成一张图片。

        Args:
            config: 文生图平台、地址、密钥和模型配置。
            prompt: 意图模型生成的图片提示词。
        Returns:
            包含媒体类型和 Base64 数据的图片。
        """
        self.validate(config)
        prompt = str(prompt or "").strip()
        if not prompt:
            raise BusinessError("文生图提示词不能为空", 40077)
        if len(prompt) > MAX_IMAGE_PROMPT_LENGTH:
            raise BusinessError("文生图提示词不能超过 2000 个字符", 40077)
        if config["provider_type"] == "gemini":
            return await self._generate_gemini(config, prompt)
        if self._uses_qwen_image_protocol(config):
            return await self._generate_qwen_image(config, prompt)
        return await self._generate_openai(config, prompt)

    @staticmethod
    def _uses_qwen_image_protocol(config: dict[str, Any]) -> bool:
        """按模型名识别 Qwen-Image 协议，中转地址不参与协议判断。"""
        model = str(config.get("model_name") or "").lower()
        return model.startswith("qwen-image")

    async def _generate_qwen_image(
        self, config: dict[str, Any], prompt: str
    ) -> AiImage:
        """优先调用中转常见的 OpenAI Images 协议，必要时回退百炼原生协议。"""
        try:
            return await self._generate_openai(config, prompt)
        except BusinessError as exc:
            if not self._should_fallback_to_native(exc):
                raise
            return await self._generate_dashscope_qwen_image(config, prompt)

    @staticmethod
    def _should_fallback_to_native(error: BusinessError) -> bool:
        """仅在 OpenAI 图片协议明确不兼容时允许 Qwen-Image 回退。"""
        if error.code != 50270:
            return False
        message = str(error).lower()
        if "http 404" in message or "http 405" in message:
            return True
        if "http 400" not in message:
            return False
        return any(
            marker in message
            for marker in (
                "image model",
                "not support",
                "unsupported",
                "not found",
            )
        )

    async def _generate_dashscope_qwen_image(
        self, config: dict[str, Any], prompt: str
    ) -> AiImage:
        """调用阿里云百炼 Qwen-Image 原生同步接口。"""
        parsed = urlsplit(config["base_url"])
        url = f"{parsed.scheme}://{parsed.netloc}{DASHSCOPE_QWEN_IMAGE_PATH}"
        response = await self.provider_client.request(
            "POST",
            url,
            "阿里云百炼 Qwen-Image 文生图",
            headers={"Authorization": f"Bearer {config['api_key']}"},
            json={
                "model": config["model_name"],
                "input": {
                    "messages": [
                        {
                            "role": "user",
                            "content": [{"text": prompt}],
                        }
                    ]
                },
                "parameters": {
                    "prompt_extend": True,
                    "watermark": False,
                },
            },
        )
        try:
            contents = response.json()["output"]["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise BusinessError("阿里云百炼 Qwen-Image 响应格式不正确", 50280) from exc
        for item in contents if isinstance(contents, list) else []:
            if isinstance(item, dict) and str(item.get("image") or "").strip():
                return await self._download_image(
                    str(item["image"]).strip(),
                    "阿里云百炼 Qwen-Image 文生图",
                )
        raise BusinessError("阿里云百炼 Qwen-Image 未返回图片", 50280)

    async def _generate_openai(
        self, config: dict[str, Any], prompt: str
    ) -> AiImage:
        """调用 OpenAI 兼容的 ``images/generations`` 接口。"""
        response = await self.provider_client.request(
            "POST",
            openai_url(config["base_url"], "images/generations"),
            "OpenAI 兼容文生图",
            headers={"Authorization": f"Bearer {config['api_key']}"},
            json={
                "model": config["model_name"],
                "prompt": prompt,
                "n": 1,
                "response_format": "b64_json",
            },
        )
        try:
            body = response.json()
            rows = body["data"]
            item = rows[0]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise BusinessError("OpenAI 兼容文生图响应格式不正确", 50278) from exc
        if not isinstance(item, dict):
            raise BusinessError("OpenAI 兼容文生图响应格式不正确", 50278)
        base64_data = str(item.get("b64_json") or item.get("base64") or "").strip()
        if base64_data:
            return self._base64_image(base64_data)
        image_url = str(item.get("url") or "").strip()
        if not image_url:
            raise BusinessError("文生图平台未返回图片数据", 50278)
        return await self._download_image(image_url, "OpenAI 兼容文生图")

    async def _generate_gemini(
        self, config: dict[str, Any], prompt: str
    ) -> AiImage:
        """调用支持图片输出的 Gemini ``generateContent`` 接口。"""
        response = await self.provider_client.request(
            "POST",
            versioned_url(
                config["base_url"],
                "v1beta",
                f"models/{config['model_name']}:generateContent",
            ),
            "Gemini 文生图",
            params={"key": config["api_key"]},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
            },
        )
        try:
            parts = response.json()["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise BusinessError("Gemini 文生图响应格式不正确", 50279) from exc
        for part in parts if isinstance(parts, list) else []:
            if not isinstance(part, dict):
                continue
            inline = part.get("inlineData") or part.get("inline_data")
            if not isinstance(inline, dict):
                continue
            data = str(inline.get("data") or "").strip()
            if data:
                return self._base64_image(
                    data,
                    str(inline.get("mimeType") or inline.get("mime_type") or ""),
                )
        raise BusinessError("Gemini 文生图未返回图片数据", 50279)

    async def _download_image(self, url: str, provider: str) -> AiImage:
        """下载文生图平台返回的临时图片地址。"""
        response = await self.provider_client.request("GET", url, provider)
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
        if not response.content:
            raise BusinessError("文生图平台返回的图片为空", 50278)
        if not content_type.startswith("image/"):
            content_type = self._media_type(response.content)
        return {
            "media_type": content_type,
            "data": base64.b64encode(response.content).decode("ascii"),
        }

    @classmethod
    def _base64_image(cls, data: str, media_type: str = "") -> AiImage:
        """严格解码并重新编码平台返回的 Base64 图片。"""
        try:
            content = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError, TypeError) as exc:
            raise BusinessError("文生图平台返回的 Base64 图片无效", 50278) from exc
        if not content:
            raise BusinessError("文生图平台返回的图片为空", 50278)
        normalized_media_type = media_type if media_type.startswith("image/") else ""
        return {
            "media_type": normalized_media_type or cls._media_type(content),
            "data": base64.b64encode(content).decode("ascii"),
        }

    @staticmethod
    def _media_type(content: bytes) -> str:
        """根据常见图片文件头识别媒体类型。"""
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            return "image/webp"
        return "image/png"
