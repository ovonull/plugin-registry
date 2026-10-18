"""机器人 AI 模型模块测试服务。

功能：
1. 分别测试文字、图片理解和文生图模型。
2. 图片模型使用有效 PNG 验证真实多模态输入链路。
"""

from typing import Any

from app.services.ai_image_generation_service import AiImageGenerationService
from app.services.ai_image_service import AiImage
from app.services.ai_provider_service import AiProviderClient
from app.services.bot_ai_config_utils import model_module_config


TEST_IMAGE: AiImage = {
    "media_type": "image/png",
    "data": "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAL0lEQVR4nO3OIQEAAAgDMHrRjtBYiHEzMb/q2UsqAQEBAQEBAQEBAQEBAQGBdOABJ/TIl2pRVaoAAAAASUVORK5CYII=",
}


async def test_ai_model_module(
    provider: AiProviderClient,
    image_generator: AiImageGenerationService,
    values: dict[str, Any],
    model_type: str,
) -> dict[str, Any]:
    """调用指定模型模块并返回文本结果及可选的测试图片。"""
    selected = model_module_config(values, model_type)
    if model_type == "generation":
        image = await image_generator.generate(
            selected,
            "请生成一张简洁的蓝色几何图案，用于测试文生图模型。",
        )
        return {
            "reply": f"文生图测试成功，已返回 {image['media_type']} 图片",
            "image": image,
        }
    return {
        "reply": await provider.test(
            selected,
            images=[TEST_IMAGE] if model_type == "image" else None,
        ),
        "image": None,
    }
