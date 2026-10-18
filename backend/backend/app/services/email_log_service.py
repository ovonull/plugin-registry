"""
邮件日志服务。

功能：
1. 追加写入邮件发送日志：任何触发发信的操作（成功、失败、被限流或配置缺失拒绝）都必须落库，
   只追加、不修改、不删除；
2. 仅管理员分页查询邮件日志，支持邮箱关键字、触发操作、发送结果和时间区间筛选。
"""

import logging
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit_log_utils import (
    load_dictionary_names,
    load_owner_names,
    parse_time_filter,
    rollback_quietly,
)
from app.common.permissions import is_admin
from app.common.request_utils import CLIENT_IP_MAX_LENGTH
from app.common.responses import BusinessError
from app.common.time_utils import format_beijing
from app.models.email_log import EmailLog
from app.models.entities import User


logger = logging.getLogger(__name__)

# 字段长度上限，与模型列长度保持一致，避免超出列长度导致写入失败。
EMAIL_MAX_LENGTH = 255
ACTION_MAX_LENGTH = 30
RESULT_MESSAGE_MAX_LENGTH = 255
# 邮件日志触发操作的数据字典类型。
ACTION_DICT_TYPE = "email_log_action"
# 发信用途（purpose）与邮件日志触发操作编码的映射。
# 新增任何发信入口时必须在此登记，并把对应取值同步写入 email_log_action 数据字典。
PURPOSE_ACTION_CODES = {
    "login": "login_code",
    "register": "register_code",
    "bind": "bind_email",
}


def action_code_for_purpose(purpose: str) -> str:
    """
    把发信用途转换为邮件日志触发操作编码，未登记的用途原样返回。

    Args:
        purpose: 发信用途，取值 login / register / bind。
    Returns:
        邮件日志触发操作编码。
    """
    return PURPOSE_ACTION_CODES.get(purpose, purpose)


class EmailLogService:
    """邮件日志的追加写入与仅管理员分页查询。"""

    async def record(
        self,
        session: AsyncSession,
        owner_id: int | None,
        email: str | None,
        action: str,
        is_success: bool,
        client_ip: str | None = None,
        result_message: str | None = None,
    ) -> None:
        """
        追加一条邮件日志。

        任何触发发信的操作都必须调用本方法。写日志本身失败时只记录错误日志，
        不允许影响发信主流程，也不允许静默失败（必须有 ERROR 日志）。

        Args:
            session: 当前请求的数据库会话。
            owner_id: 触发用户 id，找不到对应用户时传 None。
            email: 收件邮箱。
            action: 触发操作编码，取值来自数据字典 email_log_action。
            is_success: 是否发送成功。
            client_ip: 客户端 IP，优先取 X-Forwarded-For。
            result_message: 结果说明，失败时记录具体失败原因。
        Returns:
            None
        """
        mail = (email or "").strip()[:EMAIL_MAX_LENGTH]
        code = (action or "").strip()[:ACTION_MAX_LENGTH] or "-"
        message = (result_message or "").strip()[:RESULT_MESSAGE_MAX_LENGTH] or None
        try:
            session.add(
                EmailLog(
                    owner_id=owner_id,
                    email=mail,
                    action=code,
                    is_success=is_success,
                    client_ip=(client_ip or "")[:CLIENT_IP_MAX_LENGTH] or None,
                    result_message=message,
                )
            )
            await session.commit()
        except Exception as exc:
            logger.error(
                "邮件日志写入失败：邮箱=%s，触发操作=%s，发送结果=%s，客户端IP=%s，用户标识=%s，异常类型=%s，异常=%s",
                mail,
                code,
                "成功" if is_success else "失败",
                client_ip,
                owner_id,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            await rollback_quietly(session, logger, "邮件日志写入失败后")
            return
        logger.info(
            "邮件日志已写入：邮箱=%s，触发操作=%s，发送结果=%s，客户端IP=%s，用户标识=%s，说明=%s",
            mail,
            code,
            "成功" if is_success else "失败",
            client_ip,
            owner_id,
            message,
        )

    async def record_send(
        self,
        session: AsyncSession,
        purpose: str,
        email: str | None,
        is_success: bool,
        owner_id: int | None = None,
        client_ip: str | None = None,
        result_message: str | None = None,
    ) -> None:
        """
        记录一次邮件发送尝试，内部把发信用途转换为触发操作编码。

        Args:
            session: 当前请求的数据库会话。
            purpose: 发信用途，取值 login / register / bind。
            email: 收件邮箱。
            is_success: 是否发送成功。
            owner_id: 触发用户 id，找不到对应用户时传 None。
            client_ip: 客户端 IP，优先取 X-Forwarded-For。
            result_message: 结果说明，失败时记录具体失败原因。
        Returns:
            None
        """
        await self.record(
            session,
            owner_id,
            email,
            action_code_for_purpose(purpose),
            is_success,
            client_ip,
            result_message,
        )

    async def list_logs(
        self,
        session: AsyncSession,
        user: User,
        page: int,
        page_size: int,
        keyword: str = "",
        action: str = "",
        result: str = "",
        start_time: str = "",
        end_time: str = "",
    ) -> dict[str, Any]:
        """
        仅管理员分页查询邮件日志，按发生时间倒序返回。

        Args:
            session: 数据库会话。
            user: 当前登录用户，仅管理员可访问。
            page: 页码，从 1 开始。
            page_size: 每页条数。
            keyword: 关键字，匹配收件邮箱、客户端 IP 和结果说明。
            action: 触发操作编码筛选。
            result: 发送结果筛选，success / failed。
            start_time: 开始时间（北京时间字符串）。
            end_time: 结束时间（北京时间字符串）。
        Returns:
            含 items、page、page_size、total 和 pages 的分页结果。
        Raises:
            BusinessError: 非管理员访问或时间筛选格式不正确。
        """
        if not is_admin(user):
            raise BusinessError("仅管理员可以查看邮件日志", 40311)
        conditions = self._build_conditions(keyword, action, result, start_time, end_time)
        total = int(
            await session.scalar(select(func.count(EmailLog.id)).where(*conditions)) or 0
        )
        rows = (
            await session.scalars(
                select(EmailLog)
                .where(*conditions)
                .order_by(EmailLog.created_at.desc(), EmailLog.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        action_names = await load_dictionary_names(session, ACTION_DICT_TYPE)
        owner_names = await load_owner_names(
            session, {row.owner_id for row in rows if row.owner_id is not None}
        )
        return {
            "items": [
                self._serialize(row, action_names, owner_names) for row in rows
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    @staticmethod
    def _build_conditions(
        keyword: str,
        action: str,
        result: str,
        start_time: str,
        end_time: str,
    ) -> list[Any]:
        """
        构造邮件日志查询条件。

        Args:
            keyword: 关键字筛选。
            action: 触发操作编码筛选。
            result: 发送结果筛选，success / failed。
            start_time: 开始时间字符串。
            end_time: 结束时间字符串。
        Returns:
            SQLAlchemy 查询条件列表。
        Raises:
            BusinessError: 时间筛选格式不正确。
        """
        conditions: list[Any] = []
        if keyword:
            conditions.append(
                or_(
                    EmailLog.email.contains(keyword, autoescape=True),
                    EmailLog.client_ip.contains(keyword, autoescape=True),
                    EmailLog.result_message.contains(keyword, autoescape=True),
                )
            )
        if action:
            conditions.append(EmailLog.action == action)
        if result == "success":
            conditions.append(EmailLog.is_success.is_(True))
        elif result == "failed":
            conditions.append(EmailLog.is_success.is_(False))
        start = parse_time_filter(start_time, is_end=False)
        end = parse_time_filter(end_time, is_end=True)
        if start is not None:
            conditions.append(EmailLog.created_at >= start)
        if end is not None:
            conditions.append(EmailLog.created_at <= end)
        if start is not None and end is not None and start > end:
            raise BusinessError("开始时间不能晚于结束时间", 40003)
        return conditions

    @staticmethod
    def _serialize(
        log: EmailLog,
        action_names: dict[str, str],
        owner_names: dict[int, str],
    ) -> dict[str, Any]:
        """
        把邮件日志模型转换为前端展示结构。

        Args:
            log: 邮件日志模型。
            action_names: 触发操作编码到中文名称的映射。
            owner_names: 用户 id 到用户名的映射。
        Returns:
            前端展示用的邮件日志字典。
        """
        return {
            "id": log.id,
            "owner_id": log.owner_id,
            "owner_username": owner_names.get(log.owner_id, "") if log.owner_id else "",
            "email": log.email,
            "action": log.action,
            "action_name": action_names.get(log.action, log.action),
            "is_success": log.is_success,
            "client_ip": log.client_ip,
            "result_message": log.result_message,
            "created_at": format_beijing(log.created_at),
        }
