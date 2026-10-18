"""管理员用户列表和普通用户创建接口。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.permissions import is_admin
from app.common.responses import BusinessError, success
from app.dependencies import get_current_user, get_session
from app.schemas import AdminUserCreateRequest


router = APIRouter(prefix="/api/users", tags=["用户管理"])


async def _ensure_admin(request: Request, session: AsyncSession) -> None:
    """限制用户管理接口仅管理员可访问。"""
    user = await get_current_user(request, session)
    if not is_admin(user):
        raise BusinessError("只有管理员可以管理用户", 40370)


@router.get("")
async def list_users(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """分页查询未删除用户。"""
    await _ensure_admin(request, session)
    data = await request.app.state.container.user_service.list_users(
        session, page, page_size
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
    data = await request.app.state.container.user_service.create_user(session, payload)
    return success(data, "用户创建成功")
