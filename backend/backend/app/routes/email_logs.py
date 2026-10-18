"""邮件日志查询接口。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.responses import success
from app.dependencies import get_current_user, get_session


router = APIRouter(prefix="/api/email-logs", tags=["邮件日志"])


@router.get("")
async def list_email_logs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query("", max_length=255),
    action: str = Query("", max_length=30),
    result: str = Query("", max_length=10),
    start_time: str = Query("", max_length=30),
    end_time: str = Query("", max_length=30),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """仅管理员分页查询邮件日志，支持邮箱关键字、触发操作、发送结果和时间区间筛选。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.email_log_service.list_logs(
        session,
        user,
        page,
        page_size,
        keyword.strip(),
        action.strip(),
        result.strip(),
        start_time.strip(),
        end_time.strip(),
    )
    return success(data)
