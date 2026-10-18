"""FastAPI 数据库会话、登录用户和服务令牌依赖。"""

from collections.abc import AsyncIterator

from fastapi import Request
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

