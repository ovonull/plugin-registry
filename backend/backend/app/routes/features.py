"""系统功能列表接口。"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.responses import success
from app.dependencies import get_current_user, get_session


router = APIRouter(prefix="/api/features", tags=["功能列表"])


@router.get("")
async def list_features(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """返回当前系统启用的内置功能说明。"""
    await get_current_user(request, session)
    features = await request.app.state.container.feature_service.list_features(session)
    return success(features)
