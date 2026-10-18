"""机器人 AI 配置持久化、权限隔离和第三方平台协调服务。"""

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.common.responses import BusinessError
from app.common.time_utils import beijing_now, format_beijing
from app.models.entities import BotAiConfig, DataDictionary, User
from app.schemas import BotAiConfigRequest
from app.services.ai_provider_service import DEFAULT_BASE_URLS, AiProviderClient
from app.services.ai_image_service import AiImage
from app.services.ai_image_generation_service import AiImageGenerationService
from app.services.bot_ai_config_utils import (
    DEFAULT_CONFIG,
    DEFAULT_VOICE_CHARACTER,
    has_image_generation_config,
    image_model_config,
    model_module_config,
    optional_group_config,
    text_runtime_config,
)
from app.services.bot_ai_model_test_service import test_ai_model_module
from app.services.bot_service import BotService
from app.services.setting_service import decrypt_secret, encrypt_secret

class BotAiService:
    """维护每个机器人的单独 AI 回复配置。"""
    def __init__(
        self,
        bot_service: BotService,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self.bot_service = bot_service
        self.session_factory = session_factory
        self.provider = AiProviderClient()
        self.image_generator = AiImageGenerationService(self.provider)

    async def close(self) -> None:
        """关闭 AI 平台连接池。"""
        await self.provider.close()

    async def provider_options(self, session: AsyncSession) -> list[dict[str, Any]]:
        """从数据字典读取平台名称并补充协议能力。"""
        rows = (
            await session.scalars(
                select(DataDictionary)
                .where(
                    DataDictionary.dict_type == "ai_provider",
                    DataDictionary.is_enabled.is_(True),
                    DataDictionary.is_deleted.is_(False),
                )
                .order_by(DataDictionary.sort_order, DataDictionary.id)
            )
        ).all()
        return [
            {
                "value": row.item_code,
                "label": row.item_name,
                "default_base_url": DEFAULT_BASE_URLS.get(row.item_code, ""),
                "supports_models": row.item_code != "dashscope_app",
                "requires_model": row.item_code != "dashscope_app",
            }
            for row in rows
            if row.item_code in DEFAULT_BASE_URLS
        ]

    async def get_config(
        self, session: AsyncSession, user: User, bot_qq: str
    ) -> dict[str, Any]:
        """校验机器人权限并返回配置，供授权用户编辑已有 API Key。"""
        await self.bot_service.get_bot(session, user, bot_qq)
        config = await self._find(session, bot_qq)
        return self.serialize(config, bot_qq, reveal_api_key=True)

    async def save_config(
        self,
        session: AsyncSession,
        user: User,
        bot_qq: str,
        payload: BotAiConfigRequest,
    ) -> dict[str, Any]:
        """保存配置；空白或掩码 API Key 表示保留原密钥。"""
        bot = await self.bot_service.get_bot(session, user, bot_qq)
        config = await session.scalar(
            select(BotAiConfig).where(BotAiConfig.bot_qq == bot_qq)
        )
        values = self._merge(
            config if config is not None and not config.is_deleted else None,
            payload,
        )
        if values["is_enabled"]:
            self.provider.validate(text_runtime_config(values))
            image_config = optional_group_config(values, "image")
            if image_config:
                self.provider.validate(image_config)
            generation_config = optional_group_config(values, "image_generation")
            if generation_config:
                self.image_generator.validate(generation_config)
        encrypted_key = encrypt_secret(values["text_api_key"]) if values["text_api_key"] else ""
        encrypted_image_key = encrypt_secret(values["image_api_key"]) if values["image_api_key"] else ""
        encrypted_generation_key = encrypt_secret(values["image_generation_api_key"]) if values["image_generation_api_key"] else ""
        if config is None:
            config = BotAiConfig(
                owner_id=bot.owner_id,
                bot_qq=bot_qq,
                api_key=encrypted_key,
            )
            session.add(config)
        else:
            config.owner_id = bot.owner_id
            config.api_key = encrypted_key
            config.is_deleted = False
            config.updated_at = beijing_now()
        config.is_enabled = values["is_enabled"]
        config.provider_type = values["provider_type"]
        config.base_url = values["base_url"]
        config.model_name = values["model_name"]
        config.image_model_name = values["image_model_name"]
        config.text_provider_type = values["text_provider_type"]
        config.text_base_url = values["text_base_url"]
        config.text_api_key = encrypted_key
        config.text_model_name = values["text_model_name"]
        config.image_provider_type = values["image_provider_type"] or None
        config.image_base_url = values["image_base_url"] or None
        config.image_api_key = encrypted_image_key or (config.image_api_key if config is not None else None)
        config.image_generation_provider_type = values["image_generation_provider_type"] or None
        config.image_generation_base_url = values["image_generation_base_url"] or None
        config.image_generation_api_key = encrypted_generation_key or (config.image_generation_api_key if config is not None else None)
        config.image_generation_model_name = values["image_generation_model_name"] or None
        config.system_prompt = values["system_prompt"]
        config.message_intent_prompt = values["message_intent_prompt"]
        config.temperature = values["temperature"]
        config.max_tokens = values["max_tokens"]
        config.followup_count = values["followup_count"]
        config.history_limit = values["history_limit"]
        config.voice_reply_enabled = values["voice_reply_enabled"]
        config.voice_reply_probability = values["voice_reply_probability"]
        config.image_reply_probability = values["image_reply_probability"]
        config.voice_character = values["voice_character"]
        await session.commit()
        await session.refresh(config)
        return self.serialize(config, bot_qq)

    async def test_config(
        self,
        session: AsyncSession,
        user: User,
        bot_qq: str,
        payload: BotAiConfigRequest,
        model_type: str = "text",
    ) -> dict[str, Any]:
        """合并尚未保存的表单与已有密钥后测试指定模型模块。"""
        await self.bot_service.get_bot(session, user, bot_qq)
        config = await self._find(session, bot_qq)
        values = self._merge(config, payload)
        # 供 AI 请求日志归属到具体机器人，便于对比两个机器人的测试结果
        values["bot_qq"] = bot_qq
        return await test_ai_model_module(
            self.provider, self.image_generator, values, model_type
        )

    async def list_models(
        self, session: AsyncSession, user: User, bot_qq: str,
        payload: BotAiConfigRequest, model_type: str = "text",
    ) -> list[dict[str, str]]:
        """合并表单与已有密钥后查询指定模块的平台模型。"""
        await self.bot_service.get_bot(session, user, bot_qq)
        config = await self._find(session, bot_qq)
        return await self.provider.list_models(
            model_module_config(self._merge(config, payload), model_type)
        )

    async def active_runtime_config(
        self, session: AsyncSession, bot_qq: str
    ) -> dict[str, Any] | None:
        """读取事件处理使用的启用配置和明文运行时密钥。"""
        config = await session.scalar(
            select(BotAiConfig).where(
                BotAiConfig.bot_qq == bot_qq,
                BotAiConfig.is_enabled.is_(True),
                BotAiConfig.is_deleted.is_(False),
            )
        )
        if config is None:
            return None
        values = self._entity_values(config)
        values["bot_qq"] = bot_qq
        self.provider.validate(values)
        return values

    async def generate(
        self,
        config: dict[str, Any],
        user_text: str,
        quoted_text: str = "",
        images: list[AiImage] | None = None,
        history: list[dict[str, str]] | None = None,
        flatten_history: bool = False,
    ) -> str:
        """调用当前机器人所选平台生成回复，可附带历史消息和图片输入。

        Args:
            flatten_history: 为真时把历史压成单段群聊转录，规避部分中转把
                assistant 历史转成 output_text 导致的上游 400；私聊传假。
        """
        refresh_config = self._runtime_config_refresher(config)
        return await self.provider.generate(
            config,
            user_text,
            quoted_text,
            images,
            history,
            refresh_config=refresh_config,
            flatten_history=flatten_history,
        )

    async def generate_image(
        self, config: dict[str, Any], prompt: str
    ) -> AiImage:
        """使用机器人单独配置的文生图模型生成一张图片。

        Args:
            config: 当前机器人的完整运行时 AI 配置。
            prompt: 已根据消息语义整理的文生图提示词。
        Returns:
            可直接交给 OneBot 发送的 Base64 图片。
        """
        generation_config = optional_group_config(config, "image_generation")
        if generation_config is None:
            raise BusinessError("请先配置并启用文生图模型", 40077)
        return await self.image_generator.generate(generation_config, prompt)

    def _runtime_config_refresher(
        self, config: dict[str, Any]
    ) -> Callable[[], Awaitable[dict[str, Any]]] | None:
        """创建重试前读取机器人最新 AI 配置的回调。"""
        if config.get("_disable_config_refresh"):
            return None
        bot_qq = str(config.get("bot_qq") or "").strip()
        if not bot_qq or self.session_factory is None:
            return None
        prompt_override = config.get("_system_prompt_override", None)
        has_prompt_override = "_system_prompt_override" in config

        async def refresh() -> dict[str, Any]:
            async with self.session_factory() as session:
                latest = await self.active_runtime_config(session, bot_qq)
            if latest is None:
                raise BusinessError(
                    f"机器人 {bot_qq} 的 AI 配置已关闭，已停止重试", 40072
                )
            if has_prompt_override:
                latest["system_prompt"] = str(prompt_override or "")
                latest["_system_prompt_override"] = prompt_override
            return latest

        return refresh

    @staticmethod
    async def _find(
        session: AsyncSession, bot_qq: str
    ) -> BotAiConfig | None:
        return await session.scalar(
            select(BotAiConfig).where(
                BotAiConfig.bot_qq == bot_qq,
                BotAiConfig.is_deleted.is_(False),
            )
        )

    def _merge(
        self, config: BotAiConfig | None, payload: BotAiConfigRequest
    ) -> dict[str, Any]:
        current = self._entity_values(config) if config else dict(DEFAULT_CONFIG)
        existing_text_key = str(current.get("text_api_key") or current.get("api_key") or "").strip()
        existing_image_key = str(current.get("image_api_key") or "").strip()
        existing_generation_key = str(current.get("image_generation_api_key") or "").strip()
        # 只合并调用方实际提交的字段，兼容新增配置字段前的旧客户端。
        submitted = payload.model_dump(exclude_unset=True)
        current.update(submitted)
        # 新字段优先，旧字段作为文字模型兼容别名。
        if "text_provider_type" not in submitted:
            current["text_provider_type"] = current.get("provider_type") or DEFAULT_CONFIG["text_provider_type"]
        if "text_base_url" not in submitted:
            current["text_base_url"] = current.get("base_url") or DEFAULT_CONFIG["text_base_url"]
        if "text_model_name" not in submitted:
            current["text_model_name"] = current.get("model_name") or DEFAULT_CONFIG["text_model_name"]
        current["text_provider_type"] = (
            str(current.get("text_provider_type") or DEFAULT_CONFIG["text_provider_type"]).strip()
        )
        if current["text_provider_type"] not in DEFAULT_BASE_URLS:
            current["text_provider_type"] = DEFAULT_CONFIG["text_provider_type"]
        text_key = str((submitted.get("text_api_key") if "text_api_key" in submitted else submitted.get("api_key")) or "").strip()
        if text_key in {"", "******"}:
            text_key = existing_text_key
        current["text_api_key"] = text_key
        current["api_key"] = text_key
        current["provider_type"] = current["text_provider_type"]
        current["base_url"] = current["text_base_url"]
        current["model_name"] = current["text_model_name"]
        current["text_base_url"] = str(current.get("text_base_url") or "").rstrip("/")
        if not current["text_base_url"]:
            current["text_base_url"] = DEFAULT_BASE_URLS[current["text_provider_type"]]
        current["base_url"] = current["text_base_url"]
        current["text_model_name"] = str(current.get("text_model_name") or "").strip()
        current["model_name"] = current["text_model_name"]
        current["image_model_name"] = str(
            current.get("image_model_name") or ""
        ).strip()
        current["image_provider_type"] = str(current.get("image_provider_type") or "").strip()
        current["image_base_url"] = str(current.get("image_base_url") or "").rstrip("/")
        image_key = str(current.get("image_api_key") or "").strip()
        current["image_api_key"] = image_key if image_key not in {"", "******"} else existing_image_key
        current["image_generation_provider_type"] = str(current.get("image_generation_provider_type") or "").strip()
        current["image_generation_base_url"] = str(current.get("image_generation_base_url") or "").rstrip("/")
        generation_key = str(current.get("image_generation_api_key") or "").strip()
        current["image_generation_api_key"] = generation_key if generation_key not in {"", "******"} else existing_generation_key
        current["image_generation_model_name"] = str(current.get("image_generation_model_name") or "").strip()
        current["system_prompt"] = str(current.get("system_prompt") or "").strip()
        current["message_intent_prompt"] = str(
            current.get("message_intent_prompt")
            or DEFAULT_CONFIG["message_intent_prompt"]
        ).strip()
        current["temperature"] = float(current["temperature"])
        current["max_tokens"] = int(current["max_tokens"])
        current["followup_count"] = int(
            current.get("followup_count")
            if current.get("followup_count") is not None
            else DEFAULT_CONFIG["followup_count"]
        )
        current["history_limit"] = int(
            current.get("history_limit")
            if current.get("history_limit") is not None
            else DEFAULT_CONFIG["history_limit"]
        )
        current["voice_reply_enabled"] = bool(
            current.get("voice_reply_enabled", DEFAULT_CONFIG["voice_reply_enabled"])
        )
        current["voice_reply_probability"] = int(
            current.get(
                "voice_reply_probability", DEFAULT_CONFIG["voice_reply_probability"]
            )
            if current.get("voice_reply_probability") is not None
            else DEFAULT_CONFIG["voice_reply_probability"]
        )
        current["image_reply_probability"] = int(
            current.get(
                "image_reply_probability", DEFAULT_CONFIG["image_reply_probability"]
            )
            if current.get("image_reply_probability") is not None
            else DEFAULT_CONFIG["image_reply_probability"]
        )
        current["voice_character"] = self._normalise_voice_character(
            current.get("voice_character")
        )
        return current

    @staticmethod
    def _normalise_voice_character(value: Any) -> str:
        """将旧示例角色迁移为当前默认优先角色。"""
        character = str(value or "").strip()
        if character in {"", "ai_char_1", "lucy-voice-laibixiaoxin"}:
            return DEFAULT_VOICE_CHARACTER
        return character[:100]

    @staticmethod
    def _entity_values(config: BotAiConfig) -> dict[str, Any]:
        text_provider = config.text_provider_type or config.provider_type
        text_base_url = config.text_base_url or config.base_url
        text_model_name = config.text_model_name or config.model_name
        text_api_key = decrypt_secret(config.text_api_key or config.api_key)
        return {
            "is_enabled": config.is_enabled,
            "provider_type": text_provider,
            "base_url": text_base_url,
            "api_key": text_api_key,
            "model_name": text_model_name,
            "image_model_name": str(config.image_model_name or ""),
            "text_provider_type": text_provider,
            "text_base_url": text_base_url,
            "text_api_key": text_api_key,
            "text_model_name": text_model_name,
            "image_provider_type": config.image_provider_type or "",
            "image_base_url": config.image_base_url or "",
            "image_api_key": decrypt_secret(config.image_api_key or ""),
            "image_generation_provider_type": config.image_generation_provider_type or "",
            "image_generation_base_url": config.image_generation_base_url or "",
            "image_generation_api_key": decrypt_secret(config.image_generation_api_key or ""),
            "image_generation_model_name": config.image_generation_model_name or "",
            "system_prompt": config.system_prompt,
            "message_intent_prompt": (
                config.message_intent_prompt
                or DEFAULT_CONFIG["message_intent_prompt"]
            ),
            "temperature": float(config.temperature),
            "max_tokens": config.max_tokens,
            "followup_count": int(
                config.followup_count
                if config.followup_count is not None
                else DEFAULT_CONFIG["followup_count"]
            ),
            "history_limit": int(
                config.history_limit
                if config.history_limit is not None
                else DEFAULT_CONFIG["history_limit"]
            ),
            "voice_reply_enabled": bool(
                config.voice_reply_enabled
                if config.voice_reply_enabled is not None
                else DEFAULT_CONFIG["voice_reply_enabled"]
            ),
            "voice_reply_probability": int(
                config.voice_reply_probability
                if config.voice_reply_probability is not None
                else DEFAULT_CONFIG["voice_reply_probability"]
            ),
            "image_reply_probability": int(
                config.image_reply_probability
                if config.image_reply_probability is not None
                else DEFAULT_CONFIG["image_reply_probability"]
            ),
            "voice_character": BotAiService._normalise_voice_character(
                config.voice_character
            ),
        }

    @staticmethod
    def serialize(
        config: BotAiConfig | None,
        bot_qq: str,
        reveal_api_key: bool = False,
    ) -> dict[str, Any]:
        """序列化前端配置，只有授权读取配置时才回传 API Key。"""
        values = dict(DEFAULT_CONFIG)
        if config is not None:
            values.update(
                {
                    "is_enabled": config.is_enabled,
                    "provider_type": config.text_provider_type or config.provider_type,
                    "base_url": config.text_base_url or config.base_url,
                    "model_name": config.text_model_name or config.model_name,
                    "image_model_name": str(config.image_model_name or ""),
                    "text_provider_type": config.text_provider_type or config.provider_type,
                    "text_base_url": config.text_base_url or config.base_url,
                    "text_model_name": config.text_model_name or config.model_name,
                    "image_provider_type": config.image_provider_type or "",
                    "image_base_url": config.image_base_url or "",
                    "image_generation_provider_type": config.image_generation_provider_type or "",
                    "image_generation_base_url": config.image_generation_base_url or "",
                    "image_generation_model_name": config.image_generation_model_name or "",
                    "system_prompt": config.system_prompt,
                    "message_intent_prompt": (
                        config.message_intent_prompt
                        or DEFAULT_CONFIG["message_intent_prompt"]
                    ),
                    "temperature": float(config.temperature),
                    "max_tokens": config.max_tokens,
                    "followup_count": int(
                        config.followup_count
                        if config.followup_count is not None
                        else DEFAULT_CONFIG["followup_count"]
                    ),
                    "history_limit": int(
                        config.history_limit
                        if config.history_limit is not None
                        else DEFAULT_CONFIG["history_limit"]
                    ),
                    "voice_reply_enabled": bool(
                        config.voice_reply_enabled
                        if config.voice_reply_enabled is not None
                        else DEFAULT_CONFIG["voice_reply_enabled"]
                    ),
                    "voice_reply_probability": int(
                        config.voice_reply_probability
                        if config.voice_reply_probability is not None
                        else DEFAULT_CONFIG["voice_reply_probability"]
                    ),
                    "image_reply_probability": int(
                        config.image_reply_probability
                        if config.image_reply_probability is not None
                        else DEFAULT_CONFIG["image_reply_probability"]
                    ),
                    "voice_character": BotAiService._normalise_voice_character(
                        config.voice_character
                    ),
                }
            )
        values.update(
            {
                "bot_qq": bot_qq,
                "api_key": (
                    decrypt_secret(config.text_api_key or config.api_key)
                    if reveal_api_key and config
                    else ""
                ),
                "text_api_key": decrypt_secret(config.text_api_key or config.api_key) if reveal_api_key and config else "",
                "image_api_key": decrypt_secret(config.image_api_key or "") if reveal_api_key and config else "",
                "image_generation_api_key": decrypt_secret(config.image_generation_api_key or "") if reveal_api_key and config else "",
                "has_api_key": bool(config and decrypt_secret(config.text_api_key or config.api_key)),
                "has_text_api_key": bool(config and decrypt_secret(config.text_api_key or config.api_key)),
                "has_image_api_key": bool(config and decrypt_secret(config.image_api_key or "")),
                "has_image_generation_api_key": bool(config and decrypt_secret(config.image_generation_api_key or "")),
                "created_at": format_beijing(config.created_at) if config else None,
                "updated_at": format_beijing(config.updated_at) if config else None,
            }
        )
        return values
    image_model_config = staticmethod(image_model_config)
    has_image_generation_config = staticmethod(has_image_generation_config)
