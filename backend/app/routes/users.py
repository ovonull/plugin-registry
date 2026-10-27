"""管理员用户管理接口：列表、查询、创建、编辑、重置密码与软删除。

所有接口仅管理员可访问，普通用户访问统一返回权限错误。
"""

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.permissions import is_admin
from app.common.responses import BusinessError, success
from app.dependencies import get_current_user, get_session
from app.models.entities import User
from app.schemas import (
    AdminPasswordResetRequest,
    AdminUserCreateRequest,
    AdminUserUpdateRequest,
)


router = APIRouter(prefix="/api/users", tags=["用户管理"])


async def _ensure_admin(request: Request, session: AsyncSession) -> User:
    """
    校验当前登录用户为管理员。

    Args:
        request: 当前请求。
        session: 数据库会话。
    Returns:
        当前登录的管理员用户。
    Raises:
        BusinessError: 当前用户不是管理员。
    """
    user = await get_current_user(request, session)
    if not is_admin(user):
        raise BusinessError("只有管理员可以管理用户", 40370)
    return user


@router.get("")
async def list_users(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query("", max_length=100),
    role_code: str = Query("", max_length=30),
    is_active: bool | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """分页查询未删除用户，支持按关键字、角色和状态筛选。"""
    await _ensure_admin(request, session)
    data = await request.app.state.container.user_admin_service.list_users(
        session,
        page,
        page_size,
        keyword.strip(),
        role_code.strip(),
        is_active,
    )
    return success(data)


@router.post("")
async def create_user(
    payload: AdminUserCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """管理员直接创建一个普通用户。"""
    await _ensure_admin(request, session)
    data = await request.app.state.container.user_admin_service.create_user(
        session, payload
    )
    return success(data, "用户创建成功")


@router.put("/{user_id}")
async def update_user(
    payload: AdminUserUpdateRequest,
    request: Request,
    user_id: int = Path(ge=1),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """修改指定用户的昵称、邮箱、角色和启用状态。"""
    operator = await _ensure_admin(request, session)
    data = await request.app.state.container.user_admin_service.update_user(
        session, operator, user_id, payload
    )
    return success(data, "用户信息已更新")


@router.post("/{user_id}/reset-password")
async def reset_user_password(
    payload: AdminPasswordResetRequest,
    request: Request,
    user_id: int = Path(ge=1),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """管理员重置指定用户密码，该用户的所有登录会话会被撤销。"""
    operator = await _ensure_admin(request, session)
    await request.app.state.container.user_admin_service.reset_password(
        session, operator, user_id, payload
    )
    return success(None, "密码已重置，该用户需重新登录")


@router.delete("/{user_id}")
async def delete_user(
    request: Request,
    user_id: int = Path(ge=1),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """软删除指定用户，历史数据保留。"""
    operator = await _ensure_admin(request, session)
    await request.app.state.container.user_admin_service.delete_user(
        session, operator, user_id
    )
    return success(None, "用户已删除")
