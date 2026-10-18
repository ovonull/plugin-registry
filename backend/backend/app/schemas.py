"""后端请求参数模型与输入校验。"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,30}$")
BOT_QQ_PATTERN = re.compile(r"^[1-9][0-9]{4,19}$")


def validate_username_value(value: str) -> str:
    """校验并规范化用户名。"""
    value = value.strip()
    if not USERNAME_PATTERN.fullmatch(value):
        raise ValueError("用户名只能包含字母、数字和下划线")
    return value


def validate_password_strength_value(value: str) -> str:
    """校验密码必须同时包含字母和数字。"""
    if not any(char.isalpha() for char in value) or not any(
        char.isdigit() for char in value
    ):
        raise ValueError("密码必须同时包含字母和数字")
    return value


class LoginRequest(BaseModel):
    """账号密码、邮箱密码和邮箱验证码三种登录方式。"""

    login_type: Literal["username_password", "email_password", "email_code"]
    username: str | None = Field(default=None, max_length=30)
    email: EmailStr | None = None
    password: str | None = Field(default=None, max_length=128)
    email_code: str | None = Field(default=None, pattern=r"^\d{6}$")
    geetest_challenge: str = Field(min_length=16, max_length=128)


class RegisterRequest(BaseModel):
    """邮箱验证后的普通用户注册参数。"""

    username: str = Field(min_length=3, max_length=30)
    nickname: str | None = Field(default=None, max_length=50)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    email_code: str = Field(pattern=r"^\d{6}$")
    geetest_challenge: str = Field(min_length=16, max_length=128)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        """限制用户名仅包含字母、数字和下划线。"""
        return validate_username_value(value)

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        """注册密码必须同时包含字母和数字。"""
        return validate_password_strength_value(value)


class AdminUserCreateRequest(BaseModel):
    """管理员在后台直接创建普通用户的参数。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    username: str = Field(min_length=3, max_length=30)
    nickname: str | None = Field(default=None, max_length=50)
    email: EmailStr | None = None
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        """限制后台创建的用户名格式。"""
        return validate_username_value(value)

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        """限制后台创建的密码强度。"""
        return validate_password_strength_value(value)


class GeetestValidateRequest(BaseModel):
    """极验 GT3 二次校验参数。"""

    model_config = ConfigDict(populate_by_name=True)

    challenge: str = Field(min_length=16, max_length=128)
    geetest_validate: str = Field(
        validation_alias="validate", min_length=1, max_length=256
    )
    seccode: str = Field(min_length=1, max_length=256)


class SendEmailCodeRequest(BaseModel):
    """发送登录、注册或绑定邮箱验证码。"""

    email: EmailStr
    purpose: Literal["login", "register", "bind"]
    geetest_challenge: str = Field(min_length=16, max_length=128)


class ProfileUpdateRequest(BaseModel):
    """个人资料修改参数。"""

    nickname: str = Field(min_length=1, max_length=50)


class EmailBindRequest(BaseModel):
    """当前用户绑定或更换邮箱。"""

    email: EmailStr
    email_code: str = Field(pattern=r"^\d{6}$")
    geetest_challenge: str = Field(min_length=16, max_length=128)


class PasswordUpdateRequest(BaseModel):
    """登录密码修改参数。"""

    current_password: str = Field(min_length=6, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_new_password_strength(cls, value: str) -> str:
        if not any(char.isalpha() for char in value) or not any(
            char.isdigit() for char in value
        ):
            raise ValueError("新密码必须同时包含字母和数字")
        return value


class EmailSettingsRequest(BaseModel):
    """SMTP 发件人设置。"""

    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(default="", max_length=512)
    sender_name: str = Field(min_length=1, max_length=128)
    use_ssl: bool = True


class AppearanceSettingsRequest(BaseModel):
    """系统默认主题设置。"""

    color: Literal[
        "ocean", "emerald", "violet", "indigo", "amber",
        "sunset", "aurora", "rose", "ruby",
    ]
    font: Literal[
        "system", "yahei", "heiti", "songti", "kaiti",
        "fangsong", "xingkai", "rounded", "mono",
    ]
    effect: Literal["solid", "gradient"] = "solid"


class IntroductionSettingsRequest(BaseModel):
    """系统名称与说明设置。"""

    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=500)


class AnimatedEmojiSettingsRequest(BaseModel):
    """动画表情分类提示词设置参数。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    classification_prompt: str = Field(min_length=1, max_length=10000)


class AutoReplyCreateRequest(BaseModel):
    """自动回复规则新增参数。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    keyword: str = Field(min_length=1, max_length=100)
    bot_qq: str = Field(min_length=5, max_length=20)
    reply_content: str = Field(min_length=1, max_length=2000)
    is_enabled: bool = True

    @field_validator("bot_qq")
    @classmethod
    def validate_bot_qq(cls, value: str) -> str:
        """确保机器人 QQ 为合理长度的纯数字。"""
        if not BOT_QQ_PATTERN.fullmatch(value):
            raise ValueError("机器人 QQ 格式不正确")
        return value


class AutoReplyUpdateRequest(AutoReplyCreateRequest):
    """自动回复规则修改参数。"""


class ForbiddenWordCreateRequest(BaseModel):
    """违禁词批量新增参数，keywords 使用英文逗号分隔。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    bot_qq: str = Field(min_length=5, max_length=20)
    group_id: str | None = Field(default=None, max_length=30)
    keywords: str = Field(min_length=1, max_length=5000)
    mute_prompt: str = Field(default="", max_length=5000)
    mute_duration: int = Field(default=300, ge=1, le=2592000)
    mute_enabled: bool = True
    is_enabled: bool = True

    @field_validator("bot_qq")
    @classmethod
    def validate_bot_qq(cls, value: str) -> str:
        """确保机器人 QQ 为合理长度的纯数字。"""
        if not BOT_QQ_PATTERN.fullmatch(value):
            raise ValueError("机器人 QQ 格式不正确")
        return value

    @field_validator("group_id")
    @classmethod
    def validate_group_id(cls, value: str | None) -> str:
        """允许空群号表示该机器人的全部群。"""
        value = (value or "").strip()
        if value and not value.isdigit():
            raise ValueError("群号必须是数字")
        return value


class ForbiddenWordUpdateRequest(BaseModel):
    """违禁词规则修改参数，keyword 支持英文逗号分隔的多个词。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    bot_qq: str = Field(min_length=5, max_length=20)
    group_id: str | None = Field(default=None, max_length=30)
    keyword: str = Field(min_length=1, max_length=5000)
    mute_prompt: str = Field(default="", max_length=5000)
    mute_duration: int = Field(default=300, ge=1, le=2592000)
    mute_enabled: bool = True
    is_enabled: bool = True

    @field_validator("bot_qq")
    @classmethod
    def validate_bot_qq(cls, value: str) -> str:
        """确保机器人 QQ 为合理长度的纯数字。"""
        if not BOT_QQ_PATTERN.fullmatch(value):
            raise ValueError("机器人 QQ 格式不正确")
        return value

    @field_validator("group_id")
    @classmethod
    def validate_group_id(cls, value: str | None) -> str:
        """允许空群号表示该机器人的全部群。"""
        value = (value or "").strip()
        if value and not value.isdigit():
            raise ValueError("群号必须是数字")
        return value


class ForbiddenWordBatchDeleteRequest(BaseModel):
    """违禁词规则勾选批量软删除参数。"""

    ids: list[int] = Field(min_length=1, max_length=100)


class AnimatedEmojiBatchDeleteRequest(BaseModel):
    """动画表情记录勾选批量软删除参数。"""

    ids: list[int] = Field(min_length=1, max_length=100)


class ChatMessageBatchDeleteRequest(BaseModel):
    """聊天记录勾选批量软删除参数。"""

    ids: list[int] = Field(min_length=1, max_length=100)


class AiTriggerKeywordCreateRequest(BaseModel):
    """AI 触发关键词新增参数，keywords 使用英文逗号分隔。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    bot_qq: str = Field(min_length=5, max_length=20)
    keywords: str = Field(min_length=1, max_length=5000)
    is_enabled: bool = True
    reply_all: bool = False
    random_reply: bool = False
    random_reply_probability: int = Field(default=50, ge=0, le=100)

    @model_validator(mode="after")
    def validate_reply_modes(self):
        """确保回复全部与随机回复只能选择一种模式。"""
        if self.reply_all and self.random_reply:
            raise ValueError("回复全部与随机回复不能同时开启")
        return self

    @field_validator("bot_qq")
    @classmethod
    def validate_bot_qq(cls, value: str) -> str:
        """确保机器人 QQ 为合理长度的纯数字。"""
        if not BOT_QQ_PATTERN.fullmatch(value):
            raise ValueError("机器人 QQ 格式不正确")
        return value


class AiTriggerKeywordUpdateRequest(BaseModel):
    """AI 触发关键词修改参数，keyword 支持英文逗号分隔的多个词。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    bot_qq: str = Field(min_length=5, max_length=20)
    keyword: str = Field(min_length=1, max_length=5000)
    is_enabled: bool = True
    reply_all: bool = False
    random_reply: bool = False
    random_reply_probability: int = Field(default=50, ge=0, le=100)

    @model_validator(mode="after")
    def validate_reply_modes(self):
        """确保回复全部与随机回复只能选择一种模式。"""
        if self.reply_all and self.random_reply:
            raise ValueError("回复全部与随机回复不能同时开启")
        return self

    @field_validator("bot_qq")
    @classmethod
    def validate_bot_qq(cls, value: str) -> str:
        """确保机器人 QQ 为合理长度的纯数字。"""
        if not BOT_QQ_PATTERN.fullmatch(value):
            raise ValueError("机器人 QQ 格式不正确")
        return value


class AiTriggerKeywordBatchDeleteRequest(BaseModel):
    """AI 触发关键词勾选批量软删除参数。"""

    ids: list[int] = Field(min_length=1, max_length=100)


class BotCreateRequest(BaseModel):
    """机器人账号配置。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    bot_qq: str = Field(min_length=5, max_length=20)
    nickname: str = Field(default="", max_length=100)
    admin_qq: str = Field(default="", max_length=20)
    connection_mode: str = Field(
        default="auto", pattern="^(auto|websocket|http)$"
    )
    http_url: str = Field(default="", max_length=500)
    access_token: str = Field(default="", max_length=255)
    group_mode: str = Field(default="all", pattern="^(all|allowlist)$")
    is_enabled: bool = True
    welcome_enabled: bool = False
    auto_approve_friend: bool = False
    auto_approve_group_request: bool = False

    @field_validator("bot_qq")
    @classmethod
    def validate_bot_qq(cls, value: str) -> str:
        if not BOT_QQ_PATTERN.fullmatch(value):
            raise ValueError("机器人 QQ 格式不正确")
        return value

    @field_validator("admin_qq")
    @classmethod
    def validate_admin_qq(cls, value: str) -> str:
        """管理员 QQ 非必填，填写时必须为合法的 QQ 号。"""
        if value and not BOT_QQ_PATTERN.fullmatch(value):
            raise ValueError("管理员 QQ 格式不正确")
        return value


    @field_validator("http_url")
    @classmethod
    def validate_http_url(cls, value: str) -> str:
        value = value.rstrip("/")
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("HTTP 地址必须以 http:// 或 https:// 开头")
        return value


class BotUpdateRequest(BotCreateRequest):
    """机器人账号更新。"""

    welcome_enabled: bool | None = None


class BotGroupRequest(BaseModel):
    """机器人群白名单配置。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    group_id: str = Field(min_length=1, max_length=30)
    group_name: str = Field(default="", max_length=100)
    is_enabled: bool = True

    @field_validator("group_id")
    @classmethod
    def validate_group_id(cls, value: str) -> str:
        if not value.isdigit():
            raise ValueError("群号必须是数字")
        return value


class WelcomeMessageCreateRequest(BaseModel):
    """进群欢迎词新增或修改参数。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    bot_qq: str = Field(min_length=5, max_length=20)
    group_id: str | None = Field(default=None, max_length=30)
    welcome_content: str = Field(min_length=1, max_length=2000)
    is_enabled: bool = True

    @field_validator("bot_qq")
    @classmethod
    def validate_bot_qq(cls, value: str) -> str:
        if not BOT_QQ_PATTERN.fullmatch(value):
            raise ValueError("机器人 QQ 格式不正确")
        return value

    @field_validator("group_id")
    @classmethod
    def validate_group_id(cls, value: str | None) -> str:
        value = (value or "").strip()
        if value and not value.isdigit():
            raise ValueError("群号必须是数字")
        return value


class WelcomeMessageUpdateRequest(WelcomeMessageCreateRequest):
    """进群欢迎词修改参数。"""


class AiProactiveReplyCreateRequest(BaseModel):
    """AI 随机主动回复规则新增参数。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    bot_qq: str = Field(min_length=5, max_length=20)
    group_id: str | None = Field(default=None, max_length=30)
    is_enabled: bool = True
    min_interval_seconds: int = Field(default=60, ge=60, le=86400)
    max_interval_seconds: int = Field(default=300, ge=60, le=86400)

    @field_validator("bot_qq")
    @classmethod
    def validate_bot_qq(cls, value: str) -> str:
        """确保机器人 QQ 为合理长度的纯数字。"""
        if not BOT_QQ_PATTERN.fullmatch(value):
            raise ValueError("机器人 QQ 格式不正确")
        return value

    @field_validator("group_id")
    @classmethod
    def validate_group_id(cls, value: str | None) -> str:
        """允许空群号表示该机器人的全部群。"""
        value = (value or "").strip()
        if value and not value.isdigit():
            raise ValueError("群号必须是数字")
        return value

    @field_validator("max_interval_seconds")
    @classmethod
    def validate_interval_range(cls, value: int, info) -> int:
        """确保最大随机间隔不小于最小随机间隔。"""
        minimum = info.data.get("min_interval_seconds", 60)
        if value < minimum:
            raise ValueError("最大间隔不能小于最小间隔")
        return value


class AiProactiveReplyUpdateRequest(AiProactiveReplyCreateRequest):
    """AI 随机主动回复规则修改参数。"""


class ScheduledTaskUpdateRequest(BaseModel):
    """系统定时任务启停和执行间隔修改参数。"""

    interval_seconds: int = Field(default=300, ge=1, le=2592000)
    is_enabled: bool = True


class AiVoiceCharacterCreateRequest(BaseModel):
    """AI 语音角色配置新增参数。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    bot_qq: str = Field(min_length=5, max_length=20)
    group_id: str | None = Field(default=None, max_length=30)
    character_id: str = Field(min_length=1, max_length=100)
    character_name: str = Field(default="", max_length=100)
    preview_url: str = Field(default="", max_length=1000)
    is_enabled: bool = True

    @field_validator("bot_qq")
    @classmethod
    def validate_bot_qq(cls, value: str) -> str:
        """校验机器人 QQ 号。"""
        if not BOT_QQ_PATTERN.fullmatch(value):
            raise ValueError("机器人 QQ 格式不正确")
        return value

    @field_validator("group_id")
    @classmethod
    def validate_group_id(cls, value: str | None) -> str:
        """允许空群号表示机器人默认角色。"""
        value = (value or "").strip()
        if value and not value.isdigit():
            raise ValueError("群号必须是数字")
        return value

    @field_validator("preview_url")
    @classmethod
    def validate_preview_url(cls, value: str) -> str:
        """校验角色试听地址格式。"""
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("试听地址必须以 http:// 或 https:// 开头")
        return value


class AiVoiceCharacterUpdateRequest(AiVoiceCharacterCreateRequest):
    """AI 语音角色配置修改参数。"""


class BotAiConfigRequest(BaseModel):
    """机器人 AI 回复配置的保存、测试和模型查询参数。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    is_enabled: bool = False
    provider_type: Literal[
        "openai_compatible", "anthropic", "gemini", "dashscope_app"
    ] = "openai_compatible"
    base_url: str = Field(default="", max_length=1000)
    api_key: str = Field(default="", max_length=2048)
    model_name: str = Field(default="", max_length=255)
    image_model_name: str = Field(default="", max_length=255)
    text_provider_type: Literal[
        "openai_compatible", "anthropic", "gemini", "dashscope_app"
    ] | None = None
    text_base_url: str | None = Field(default=None, max_length=1000)
    text_api_key: str | None = Field(default=None, max_length=2048)
    text_model_name: str | None = Field(default=None, max_length=255)
    image_provider_type: Literal[
        "", "openai_compatible", "anthropic", "gemini", "dashscope_app"
    ] | None = None
    image_base_url: str | None = Field(default=None, max_length=1000)
    image_api_key: str | None = Field(default=None, max_length=2048)
    image_generation_provider_type: Literal[
        "", "openai_compatible", "anthropic", "gemini", "dashscope_app"
    ] | None = None
    image_generation_base_url: str | None = Field(default=None, max_length=1000)
    image_generation_api_key: str | None = Field(default=None, max_length=2048)
    image_generation_model_name: str | None = Field(default=None, max_length=255)
    system_prompt: str = Field(default="", max_length=10000)
    message_intent_prompt: str = Field(default="", max_length=10000)
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=1000, ge=1, le=32768)
    followup_count: int = Field(default=3, ge=0, le=5000)
    history_limit: int = Field(default=10, ge=0, le=5000)
    voice_reply_enabled: bool = False
    voice_reply_probability: int = Field(default=20, ge=0, le=100)
    image_reply_probability: int = Field(default=100, ge=0, le=100)
    voice_character: str = Field(
        default="lucy-voice-f38", max_length=100
    )

    @field_validator(
        "base_url", "text_base_url", "image_base_url", "image_generation_base_url"
    )
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.rstrip("/")
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("AI API 地址必须以 http:// 或 https:// 开头")
        return value


class BotActionRequest(BaseModel):
    """从管理界面调用 NapCat 动作。"""

    action: str = Field(min_length=1, max_length=100)
    bot_qq: str = Field(min_length=5, max_length=20)
    params: dict = Field(default_factory=dict)

    @field_validator("bot_qq")
    @classmethod
    def validate_bot_qq(cls, value: str) -> str:
        if not BOT_QQ_PATTERN.fullmatch(value):
            raise ValueError("机器人 QQ 格式不正确")
        return value
