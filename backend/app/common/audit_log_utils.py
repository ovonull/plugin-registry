"""
日志类服务的公共工具。

功能：
1. 统一解析前端提交的北京时间筛选字符串；
2. 统一安全回滚失败会话、批量查询用户名称和数据字典名称，供登录日志、邮件日志等服务复用。
"""

from datetime import datetime
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.responses import BusinessError
from app.models.entities import DataDictionary, User


# 支持的时间格式：完整时间、前端 datetime-local 以及纯日期（纯日期结束时间按当天 23:59:59 处理）。
TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M")
DATE_FORMAT = "%Y-%m-%d"


def parse_time_filter(value: str, is_end: bool) -> datetime | None:
    """
    把前端提交的北京时间字符串解析为 datetime。

    Args:
        value: 时间字符串，支持 YYYY-MM-DD、YYYY-MM-DD HH:MM:SS、YYYY-MM-DDTHH:MM(S)。
        is_end: 是否为结束时间，纯日期时按当天 23:59:59 处理。
    Returns:
        解析后的 datetime，未填写时返回 None。
    Raises:
        BusinessError: 时间格式不正确。
    """
    text = (value or "").strip()
    if not text:
        return None
    if len(text) == 10:
        try:
            parsed = datetime.strptime(text, DATE_FORMAT)
        except ValueError as exc:
            raise BusinessError(
                "时间筛选格式不正确，请使用 2026-01-01 或 2026-01-01 10:00:00", 40003
            ) from exc
        return parsed.replace(hour=23, minute=59, second=59) if is_end else parsed
    for pattern in TIME_FORMATS:
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    raise BusinessError(
        "时间筛选格式不正确，请使用 2026-01-01 或 2026-01-01 10:00:00", 40003
    )


async def rollback_quietly(
    session: AsyncSession, logger: logging.Logger, context: str
) -> None:
    """
    回滚处于失败状态的会话，回滚本身失败时记录错误日志。

    Args:
        session: 数据库会话。
        logger: 调用方的日志记录器。
        context: 日志上下文，说明是哪个动作触发的回滚。
    Returns:
        None
    """
    try:
        await session.rollback()
    except Exception as exc:
        logger.error(
            "%s回滚会话异常：类型=%s，错误=%s",
            context,
            type(exc).__name__,
            exc,
            exc_info=True,
        )


async def load_owner_names(session: AsyncSession, owner_ids: set[int]) -> dict[int, str]:
    """
    批量查询用户在系统中的用户名。

    Args:
        session: 数据库会话。
        owner_ids: 需要查询的用户 id 集合。
    Returns:
        用户 id 到用户名的映射。
    """
    if not owner_ids:
        return {}
    rows = (
        await session.execute(
            select(User.id, User.username).where(User.id.in_(owner_ids))
        )
    ).all()
    return {row[0]: row[1] for row in rows}


async def load_dictionary_names(
    session: AsyncSession, dict_type: str
) -> dict[str, str]:
    """
    读取数据字典中指定类型的编码到中文名称映射。

    Args:
        session: 数据库会话。
        dict_type: 字典类型编码。
    Returns:
        字典项编码到中文名称的映射。
    """
    rows = (
        await session.execute(
            select(DataDictionary.item_code, DataDictionary.item_name).where(
                DataDictionary.dict_type == dict_type,
                DataDictionary.is_deleted.is_(False),
            )
        )
    ).all()
    return {code: name for code, name in rows}
