"""
插件安装与机器人的分配关系读写。

功能：
1. 校验一批机器人是否都属于当前账号，管理员沿用既有的越权例外；
2. 批量读取安装实例已分配的机器人，供列表与详情序列化使用；
3. 按目标集合重算分配关系：新增的插入、去掉的软删除、曾经删除的复活，不产生重复记录。

约定：
- 本模块只处理分配关系，不判断插件是否上架、版本是否通过审核，那些规则留在安装服务；
- 分配关系只做软删除，保留历史，便于审计哪台机器人曾经装过什么插件；
- 所有写操作由调用方统一提交事务，本模块不自行 commit。
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.permissions import is_admin
from app.common.responses import BusinessError
from app.common.time_utils import beijing_now
from app.models.entities import BotAccount, User
from app.models.plugin import PluginInstallationBot


logger = logging.getLogger("uvicorn.error")

# 机器人不存在
CODE_BOT_NOT_FOUND = 40430
# 无权操作该机器人上的插件安装
CODE_INSTALL_FORBIDDEN = 40383


async def resolve_bot_qqs(
    session: AsyncSession, user: User, bot_qqs: list[str]
) -> list[str]:
    """
    校验并整理目标机器人列表。

    Args:
        session: 数据库会话。
        user: 当前登录用户。
        bot_qqs: 前端提交的机器人 QQ 列表，允许为空。
    Returns:
        去重、去空格后的机器人 QQ 列表，顺序与入参一致。
    Raises:
        BusinessError: 机器人不存在或不属于当前账号。
    """
    ordered: list[str] = []
    for item in bot_qqs:
        bot_qq = str(item or "").strip()
        if bot_qq and bot_qq not in ordered:
            ordered.append(bot_qq)
    if not ordered:
        return []
    bots = (
        await session.scalars(
            select(BotAccount).where(
                BotAccount.bot_qq.in_(ordered), BotAccount.is_deleted.is_(False)
            )
        )
    ).all()
    owned = {bot.bot_qq: bot for bot in bots}
    for bot_qq in ordered:
        bot = owned.get(bot_qq)
        if bot is None:
            raise BusinessError(f"机器人 {bot_qq} 不存在，请先在机器人管理中添加", CODE_BOT_NOT_FOUND)
        if not is_admin(user) and bot.owner_id != user.id:
            raise BusinessError(
                f"无权为机器人 {bot_qq} 分配插件，请确认该机器人属于当前账号",
                CODE_INSTALL_FORBIDDEN,
            )
    return ordered


async def load_binding_map(
    session: AsyncSession, installation_ids: set[int]
) -> dict[int, list[str]]:
    """
    批量读取安装实例已分配的机器人。

    Args:
        session: 数据库会话。
        installation_ids: 安装记录 ID 集合。
    Returns:
        安装记录 ID 到机器人 QQ 升序列表的映射，未分配的空列表。
    """
    targets = {item for item in installation_ids if item}
    if not targets:
        return {}
    rows = (
        await session.scalars(
            select(PluginInstallationBot)
            .where(
                PluginInstallationBot.installation_id.in_(targets),
                PluginInstallationBot.is_deleted.is_(False),
            )
            .order_by(PluginInstallationBot.bot_qq)
        )
    ).all()
    result: dict[int, list[str]] = {item: [] for item in targets}
    for row in rows:
        result.setdefault(row.installation_id, []).append(row.bot_qq)
    return result


async def replace_bindings(
    session: AsyncSession,
    installation_id: int,
    owner_id: int,
    bot_qqs: list[str],
) -> list[str]:
    """
    把安装实例的机器人分配重算为目标集合。

    Args:
        session: 数据库会话。
        installation_id: 安装记录 ID。
        owner_id: 安装记录归属用户，写入分配记录用于隔离。
        bot_qqs: 目标机器人 QQ 列表，已去重。
    Returns:
        重算后的机器人 QQ 升序列表。
    """
    targets = {item for item in bot_qqs if item}
    rows = (
        await session.scalars(
            select(PluginInstallationBot).where(
                PluginInstallationBot.installation_id == installation_id
            )
        )
    ).all()
    existing = {row.bot_qq: row for row in rows}
    now = beijing_now()
    for bot_qq, row in existing.items():
        if bot_qq in targets:
            if row.is_deleted:
                row.is_deleted = False
                row.owner_id = owner_id
                row.updated_at = now
            continue
        if not row.is_deleted:
            row.is_deleted = True
            row.updated_at = now
    for bot_qq in targets:
        if bot_qq in existing:
            continue
        session.add(
            PluginInstallationBot(
                owner_id=owner_id,
                installation_id=installation_id,
                bot_qq=bot_qq,
            )
        )
    await session.flush()
    return sorted(targets)


async def soft_delete_bindings(session: AsyncSession, installation_id: int) -> None:
    """
    软删除某个安装实例的全部机器人分配。

    Args:
        session: 数据库会话。
        installation_id: 安装记录 ID。
    """
    rows = (
        await session.scalars(
            select(PluginInstallationBot).where(
                PluginInstallationBot.installation_id == installation_id,
                PluginInstallationBot.is_deleted.is_(False),
            )
        )
    ).all()
    now = beijing_now()
    for row in rows:
        row.is_deleted = True
        row.updated_at = now
    await session.flush()
