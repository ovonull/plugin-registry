"""注册、登录、当前用户和个人设置接口。"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.request_utils import extract_client_ip
from app.common.responses import success
from app.dependencies import extract_bearer_token, get_current_user, get_session
from app.models.entities import User
from app.schemas import (
    EmailBindRequest,
    LoginRequest,
    PasswordUpdateRequest,
    ProfileUpdateRequest,
    RegisterRequest,
    SendEmailCodeRequest,
)


router = APIRouter(prefix="/api/auth", tags=["用户认证"])


@router.post("/register")
async def register(
    payload: RegisterRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """注册普通用户。"""
    data = await request.app.state.container.user_service.register(session, payload)
    return success(data, "注册成功")


@router.post("/email-code")
async def send_email_code(
    payload: SendEmailCodeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """完成极验后发送登录、注册或绑定邮箱验证码。"""
    owner = await get_current_user(request, session) if payload.purpose == "bind" else None
    request_ip = extract_client_ip(request)
    await request.app.state.container.user_service.send_email_code(
        session,
        str(payload.email),
        payload.purpose,
        payload.geetest_challenge,
        owner,
        request_ip,
    )
    return success(message="验证码已发送，请查收邮件")


@router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """登录并返回会话令牌。"""
    data = await request.app.state.container.user_service.login(
        session, payload, extract_client_ip(request) or ""
    )
    return success(data, "登录成功")


@router.get("/me")
async def current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """获取当前用户资料。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.user_service.serialize_user(session, user)
    return success(data)


@router.post("/logout")
async def logout(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """注销当前会话。"""
    token = extract_bearer_token(request)
    await request.app.state.container.user_service.logout(session, token)
    return success(message="已退出登录")


@router.put("/profile")
async def update_profile(
    payload: ProfileUpdateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """修改当前用户昵称。"""
    user: User = await get_current_user(request, session)
    data = await request.app.state.container.user_service.update_profile(
        session, user, payload
    )
    return success(data, "个人资料已保存")


@router.put("/password")
async def update_password(
    payload: PasswordUpdateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """修改当前用户登录密码。"""
    user: User = await get_current_user(request, session)
    await request.app.state.container.user_service.update_password(
        session, user, payload
    )
    return success(message="密码修改成功")


@router.put("/email")
async def bind_email(
    payload: EmailBindRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """绑定或更换当前账号邮箱。"""
    user: User = await get_current_user(request, session)
    data = await request.app.state.container.user_service.bind_email(
        session, user, payload
    )
    return success(data, "邮箱已绑定")
