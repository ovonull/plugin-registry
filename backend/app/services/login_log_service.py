"""
登录日志服务。

功能：
1. 追加写入用户登录日志：登录成功与失败都必须落库，只追加、不修改、不删除；
2. 按当前用户权限分页查询登录日志，支持关键字、登录方式和时间区间筛选。
"""

from collections.abc import Awaitable, Callable
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
from app.common.permissions import owner_scope
from app.common.request_utils import CLIENT_IP_MAX_LENGTH
from app.common.responses import BusinessError
from app.common.time_utils import format_beijing
from app.models.entities import User
from app.models.login_log import LoginLog


logger = logging.getLogger(__name__)

# 字段长度上限，与模型列长度保持一致，避免超出列长度导致写入失败。
ACCOUNT_MAX_LENGTH = 255
RESULT_MESSAGE_MAX_LENGTH = 255


class LoginLogService:
    """登录日志的追加写入与按权限分页查询。"""

    async def audit_login(
        self,
        session: AsyncSession,
        login_type: str,
        login_account: str,
        client_ip: str,
        authenticate: Callable[[], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """
        执行登录认证并写入登录日志，成功与失败都必须落库。

        Args:
            session: 当前请求的数据库会话。
            login_type: 登录方式编码，取值来自数据字典 login_type。
            login_account: 本次尝试的登录账号。
            client_ip: 客户端 IP，优先取 X-Forwarded-For。
            authenticate: 真正执行认证的异步回调，返回登录结果。
        Returns:
            认证回调返回的登录结果。
        Raises:
            BusinessError: 认证失败，先把失败原因写入登录日志再抛出。
        """
        try:
            data = await authenticate()
        except BusinessError as exc:
            # 失败分支必须落库，禁止在异常分支直接返回而不写登录日志。
            await self.record(
                session,
                owner_id=None,
                login_account=login_account,
                login_type=login_type,
                is_success=False,
                client_ip=client_ip,
                result_message=exc.message,
            )
            raise
        except Exception as exc:
            # 非业务异常（数据库故障、第三方服务异常等）同样属于登录失败，必须先落库再抛出，
            # 避免异常冒泡到全局异常处理器后绕过登录日志，造成失败记录遗漏。
            await self.record(
                session,
                owner_id=None,
                login_account=login_account,
                login_type=login_type,
                is_success=False,
                client_ip=client_ip,
                result_message=f"系统异常：{type(exc).__name__}：{exc}",
            )
            raise
        await self.record(
            session,
            owner_id=data["user"]["id"],
            login_account=login_account,
            login_type=login_type,
            is_success=True,
            client_ip=client_ip,
            result_message="登录成功",
        )
        return data


    async def record(
        self,
        session: AsyncSession,
        owner_id: int | None,
        login_account: str | None,
        login_type: str,
        is_success: bool,
        client_ip: str | None = None,
        result_message: str | None = None,
    ) -> None:
        """
        追加一条登录日志。

        登录成功与失败都必须调用本方法。写日志本身失败时只记录错误日志，
        不允许影响登录主流程，也不允许静默失败（必须有 ERROR 日志）。

        Args:
            session: 当前请求的数据库会话。
            owner_id: 登录成功时的用户 id，登录失败传 None。
            login_account: 登录账号（用户名或邮箱），失败时也记录尝试账号。
            login_type: 登录方式编码，取值来自数据字典 login_type。
            is_success: 是否登录成功。
            client_ip: 客户端 IP，优先取 X-Forwarded-For。
            result_message: 结果说明，失败时记录具体失败原因。
        Returns:
            None
        """
        account = (login_account or "").strip()[:ACCOUNT_MAX_LENGTH] or None
        message = (result_message or "").strip()[:RESULT_MESSAGE_MAX_LENGTH] or None
        try:
            session.add(
                LoginLog(
                    owner_id=owner_id,
                    login_account=account,
                    login_type=login_type,
                    is_success=is_success,
                    client_ip=(client_ip or "")[:CLIENT_IP_MAX_LENGTH] or None,
                    result_message=message,
                )
            )
            await session.commit()
        except Exception as exc:
            logger.error(
                "登录日志写入失败：账号=%s，登录方式=%s，结果=%s，客户端IP=%s，用户标识=%s，异常类型=%s，错误=%s",
                account,
                login_type,
                "成功" if is_success else "失败",
                client_ip,
                owner_id,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            await rollback_quietly(session, logger, "登录日志写入失败后")
            return
        logger.info(
            "登录日志已写入：账号=%s，登录方式=%s，结果=%s，客户端IP=%s，用户标识=%s，说明=%s",
            account,
            login_type,
            "成功" if is_success else "失败",
            client_ip,
            owner_id,
            message,
        )

    async def list_logs(
        self,
        session: AsyncSession,
        user: User,
        page: int,
        page_size: int,
        keyword: str = "",
        login_type: str = "",
        start_time: str = "",
        end_time: str = "",
    ) -> dict[str, Any]:
        """
        分页查询登录日志，按发生时间倒序返回。

        普通用户只能查看自己的登录记录，管理员可以查看全部记录。

        Args:
            session: 数据库会话。
            user: 当前登录用户，用于限定数据范围。
            page: 页码，从 1 开始。
            page_size: 每页条数。
            keyword: 关键字，匹配登录账号、客户端 IP 和结果说明。
            login_type: 登录方式编码筛选。
            start_time: 开始时间（北京时间字符串）。
            end_time: 结束时间（北京时间字符串）。
        Returns:
            含 items、page、page_size、total 和 pages 的分页结果。
        Raises:
            BusinessError: 时间筛选格式不正确。
        """
        conditions = self._build_conditions(keyword, login_type, start_time, end_time)
        scope = owner_scope(user)
        if scope is not None:
            conditions.append(LoginLog.owner_id == scope)
        total = int(
            await session.scalar(select(func.count(LoginLog.id)).where(*conditions)) or 0
        )
        rows = (
            await session.scalars(
                select(LoginLog)
                .where(*conditions)
                .order_by(LoginLog.created_at.desc(), LoginLog.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        type_names = await load_dictionary_names(session, "login_type")
        owner_names = await load_owner_names(
            session, {row.owner_id for row in rows if row.owner_id is not None}
        )
        return {
            "items": [
                self._serialize(row, type_names, owner_names) for row in rows
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    @staticmethod
    def _build_conditions(
        keyword: str,
        login_type: str,
        start_time: str,
        end_time: str,
    ) -> list[Any]:
        """
        构造登录日志查询条件。

        Args:
            keyword: 关键字筛选。
            login_type: 登录方式编码筛选。
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
                    LoginLog.login_account.contains(keyword, autoescape=True),
                    LoginLog.client_ip.contains(keyword, autoescape=True),
                    LoginLog.result_message.contains(keyword, autoescape=True),
                )
            )
        if login_type:
            conditions.append(LoginLog.login_type == login_type)
        start = parse_time_filter(start_time, is_end=False)
        end = parse_time_filter(end_time, is_end=True)
        if start is not None:
            conditions.append(LoginLog.created_at >= start)
        if end is not None:
            conditions.append(LoginLog.created_at <= end)
        if start is not None and end is not None and start > end:
            raise BusinessError("开始时间不能晚于结束时间", 40003)
        return conditions

    @staticmethod
    def _serialize(
        log: LoginLog,
        type_names: dict[str, str],
        owner_names: dict[int, str],
    ) -> dict[str, Any]:
        """
        把登录日志模型转换为前端展示结构。

        Args:
            log: 登录日志模型。
            type_names: 登录方式编码到中文名称的映射。
            owner_names: 用户 id 到用户名的映射。
        Returns:
            前端展示用的登录日志字典。
        """
        return {
            "id": log.id,
            "owner_id": log.owner_id,
            "owner_username": owner_names.get(log.owner_id, "") if log.owner_id else "",
            "login_account": log.login_account,
            "login_type": log.login_type,
            "login_type_name": type_names.get(log.login_type, log.login_type),
            "is_success": log.is_success,
            "client_ip": log.client_ip,
            "result_message": log.result_message,
            "created_at": format_beijing(log.created_at),
        }