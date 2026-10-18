"""
插件动作审计服务。

功能：
1. 以只追加方式记录每次动作调用：动作名、所属能力、脱敏参数、结果与耗时；
2. 参数写入前统一脱敏与截断，密钥类字段永远不会落库；
3. 提供分页查询，管理员可见全部，普通用户只能看到自己名下机器人的记录。

约定：
- 审计写入失败绝不能影响事件主流程，调用方只记录日志不抛出；
- 本表只追加，不提供修改与删除入口。
"""

import json
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.permissions import owner_scope
from app.common.time_utils import format_beijing
from app.models.entities import User
from app.models.plugin import PluginActionLog


# 结果码：成功 / 能力未批准 / 执行失败 / 超时
RESULT_OK = "ok"
RESULT_DENIED = "denied"
RESULT_ERROR = "error"
RESULT_TIMEOUT = "timeout"

# 参数中出现的敏感键名片段，命中即整体替换为掩码。
SENSITIVE_HINTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "cookie",
    "authorization",
    "api_key",
    "apikey",
)
SENSITIVE_MASK = "***"
MAX_PARAMS_CHARS = 1000


def _mask(value: Any) -> Any:
    """递归掩码字典中的敏感字段。"""
    if isinstance(value, dict):
        return {
            str(key): (
                SENSITIVE_MASK
                if any(hint in str(key).lower() for hint in SENSITIVE_HINTS)
                else _mask(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_mask(item) for item in value]
    return value


def sanitize_params(params: dict[str, Any] | None) -> str:
    """
    把动作参数脱敏并序列化成审计字段。

    Args:
        params: 原始参数。
    Returns:
        脱敏后的 JSON 文本，超长时截断。
    """
    if not params:
        return ""
    try:
        text = json.dumps(_mask(params), ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(params)
    return text[:MAX_PARAMS_CHARS]


def serialize_action_log(row: PluginActionLog) -> dict[str, Any]:
    """把审计记录转换成接口返回结构。"""
    return {
        "id": row.id,
        "plugin_id": row.plugin_id,
        "version_id": row.version_id,
        "owner_id": row.owner_id,
        "bot_qq": row.bot_qq,
        "group_id": row.group_id,
        "user_id": row.user_id,
        "action_name": row.action_name,
        "capability": row.capability,
        "params": row.params_json,
        "result_code": row.result_code,
        "result_message": row.result_message,
        "duration_ms": row.duration_ms,
        "created_at": format_beijing(row.created_at),
    }


class PluginActionLogService:
    """插件动作审计的写入与查询。"""

    @staticmethod
    async def append(
        session: AsyncSession,
        *,
        plugin_id: str,
        version_id: int | None,
        owner_id: int | None,
        bot_qq: str,
        group_id: str,
        user_id: str,
        action_name: str,
        capability: str,
        params: dict[str, Any] | None,
        result_code: str,
        result_message: str = "",
        duration_ms: int = 0,
    ) -> None:
        """追加一条审计记录。"""
        session.add(
            PluginActionLog(
                plugin_id=plugin_id,
                version_id=version_id,
                owner_id=owner_id,
                bot_qq=bot_qq,
                group_id=group_id,
                user_id=user_id,
                action_name=action_name,
                capability=capability,
                params_json=sanitize_params(params),
                result_code=result_code,
                result_message=str(result_message or "")[:1000],
                duration_ms=max(int(duration_ms or 0), 0),
            )
        )

    @staticmethod
    async def list_logs(
        session: AsyncSession,
        user: User,
        page: int,
        page_size: int,
        plugin_id: str = "",
        bot_qq: str = "",
        result_code: str = "",
        keyword: str = "",
    ) -> dict[str, Any]:
        """
        分页查询审计记录。

        Args:
            session: 数据库会话。
            user: 当前登录用户。
            page: 页码，从 1 开始。
            page_size: 每页条数。
            plugin_id: 按插件筛选。
            bot_qq: 按机器人筛选。
            result_code: 按结果筛选。
            keyword: 动作名关键字。
        Returns:
            含 items、page、page_size、total、pages 的分页结果。
        """
        conditions = []
        if plugin_id:
            conditions.append(PluginActionLog.plugin_id == plugin_id)
        if bot_qq:
            conditions.append(PluginActionLog.bot_qq == bot_qq)
        if result_code:
            conditions.append(PluginActionLog.result_code == result_code)
        if keyword:
            conditions.append(
                or_(
                    PluginActionLog.action_name.contains(keyword, autoescape=True),
                    PluginActionLog.capability.contains(keyword, autoescape=True),
                )
            )
        scope = owner_scope(user)
        if scope is not None:
            conditions.append(PluginActionLog.owner_id == scope)

        total = int(
            await session.scalar(
                select(func.count(PluginActionLog.id)).where(*conditions)
            )
            or 0
        )
        rows = (
            await session.scalars(
                select(PluginActionLog)
                .where(*conditions)
                .order_by(PluginActionLog.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return {
            "items": [serialize_action_log(row) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max((total + page_size - 1) // page_size, 1),
        }
