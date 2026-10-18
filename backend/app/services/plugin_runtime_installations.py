"""
运行期插件安装读取。

功能：
1. 按机器人反查该账号分配到这台机器的已启用安装，供事件分发使用；
2. 汇总运行期所需的最小信息：包目录、入口文件、已批准能力、出网白名单与解密后的配置；
3. 插件已下架、版本缺失或安装未启用时一律跳过，不影响事件主流程。

约定：
- 本模块只读不写，安装的增删改一律由 plugin_install_service 负责；
- 只按机器人 + 分配关系取数，天然不会跨账号泄露其他用户的安装。
"""

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plugin import Plugin, PluginInstallation, PluginInstallationBot, PluginVersion
from app.services.plugin_common import load_versions
from app.services.plugin_config_service import load_config
from app.services.plugin_serializers import load_code_list


logger = logging.getLogger("uvicorn.error")


def manifest_domains(version: PluginVersion) -> list[str]:
    """
    从版本清单中取出出网域名白名单。

    Args:
        version: 插件版本记录。
    Returns:
        小写域名列表，清单异常或未声明时返回空列表。
    """
    try:
        manifest = json.loads(version.manifest_json or "{}")
    except json.JSONDecodeError:
        logger.warning(
            "插件版本清单不是合法 JSON，域名白名单按空处理，版本=%s", version.id
        )
        return []
    if not isinstance(manifest, dict):
        return []
    raw = manifest.get("allowed_domains")
    if not isinstance(raw, list):
        return []
    return [str(item).strip().lower() for item in raw if str(item).strip()]


async def load_enabled_installations(
    session: AsyncSession, bot_qq: str
) -> list[dict[str, Any]]:
    """
    读取某机器人上已启用、且插件仍处于上架状态的安装。

    Args:
        session: 数据库会话。
        bot_qq: 机器人 QQ。
    Returns:
        运行期所需的精简信息列表；插件已下架或缺少可用版本时不会出现在结果中。
    """
    bot_key = str(bot_qq or "").strip()
    if not bot_key:
        return []
    # 按机器人反查安装实例：只有该账号把这台机器人分配进生效范围才会命中。
    rows = (
        await session.scalars(
            select(PluginInstallation)
            .join(
                PluginInstallationBot,
                PluginInstallationBot.installation_id == PluginInstallation.id,
            )
            .where(
                PluginInstallationBot.bot_qq == bot_key,
                PluginInstallationBot.is_deleted.is_(False),
                PluginInstallation.is_enabled.is_(True),
                PluginInstallation.is_deleted.is_(False),
            )
        )
    ).all()
    if not rows:
        return []
    published = {
        row.plugin_id
        for row in (
            await session.scalars(
                select(Plugin).where(
                    Plugin.plugin_id.in_({item.plugin_id for item in rows}),
                    Plugin.is_published.is_(True),
                    Plugin.is_deleted.is_(False),
                )
            )
        ).all()
    }
    versions = await load_versions(session, {row.version_id for row in rows})
    result: list[dict[str, Any]] = []
    for row in rows:
        if row.plugin_id not in published:
            continue
        version = versions.get(row.version_id)
        if version is None or not version.package_path or not version.entry:
            logger.warning(
                "插件安装缺少可用版本，跳过运行期分发，插件=%s，机器人=%s",
                row.plugin_id,
                bot_key,
            )
            continue
        result.append(
            {
                "plugin_id": row.plugin_id,
                "installation_id": row.id,
                "owner_id": row.owner_id,
                "bot_qq": bot_key,
                "version_id": row.version_id,
                "entry": version.entry,
                "package_dir": version.package_path,
                "granted_capabilities": load_code_list(row.granted_capabilities_json),
                "allowed_domains": manifest_domains(version),
                "config": load_config(row, version),
            }
        )
    return result
