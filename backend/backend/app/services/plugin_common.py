"""
插件模块共用的错误码与轻量批量查询。

功能：
1. 集中定义安装与配置环节共用的错误码，避免多个模块各写一份；
2. 提供用户、插件主表与版本表的批量读取，减少各处重复 SQL。

约定：本模块只放与业务判断无关的共用件，业务规则留在各自的服务中。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import User
from app.models.plugin import Plugin, PluginVersion


# 安装或插件配置参数不完整、不合法
CODE_INSTALL_REQUEST_INVALID = 40110


async def load_user_names(
    session: AsyncSession, user_ids: set[int]
) -> dict[int, str]:
    """
    批量读取用户名。

    Args:
        session: 数据库会话。
        user_ids: 需要查询的用户 ID 集合。
    Returns:
        用户 ID 到用户名的映射。
    """
    targets = {user_id for user_id in user_ids if user_id}
    if not targets:
        return {}
    rows = await session.execute(
        select(User.id, User.username).where(User.id.in_(targets))
    )
    return {row.id: row.username for row in rows}


async def load_plugins(
    session: AsyncSession, plugin_ids: set[str]
) -> dict[str, Plugin]:
    """
    批量读取插件主表记录。

    Args:
        session: 数据库会话。
        plugin_ids: 插件标识集合。
    Returns:
        插件标识到插件记录的映射。
    """
    targets = {plugin_id for plugin_id in plugin_ids if plugin_id}
    if not targets:
        return {}
    rows = (
        await session.scalars(select(Plugin).where(Plugin.plugin_id.in_(targets)))
    ).all()
    return {row.plugin_id: row for row in rows}


async def load_versions(
    session: AsyncSession, version_ids: set[int]
) -> dict[int, PluginVersion]:
    """
    批量读取插件版本记录。

    Args:
        session: 数据库会话。
        version_ids: 版本记录 ID 集合。
    Returns:
        版本 ID 到版本记录的映射。
    """
    targets = {version_id for version_id in version_ids if version_id}
    if not targets:
        return {}
    rows = (
        await session.scalars(
            select(PluginVersion).where(PluginVersion.id.in_(targets))
        )
    ).all()
    return {row.id: row for row in rows}
