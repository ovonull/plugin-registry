"""登录日志查询接口。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.responses import success
from app.dependencies import get_current_user, get_session


router = APIRouter(prefix="/api/login-logs", tags=["登录日志"])


@router.get("")
async def list_login_logs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query("", max_length=255),
    login_type: str = Query("", max_length=30),
    start_time: str = Query("", max_length=30),
    end_time: str = Query("", max_length=30),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """按当前用户权限分页查询登录日志：普通用户仅本人，管理员可查看全部。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.login_log_service.list_logs(
        session,
        user,
        page,
        page_size,
        keyword.strip(),
        login_type.strip(),
        start_time.strip(),
        end_time.strip(),
    )
    return success(data)