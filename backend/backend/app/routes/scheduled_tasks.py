"""系统定时任务查询和配置接口。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.permissions import is_admin
from app.common.responses import BusinessError, success
from app.dependencies import get_current_user, get_session
from app.schemas import ScheduledTaskUpdateRequest


router = APIRouter(prefix="/api/scheduled_tasks", tags=["定时任务"])


async def _ensure_admin(request: Request, session: AsyncSession):
    """系统级任务只允许管理员调整，避免影响其他用户配置的机器人。"""
    user = await get_current_user(request, session)
    if not is_admin(user):
        raise BusinessError("只有管理员可以管理定时任务", 40370)
    return user


@router.get("")
async def list_tasks(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """分页查询所有系统定时任务。"""
    await _ensure_admin(request, session)
    data = await request.app.state.container.scheduled_task_service.list_tasks(
        session, page, page_size
    )
    return success(data)


@router.put("/{task_id}")
async def update_task(
    task_id: int,
    payload: ScheduledTaskUpdateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """修改定时任务间隔和启停状态。"""
    await _ensure_admin(request, session)
    data = await request.app.state.container.scheduled_task_service.update_task(
        session, task_id, payload
    )
    return success(data, "定时任务已保存")


@router.post("/{task_id}/run")
async def run_task(
    task_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """立即执行指定定时任务并返回最新执行结果。"""
    await _ensure_admin(request, session)
    data = await request.app.state.container.scheduled_task_service.run_task(task_id)
    if data.get("last_status") == "unsupported":
        return success(data, data.get("last_error") or "当前版本不支持该定时任务")
    if data.get("last_status") in {"failed", "partial"}:
        return success(data, data.get("last_error") or "定时任务执行未完全成功")
    return success(data, "定时任务已执行")
