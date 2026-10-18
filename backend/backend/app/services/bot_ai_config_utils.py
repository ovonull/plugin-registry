"""机器人 AI 配置分组和运行时配置转换辅助函数。"""

from typing import Any

from app.common.responses import BusinessError
from app.services.ai_image_generation_service import AiImageGenerationService
from app.services.ai_message_intent import DEFAULT_MESSAGE_INTENT_PROMPT
from app.services.ai_provider_service import DEFAULT_BASE_URLS


DEFAULT_CONFIG: dict[str, Any] = {
    "is_enabled": False,
    "provider_type": "openai_compatible",
    "base_url": DEFAULT_BASE_URLS["openai_compatible"],
    "api_key": "",
    "model_name": "qwen-plus",
    "image_model_name": "",
    "text_provider_type": "openai_compatible",
    "text_base_url": DEFAULT_BASE_URLS["openai_compatible"],
    "text_api_key": "",
    "text_model_name": "qwen-plus",
    "image_provider_type": "",
    "image_base_url": "",
    "image_api_key": "",
    "image_generation_provider_type": "",
    "image_generation_base_url": "",
    "image_generation_api_key": "",
    "image_generation_model_name": "",
    "system_prompt": "",
    "message_intent_prompt": DEFAULT_MESSAGE_INTENT_PROMPT,
    "temperature": 0.7,
    "max_tokens": 1000,
    "followup_count": 3,
    "history_limit": 10,
    "voice_reply_enabled": False,
    "voice_reply_probability": 20,
    "image_reply_probability": 100,
    "voice_character": "lucy-voice-f38",
}
DEFAULT_VOICE_CHARACTER = "lucy-voice-f38"


def text_runtime_config(values: dict[str, Any]) -> dict[str, Any]:
    """从配置值中生成文字模型调用所需的兼容运行时配置。"""
    selected = dict(values)
    selected["provider_type"] = values.get("text_provider_type") or values.get("provider_type")
    selected["base_url"] = values.get("text_base_url") or values.get("base_url")
    selected["api_key"] = values.get("text_api_key") or values.get("api_key")
    selected["model_name"] = values.get("text_model_name") or values.get("model_name")
    return selected


def optional_group_config(values: dict[str, Any], group: str) -> dict[str, Any] | None:
    """读取图片理解或文生图配置；模型名为空时返回 None 表示未配置。

    图片理解与文生图均为可选模块，用户不使用时可留空。判定统一以模型名
    为准：只要未填模型即视为未配置（即便平台/地址被自动填充），从而支持
    真正的可选；平台/地址/密钥缺省时回退到文字模型。
    """
    prefix = "image_generation" if group == "image_generation" else "image"
    provider = str(values.get(f"{prefix}_provider_type") or "").strip()
    base_url = str(values.get(f"{prefix}_base_url") or "").strip()
    api_key = str(values.get(f"{prefix}_api_key") or "").strip()
    model = str(values.get(f"{prefix}_model_name") or "").strip()
    if not model:
        return None
    return {
        "provider_type": provider or values.get("text_provider_type"),
        "base_url": base_url or values.get("text_base_url"),
        "api_key": api_key or values.get("text_api_key"),
        "model_name": model,
    }


def image_model_config(config: dict[str, Any]) -> dict[str, Any]:
    """选择图片理解模型；未配置时回退使用文字模型。"""
    image_model = str(config.get("image_model_name") or "").strip()
    if not image_model and not config.get("image_provider_type"):
        return config
    selected = dict(config)
    selected["provider_type"] = config.get("image_provider_type") or config.get(
        "provider_type"
    )
    selected["base_url"] = config.get("image_base_url") or config.get("base_url")
    selected["api_key"] = config.get("image_api_key") or config.get("api_key")
    selected["model_name"] = image_model or config.get("model_name")
    return selected


def model_module_config(
    values: dict[str, Any], model_type: str
) -> dict[str, Any]:
    """按模块选择文字、图片理解或文生图模型的运行时配置。"""
    if model_type == "text":
        return text_runtime_config(values)
    if model_type == "image":
        return image_model_config(values)
    if model_type == "generation":
        selected = optional_group_config(values, "image_generation")
        if selected is None:
            raise BusinessError("请先填写文生图模型配置", 40078)
        return selected
    raise BusinessError("AI 模型模块类型不正确", 40079)


def has_image_generation_config(config: dict[str, Any]) -> bool:
    """判断当前机器人是否配置了完整且受支持的文生图模型。"""
    generation_config = optional_group_config(config, "image_generation")
    if generation_config is None:
        return False
    try:
        AiImageGenerationService.validate(generation_config)
    except BusinessError:
        return False
    return True
