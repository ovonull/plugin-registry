"""FastAPI 数据库会话、登录用户和服务令牌依赖。"""

from collections.abc import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.responses import BusinessError
from app.common.security import secure_equals
from app.models.entities import User


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """为当前请求提供数据库会话。"""
    async with request.app.state.container.database.session_factory() as session:
        yield session


def extract_bearer_token(request: Request) -> str:
    """从 Authorization 请求头提取 Bearer 令牌。"""
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


async def get_current_user(
    request: Request, session: AsyncSession = None
) -> User:
    """解析当前登录用户，供需要认证的接口复用。"""
    token = extract_bearer_token(request)
    if not token:
        raise BusinessError("请先登录", 40100)
    if session is None:
        async with request.app.state.container.database.session_factory() as own_session:
            return await request.app.state.container.user_service.get_current_user(
                own_session, token
            )
    return await request.app.state.container.user_service.get_current_user(session, token)


def verify_service_token(request: Request) -> None:
    """校验面向业务程序的 OneBot HTTP 动作代理令牌。"""
    expected = request.app.state.container.settings.service_token
    if not expected:
        return
    supplied = request.query_params.get("access_token", "") or extract_bearer_token(
        request
    )
    if not supplied or not secure_equals(supplied, expected):
        raise BusinessError("服务访问令牌无效", 40303)


async def require_registry_access(
    request: Request, session: AsyncSession = Depends(get_session)
) -> str:
    """
    校验插件库对外接口的访问权限。

    接受两种身份：
    1. 下游程序携带的接入令牌（``Authorization: Bearer <token>`` 或 ``X-Registry-Token``），
       需与后台「插件同步 → 接入令牌」里配置的值一致；
    2. 已登录账号的会话令牌，供前端页面直接调用。

    Returns:
        ``service`` 表示接入令牌通过，``user`` 表示登录账号通过。
    Raises:
        BusinessError: 两种方式都没有通过。
    """
    supplied = extract_bearer_token(request) or str(
        request.headers.get("x-registry-token", "")
    ).strip()
    expected = getattr(request.app.state.container.settings, "registry_token", "")
    if expected and supplied and secure_equals(supplied, expected):
        return "service"
    try:
        await request.app.state.container.user_service.get_current_user(
            session, supplied
        )
        return "user"
    except BusinessError as error:
        raise BusinessError(
            "需要登录账号，或提供有效的插件库接入令牌",
            40100,
        ) from error

