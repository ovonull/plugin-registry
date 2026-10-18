"""
插件数据的接口序列化。

功能：
1. 把插件主表、版本表与安装表记录转换成前端统一消费的结构；
2. 容错解析入库的能力数组，脏数据按空数组处理并告警，避免接口整体失败；
3. 审核台与市场页共用同一套字段命名，前端无需按页面分别适配。

说明：能力只返回能力码，中文名称由前端查询 plugin_capability 数据字典获取，
不在本模块与前端重复维护中文映射。
"""

import json
import logging
from typing import Any

from app.common.time_utils import format_beijing
from app.models.plugin import Plugin, PluginInstallation, PluginVersion
from app.services.plugin_config_service import config_schema, load_config


logger = logging.getLogger("uvicorn.error")


def load_code_list(value: str) -> list[str]:
    """
    容错解析入库的能力数组。

    Args:
        value: 数据库中保存的 JSON 字符串。
    Returns:
        能力码列表，内容不合法时返回空数组。
    """
    try:
        loaded = json.loads(value or "[]")
    except json.JSONDecodeError:
        logger.warning("插件能力字段不是合法 JSON，已按空数组处理")
        return []
    if not isinstance(loaded, list):
        return []
    return [str(item) for item in loaded]


def serialize_version(version: PluginVersion) -> dict[str, Any]:
    """将插件版本记录转换为统一接口响应，含申请与批准的能力。"""
    return {
        "id": version.id,
        "plugin_id": version.plugin_id,
        "version": version.version,
        "entry": version.entry,
        "package_path": version.package_path,
        "checksum": version.checksum,
        "source_ref": version.source_ref,
        "review_status": version.review_status,
        "reviewed_by": version.reviewed_by,
        "reviewed_at": format_beijing(version.reviewed_at),
        "review_comment": version.review_comment,
        "min_host_version": version.min_host_version,
        "max_host_version": version.max_host_version,
        "capabilities": load_code_list(version.capabilities_json),
        "granted_capabilities": load_code_list(version.granted_capabilities_json),
        "created_at": format_beijing(version.created_at),
        "updated_at": format_beijing(version.updated_at),
    }


def serialize_plugin(
    plugin: Plugin,
    current_version: PluginVersion | None,
    owner_username: str | None,
) -> dict[str, Any]:
    """
    将插件主表记录转换为统一接口响应。

    Args:
        plugin: 插件主表记录。
        current_version: 当前生效版本，未审核通过时为空。
        owner_username: 提交者用户名。
    Returns:
        插件接口响应数据。
    """
    return {
        "plugin_id": plugin.plugin_id,
        "name": plugin.name,
        "summary": plugin.summary,
        "description": plugin.description,
        "author_name": plugin.author_name,
        "author_url": plugin.author_url,
        "homepage": plugin.homepage,
        "license": plugin.license,
        "source_type": plugin.source_type,
        "repo_url": plugin.repo_url,
        "icon_path": plugin.icon_path,
        "owner_id": plugin.owner_id,
        "owner_username": owner_username or "未知用户",
        "is_published": plugin.is_published,
        "current_version": (
            serialize_version(current_version) if current_version is not None else None
        ),
        "created_at": format_beijing(plugin.created_at),
        "updated_at": format_beijing(plugin.updated_at),
    }


def serialize_installation(
    installation: PluginInstallation,
    plugin: Plugin | None,
    version: PluginVersion | None,
    bot_qqs: list[str] | None = None,
    latest_version: PluginVersion | None = None,
) -> dict[str, Any]:
    """
    将插件安装记录转换为统一接口响应。

    Args:
        installation: 安装记录。
        plugin: 对应的插件主表记录。
        version: 安装时对应的版本记录。
        bot_qqs: 该安装已分配的机器人 QQ 列表，未传入时按空列表返回。
        latest_version: 插件当前生效版本（即最新已上架版本），用于判断能否升级。
    Returns:
        含配置项定义、已批准能力、生效能力与生效机器人的安装信息，
        以及新版本号、新版本已批准能力与是否可升级。
    """
    # 只有插件仍在上架、且新版本已通过审核时才提示升级，
    # 否则用户点了也升不了，徒增困惑。
    upgradable = bool(
        latest_version is not None
        and latest_version.id != installation.version_id
        and latest_version.review_status == "approved"
        and plugin is not None
        and not plugin.is_deleted
        and plugin.is_published
    )
    return {
        "id": installation.id,
        "owner_id": installation.owner_id,
        "plugin_id": installation.plugin_id,
        "plugin_name": plugin.name if plugin is not None else "",
        "plugin_summary": plugin.summary if plugin is not None else "",
        "plugin_published": bool(plugin.is_published) if plugin is not None else False,
        # 仓库来源的插件支持「检查更新」，压缩包来源没有关联仓库。
        "plugin_source_type": plugin.source_type if plugin is not None else "",
        "plugin_repo_url": plugin.repo_url if plugin is not None else "",
        "bot_qqs": sorted(bot_qqs or []),
        "version_id": installation.version_id,
        "version": version.version if version is not None else "",
        "is_enabled": installation.is_enabled,
        "capabilities": (
            load_code_list(version.capabilities_json) if version else []
        ),
        "approved_capabilities": (
            load_code_list(version.granted_capabilities_json) if version else []
        ),
        "granted_capabilities": load_code_list(
            installation.granted_capabilities_json
        ),
        "config": load_config(installation, version),
        "config_schema": config_schema(version),
        "has_update": upgradable,
        "latest_version": latest_version.version if latest_version is not None else "",
        "latest_version_id": (
            latest_version.id if latest_version is not None else None
        ),
        # 新版本申请与已批准的能力，前端据此提示「升级后需要补勾哪些能力」。
        "latest_capabilities": (
            load_code_list(latest_version.capabilities_json)
            if latest_version is not None
            else []
        ),
        "latest_approved_capabilities": (
            load_code_list(latest_version.granted_capabilities_json)
            if latest_version is not None
            else []
        ),
        "created_at": format_beijing(installation.created_at),
        "updated_at": format_beijing(installation.updated_at),
    }


def serialize_review_version(
    version: PluginVersion,
    plugin: Plugin | None,
    user_names: dict[int, str],
) -> dict[str, Any]:
    """
    把版本记录与插件信息合并为审核台可用的结构。

    Args:
        version: 版本记录。
        plugin: 插件主表记录。
        user_names: 用户 ID 到用户名的映射。
    Returns:
        审核台列表项。
    """
    return {
        "id": version.id,
        "plugin_id": version.plugin_id,
        "plugin_name": plugin.name if plugin is not None else "",
        "plugin_summary": plugin.summary if plugin is not None else "",
        # 清单里声明的作者，与提交人账号是两回事，审核时两者都要看得到。
        "author_name": plugin.author_name if plugin is not None else "",
        "is_published": bool(plugin.is_published) if plugin is not None else False,
        "is_current": bool(
            plugin is not None and plugin.current_version_id == version.id
        ),
        "version": version.version,
        "entry": version.entry,
        "checksum": version.checksum,
        "source_ref": version.source_ref,
        "review_status": version.review_status,
        "reviewed_by": version.reviewed_by,
        "reviewed_username": (
            user_names.get(version.reviewed_by, "") if version.reviewed_by else ""
        ),
        "reviewed_at": format_beijing(version.reviewed_at),
        "review_comment": version.review_comment,
        "min_host_version": version.min_host_version,
        "max_host_version": version.max_host_version,
        "capabilities": load_code_list(version.capabilities_json),
        "granted_capabilities": load_code_list(version.granted_capabilities_json),
        "owner_id": version.owner_id,
        "owner_username": user_names.get(version.owner_id, "未知用户"),
        "created_at": format_beijing(version.created_at),
        "updated_at": format_beijing(version.updated_at),
    }


def serialize_plugin_state(plugin: Plugin) -> dict[str, Any]:
    """返回插件上架状态，供审核台操作后即时刷新列表，不重复下发完整详情。"""
    return {
        "plugin_id": plugin.plugin_id,
        "name": plugin.name,
        "is_published": plugin.is_published,
        "current_version_id": plugin.current_version_id,
        "updated_at": format_beijing(plugin.updated_at),
    }
