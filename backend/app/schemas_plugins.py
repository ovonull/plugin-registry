"""插件系统接口请求参数模型。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PluginRepoSubmitRequest(BaseModel):
    """从代码仓库提交插件版本的参数。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    repo_url: str = Field(min_length=1, max_length=500)
    # 留空即自动探测仓库最新的 release / tag / commit；
    # 填写时只允许 tag 或 commit，由 PluginSourceService 校验并给出中文提示。
    source_ref: str = Field("", max_length=255)


class PluginRepoProbeRequest(BaseModel):
    """探测仓库最新版本的参数，不落库、不登记，只用于给上传前确认。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    repo_url: str = Field(min_length=1, max_length=500)
    source_ref: str = Field("", max_length=255)


class PluginInstallRequest(BaseModel):
    """为当前账号安装插件的参数，安装即分配给自己名下的若干机器人。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    plugin_id: str = Field(min_length=1, max_length=100)
    # 一个账号只安装一次，多台机器人共用这份安装与配置。
    bot_qqs: list[str] = Field(default_factory=list, max_length=50)


class PluginInstallationUpdateRequest(BaseModel):
    """修改插件安装的参数，未提交的字段表示不修改。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    is_enabled: bool | None = None
    config: dict[str, Any] | None = None
    # 只能收窄管理员已批准的能力，不能超出该范围。
    granted_capabilities: list[str] | None = None
    # 本安装生效的机器人范围，提交空列表表示取消全部分配。
    bot_qqs: list[str] | None = Field(default=None, max_length=50)


class PluginUpgradeRequest(BaseModel):
    """升级插件安装的参数。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    # 留空表示升级到插件当前生效版本；填已审核的旧版本号即为回滚。
    version: str = Field(default="", max_length=30)


class PluginReviewApproveRequest(BaseModel):
    """审核通过插件版本的参数。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    plugin_id: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=30)
    # 为空表示只批准非高风险能力，高风险能力必须在审核面板显式勾选。
    granted_capabilities: list[str] | None = None
    comment: str = Field(default="", max_length=2000)


class PluginReviewRejectRequest(BaseModel):
    """驳回插件版本的参数，驳回必须填写原因。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    plugin_id: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=30)
    comment: str = Field(min_length=1, max_length=2000)


class PluginPublishRequest(BaseModel):
    """上架插件的参数。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    plugin_id: str = Field(min_length=1, max_length=100)
    # 填写时把该版本切换为当前生效版本，留空时沿用插件已有版本。
    version: str = Field(default="", max_length=30)


class PluginTrustGrantRequest(BaseModel):
    """把用户加入代码插件信任名单的参数。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    user_id: int = Field(ge=1)
    reason: str = Field(default="", max_length=255)
