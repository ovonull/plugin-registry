"""多模态消息在不同 AI 平台协议之间的转换工具。"""

from typing import Any

from app.services.ai_image_service import AiImage


def build_openai_content(
    user_text: str, images: list[AiImage]
) -> str | list[dict[str, Any]]:
    """构造 OpenAI 兼容协议的文本或多模态内容。"""
    if not images:
        return user_text
    content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    content.extend(
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{image['media_type']};base64,{image['data']}"
            },
        }
        for image in images
    )
    return content


def messages_have_images(messages: list[dict[str, Any]]) -> bool:
    """判断消息是否包含多模态图片块。"""
    return any(
        isinstance(item.get("content"), list)
        and any(
            isinstance(part, dict)
            and part.get("type") in {"image_url", "image"}
            for part in item["content"]
        )
        for item in messages
    )


def to_anthropic_message(item: dict[str, Any]) -> dict[str, Any]:
    """将统一消息转换为 Anthropic content block 格式。"""
    content = item["content"]
    if not isinstance(content, list):
        return {"role": item["role"], "content": content}
    blocks: list[dict[str, Any]] = []
    for part in content:
        if part.get("type") == "text":
            blocks.append({"type": "text", "text": part["text"]})
        elif part.get("type") == "image_url":
            url = str(part.get("image_url", {}).get("url", ""))
            media_type, _, data = url.partition(";base64,")
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type.removeprefix("data:"),
                        "data": data,
                    },
                }
            )
    return {"role": item["role"], "content": blocks}


def gemini_parts(content: Any) -> list[dict[str, Any]]:
    """将统一消息转换为 Gemini parts 格式。"""
    if not isinstance(content, list):
        return [{"text": str(content)}]
    parts: list[dict[str, Any]] = []
    for part in content:
        if part.get("type") == "text":
            parts.append({"text": part["text"]})
        elif part.get("type") == "image_url":
            url = str(part.get("image_url", {}).get("url", ""))
            media_type, _, data = url.partition(";base64,")
            parts.append(
                {
                    "inline_data": {
                        "mime_type": media_type.removeprefix("data:"),
                        "data": data,
                    }
                }
            )
    return parts
