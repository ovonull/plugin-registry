"""用户、会话、数据字典和自动回复规则模型。"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    IntegerPrimaryKeyMixin,
    SoftDeleteMixin,
    TimestampMixin,
)


BASE64_TEXT = Text().with_variant(LONGTEXT(), "mysql")


class User(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """系统登录用户。"""

    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, index=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nickname: Mapped[str] = mapped_column(String(50), nullable=False)
    role_code: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuthSession(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """可撤销的用户登录会话。"""

    __tablename__ = "auth_sessions"

    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


class GeetestChallenge(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """极验初始化与验证状态，支持多进程部署下的一次性消费。"""

    __tablename__ = "geetest_challenges"
    __table_args__ = (UniqueConstraint("challenge", name="uq_geetest_challenge"),)

    challenge: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


class EmailCode(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """邮箱验证码记录，只持久化摘要并支持一次性使用。"""

    __tablename__ = "email_codes"
    __table_args__ = (
        Index("ix_email_code_lookup", "email", "purpose", "used_at"),
        Index("ix_email_code_owner", "owner_id"),
    )

    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(30), nullable=False)
    challenge: Mapped[str] = mapped_column(String(128), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    request_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SystemSetting(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """按功能分组存储全局系统设置。"""

    __tablename__ = "system_settings"
    __table_args__ = (
        UniqueConstraint("setting_key", name="uq_system_setting_key"),
        Index("ix_system_setting_group", "group_name"),
    )

    group_name: Mapped[str] = mapped_column(String(32), nullable=False)
    setting_key: Mapped[str] = mapped_column(String(128), nullable=False)
    setting_value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    is_sensitive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class DataDictionary(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """集中维护角色等枚举的中文显示值。"""

    __tablename__ = "data_dictionaries"
    __table_args__ = (
        UniqueConstraint("dict_type", "item_code", name="uq_dictionary_type_code"),
    )

    dict_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    item_code: Mapped[str] = mapped_column(String(50), nullable=False)
    item_name: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class SystemFeature(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """系统内置功能说明，供功能列表菜单展示。"""

    __tablename__ = "system_features"
    __table_args__ = (
        UniqueConstraint("code", name="uq_system_feature_code"),
        Index("ix_system_feature_sort", "sort_order"),
    )

    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    trigger_keyword: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(50), nullable=False, default="通用")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AutoReplyRule(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """按用户隔离的机器人关键字自动回复规则。"""

    __tablename__ = "auto_reply_rules"
    __table_args__ = (
        Index("ix_auto_reply_bot_keyword", "bot_qq", "keyword"),
    )

    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    keyword: Mapped[str] = mapped_column(String(100), nullable=False)
    bot_qq: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    reply_content: Mapped[str] = mapped_column(Text, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ForbiddenWordRule(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """按机器人和群配置的一条逗号分隔违禁词列表。"""

    __tablename__ = "forbidden_word_rules"
    __table_args__ = (
        UniqueConstraint(
            "bot_qq", "group_id", "keyword", name="uq_forbidden_word_bot_group_keyword"
        ),
        Index("ix_forbidden_word_bot_group", "bot_qq", "group_id"),
    )

    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    bot_qq: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    group_id: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    # keyword 保留兼容历史逐词记录；新规则的完整列表写入 keywords。
    keyword: Mapped[str] = mapped_column(String(100), nullable=False)
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    mute_prompt: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    mute_duration: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    mute_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AiTriggerKeywordRule(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """按机器人维护一条逗号分隔的 AI 触发关键词列表。"""

    __tablename__ = "ai_trigger_keyword_rules"
    __table_args__ = (
        UniqueConstraint("bot_qq", name="uq_ai_trigger_keyword_bot"),
        Index("ix_ai_trigger_keyword_bot", "bot_qq", "is_enabled"),
    )

    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    bot_qq: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # keyword 保留兼容统一列表接口字段；新规则的完整 CSV 写入 keywords。
    keyword: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reply_all: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    random_reply: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    random_reply_probability: Mapped[int] = mapped_column(
        Integer, nullable=False, default=50
    )


class BotAccount(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """Configured NapCat bot account and its group access policy."""

    __tablename__ = "bot_accounts"
    __table_args__ = (UniqueConstraint("bot_qq", name="uq_bot_account_qq"),)

    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    bot_qq: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    nickname: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    admin_qq: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    connection_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="auto"
    )
    http_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    access_token: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    group_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="all"
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    welcome_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_approve_friend: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_approve_group_request: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )


class BotGroup(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """Group allowlist entry belonging to a bot QQ account."""

    __tablename__ = "bot_groups"
    __table_args__ = (
        UniqueConstraint("bot_qq", "group_id", name="uq_bot_group"),
        Index("ix_bot_group_bot", "bot_qq"),
    )

    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    bot_qq: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    group_id: Mapped[str] = mapped_column(String(30), nullable=False)
    group_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class BotFriend(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """机器人从 NapCat 同步回来的好友快照，按机器人和好友 QQ 唯一。"""

    __tablename__ = "bot_friends"
    __table_args__ = (
        UniqueConstraint("bot_qq", "friend_qq", name="uq_bot_friend"),
        Index("ix_bot_friend_owner_bot", "owner_id", "bot_qq"),
        Index("ix_bot_friend_bot_category", "bot_qq", "category_name"),
    )

    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    bot_qq: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    friend_qq: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    nickname: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    remark: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # 性别取值统一为 male/female/unknown，中文名称由 friend_sex 数据字典提供。
    sex: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    age: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    qid: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    long_nick: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    category_id: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    category_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    birthday: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    # 是否屏蔽该好友：屏蔽后接收其私聊/群聊消息时直接跳过全部处理。
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class BotAiConfig(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """机器人维度的 AI 回复配置，API Key 以密文保存。"""

    __tablename__ = "bot_ai_configs"
    __table_args__ = (UniqueConstraint("bot_qq", name="uq_bot_ai_config_qq"),)

    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    bot_qq: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    provider_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="openai_compatible"
    )
    base_url: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    api_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    image_model_name: Mapped[str] = mapped_column(
        String(255), nullable=False, default=""
    )
    # 新版按用途拆分的 AI 配置；旧字段保留用于历史数据和旧客户端兼容。
    text_provider_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    text_base_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    text_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    image_provider_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    image_base_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    image_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_generation_provider_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    image_generation_base_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    image_generation_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_generation_model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    message_intent_prompt: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)
    followup_count: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    history_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    voice_reply_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    voice_reply_probability: Mapped[int] = mapped_column(
        Integer, nullable=False, default=20
    )
    image_reply_probability: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100
    )
    voice_character: Mapped[str] = mapped_column(
        String(100), nullable=False, default="lucy-voice-f38"
    )


class AiVoiceCharacterRule(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """按机器人和群配置 AI 语音角色，空群号表示机器人默认角色。"""

    __tablename__ = "ai_voice_character_rules"
    __table_args__ = (
        UniqueConstraint("bot_qq", "group_id", name="uq_ai_voice_character_bot_group"),
        Index("ix_ai_voice_character_bot_group", "bot_qq", "group_id"),
    )

    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    bot_qq: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    group_id: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    character_id: Mapped[str] = mapped_column(String(100), nullable=False)
    character_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    preview_url: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AiProactiveReplyRule(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """按机器人和群配置 AI 随机主动回复规则。"""

    __tablename__ = "ai_proactive_reply_rules"
    __table_args__ = (
        UniqueConstraint("bot_qq", "group_id", name="uq_ai_proactive_bot_group"),
        Index("ix_ai_proactive_bot_schedule", "bot_qq", "is_enabled", "next_run_at"),
    )

    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    bot_qq: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    group_id: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    min_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    max_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_message_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")


class AiProactiveReplyMessage(
    Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin
):
    """记录 AI 主动回复规则已经处理过的消息，避免重复触发。"""

    __tablename__ = "ai_proactive_reply_messages"
    __table_args__ = (
        UniqueConstraint(
            "rule_id", "message_key", name="uq_ai_proactive_rule_message"
        ),
        Index("ix_ai_proactive_reply_message_rule", "rule_id", "created_at"),
    )

    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    rule_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    bot_qq: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    group_id: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    message_key: Mapped[str] = mapped_column(String(128), nullable=False)
    message_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")


class ScheduledTask(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """系统级定时任务配置及最近一次执行状态。"""

    __tablename__ = "scheduled_tasks"
    __table_args__ = (
        UniqueConstraint("task_key", name="uq_scheduled_task_key"),
        Index("ix_scheduled_task_schedule", "is_enabled", "next_run_at"),
    )

    # 系统级任务不属于某个业务用户，owner_id 允许为空；管理接口仅管理员可操作。
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    task_key: Mapped[str] = mapped_column(String(100), nullable=False)
    task_name: Mapped[str] = mapped_column(String(100), nullable=False)
    task_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_result: Mapped[str] = mapped_column(Text, nullable=False, default="")


class AnimatedEmoji(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """收到的 QQ 动画表情图片及其 AI 分类结果。"""

    __tablename__ = "animated_emojis"
    __table_args__ = (
        Index("ix_animated_emoji_bot_received", "bot_qq", "received_at"),
        Index(
            "ix_animated_emoji_bot_category",
            "bot_qq",
            "category",
            "is_deleted",
        ),
        Index("ix_animated_emoji_category_received", "category", "received_at"),
        Index("ix_animated_emoji_owner_received", "owner_id", "received_at"),
    )

    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    bot_qq: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    group_id: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    group_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    user_id: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    sender_nickname: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    message_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    file_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    summary: Mapped[str] = mapped_column(String(100), nullable=False, default="[动画表情]")
    media_type: Mapped[str] = mapped_column(String(100), nullable=False, default="image/jpeg")
    image_base64: Mapped[str | None] = mapped_column(
        BASE64_TEXT, nullable=True, default="", deferred=True
    )
    source_url: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    source_path: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    source_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="未分类", index=True)
    classification_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    classification_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    classified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


class WelcomeMessageRule(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """机器人进群欢迎词，空群号表示该机器人的默认欢迎词。"""

    __tablename__ = "welcome_message_rules"
    __table_args__ = (
        UniqueConstraint("bot_qq", "group_id", name="uq_welcome_bot_group"),
        Index("ix_welcome_rule_bot_group", "bot_qq", "group_id"),
    )

    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    bot_qq: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    group_id: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    welcome_content: Mapped[str] = mapped_column(Text, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ChatMessage(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """OneBot 接收到的聊天消息原始记录，按机器人和用户归属保存。"""

    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_message_owner_time", "owner_id", "event_time"),
        Index("ix_chat_message_bot_time", "bot_qq", "event_time"),
        Index(
            "ix_chat_message_bot_group_time",
            "bot_qq",
            "group_id",
            "event_time",
            "id",
        ),
        Index("ix_chat_message_group_time", "group_id", "event_time"),
        Index("ix_chat_message_message_id", "bot_qq", "message_id"),
    )

    # 未配置的机器人也允许写入，待后台配置后可通过 bot_qq 追溯归属。
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    bot_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    bot_qq: Mapped[str] = mapped_column(String(20), nullable=False, index=True, default="")
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    post_type: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    message_type: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    sub_type: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    message_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    group_id: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    target_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
    sender_user_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
    sender_nickname: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    sender_card: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    sender_role: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    raw_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    message_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    message_data: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sender_data: Mapped[str] = mapped_column(Text, nullable=False, default="")
    event_data: Mapped[str] = mapped_column(Text, nullable=False, default="")
    event_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    ai_triggered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
