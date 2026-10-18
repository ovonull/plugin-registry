"""极验行为验证 3.0 接口。"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.responses import success
from app.dependencies import get_session
from app.schemas import GeetestValidateRequest


router = APIRouter(prefix="/api/geetest", tags=["极验验证码"])


@router.get("/register")
async def geetest_register(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """获取 gt.js 初始化参数。"""
    data = await request.app.state.container.geetest_service.register(session)
    return success(data, "极验初始化成功")


@router.post("/validate")
async def geetest_validate(
    payload: GeetestValidateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """提交 challenge、validate 和 seccode 完成二次校验。"""
    await request.app.state.container.geetest_service.validate(
        session,
        payload.challenge,
        payload.geetest_validate,
        payload.seccode,
    )
    return success(message="验证通过")
