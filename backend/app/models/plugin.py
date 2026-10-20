"""
插件系统的数据库模型。

功能：
1. 维护插件身份、版本与审核状态（审核状态机只落在 plugin_versions 上）；
2. 记录插件在账号上的安装与启用状态，并维护该安装分配到哪些机器人；
3. 维护可上传代码插件的信任名单；
4. 以「只追加」方式记录审核流水与插件动作审计，供事后追责。

设计约定：
- 表之间不使用外键约束，关联关系全部由代码维护（与项目既有规范一致）；
- plugin_reviews 与 plugin_action_logs 只追加，不提供 UPDATE / DELETE 入口；
- 审核状态只存在于 plugin_versions，plugins.is_published 是管理员的上架总闸，两者独立。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    IntegerPrimaryKeyMixin,
    SoftDeleteMixin,
    TimestampMixin,
)


class Plugin(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """插件主表：保存插件身份信息与上架总闸。"""

    __tablename__ = "plugins"
    __table_args__ = (
        UniqueConstraint("plugin_id", name="uq_plugin_identifier"),
        Index("ix_plugin_published", "is_published"),
        Index("ix_plugin_owner_published", "owner_id", "is_published"),
    )

    # 清单中的插件标识，一经入库禁止修改，改名视为新插件，防止绕过审核换内容。
    plugin_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    summary: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    author_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    author_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    homepage: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    license: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    # 取值 repo（代码仓库）或 zip（上传压缩包），中文名称由 plugin_source_type 数据字典提供。
    source_type: Mapped[str] = mapped_column(String(20), nullable=False, default="zip")
    repo_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    icon_path: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # 当前生效版本，指向 plugin_versions.id；由审核通过时更新，不使用外键约束。
    current_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 管理员上架总闸，默认关闭，只有上架后的插件才能被安装启用。
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class PluginVersion(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """插件版本表：审核状态机的唯一载体。"""

    __tablename__ = "plugin_versions"
    __table_args__ = (
        UniqueConstraint("plugin_id", "version", name="uq_plugin_version"),
        Index("ix_plugin_version_review", "review_status", "is_deleted"),
    )

    plugin_id: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(30), nullable=False)
    manifest_json: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 插件在清单中申请的能力
    capabilities_json: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 管理员实际批准的能力，允许小于申请，运行期以本字段为准
    granted_capabilities_json: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    entry: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    package_path: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    # 归一化后包内容的 sha256，用于发现提交后内容被偷改
    checksum: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # 仓库来源的 git tag 或 commit，压缩包来源为空
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # 取值 pending / approved / rejected，中文名称由 plugin_review_status 数据字典提供。
    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    reviewed_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    min_host_version: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    max_host_version: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)


class PluginInstallation(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """
    插件安装表：记录某个账号安装了哪个插件的哪个版本。

    一个账号对同一个插件只有一条安装记录，该账号下的多台机器人共用这份配置，
    具体分配到哪几台机器人由 plugin_installation_bots 维护，因此换机器人无需重复安装。
    """

    __tablename__ = "plugin_installations"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "plugin_id", name="uq_plugin_installation_owner_plugin"
        ),
        Index("ix_plugin_installation_owner_only", "owner_id"),
    )

    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    plugin_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 默认关闭，安装后需要用户显式开启，避免新装插件立即影响线上群。
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 插件配置；含敏感字段时先用 SETTINGS_ENCRYPTION_SECRET 加密后再写入。
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 本安装实例实际生效的能力，默认继承版本批准值，允许按账号再收窄。
    granted_capabilities_json: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )


class PluginInstallationBot(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """
    插件安装与机器人的分配关系。

    一条记录表示「该安装实例允许在这台机器人上运行」，取消分配即软删除，
    运行期按机器人反查安装实例，账号与账号之间互不可见。
    """

    __tablename__ = "plugin_installation_bots"
    __table_args__ = (
        UniqueConstraint(
            "installation_id", "bot_qq", name="uq_plugin_installation_bot"
        ),
        Index("ix_plugin_installation_bot_qq", "bot_qq"),
    )

    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    installation_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    bot_qq: Mapped[str] = mapped_column(String(20), nullable=False)


class PluginTrustedAuthor(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """信任名单：被授权上传代码插件的用户。"""

    __tablename__ = "plugin_trusted_authors"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_plugin_trusted_author"),
    )

    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    granted_by: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # 撤销授权时置为 False，保留历史授权记录，不做物理删除。
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PluginReview(Base, IntegerPrimaryKeyMixin, TimestampMixin):
    """插件审核流水，只追加，禁止 UPDATE / DELETE。"""

    __tablename__ = "plugin_reviews"
    __table_args__ = (
        Index("ix_plugin_review_plugin", "plugin_id", "created_at"),
        Index("ix_plugin_review_reviewer", "reviewer_id", "created_at"),
    )

    plugin_id: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    # 取值 submit / approve / reject / publish / unpublish / delete，中文名称由 plugin_review_action 字典提供。
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    from_status: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    to_status: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    reviewer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")


class PluginActionLog(Base, IntegerPrimaryKeyMixin, TimestampMixin):
    """插件动作审计日志，只追加，未授权调用也必须留痕。"""

    __tablename__ = "plugin_action_logs"
    __table_args__ = (
        Index("ix_plugin_action_log_plugin", "plugin_id", "created_at"),
        Index("ix_plugin_action_log_bot", "bot_qq", "created_at"),
    )

    plugin_id: Mapped[str] = mapped_column(String(100), nullable=False)
    version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    bot_qq: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    group_id: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    user_id: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    action_name: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    capability: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    # 已脱敏的调用参数，禁止写入任何密钥或凭证。
    params_json: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 取值 ok / denied / error / timeout，中文名称由 plugin_action_result 字典提供。
    result_code: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    result_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class RegistryToken(Base, IntegerPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """插件库分发接口的接入令牌。

    只保存摘要与前缀：明文仅创建时返回一次，之后无法再读取，
    与「单条 settings 加密保存」相比更适合发给多个下游并逐个吊销。
    """

    __tablename__ = "registry_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_registry_token_hash"),
        Index("ix_registry_token_active", "is_deleted"),
    )

    # 用途标注，例如「NapCatQQ Service 生产」，方便区分是谁在用。
    name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    # sha256 摘要，用于等值比对；不存明文。
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # 明文前缀（reg_ 加前 8 位），仅用于列表展示与人工核对。
    token_prefix: Mapped[str] = mapped_column(String(24), nullable=False, default="")
    # 最近一次通过鉴权的时间，用于判断某个下游是否还在用。
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
