"""系统设置接口，每个 Tab 使用独立保存接口。"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.permissions import is_admin
from app.common.responses import BusinessError, success
from app.dependencies import get_current_user, get_session
from app.schemas import (
    AppearanceSettingsRequest,
    AnimatedEmojiSettingsRequest,
    EmailSettingsRequest,
    IntroductionSettingsRequest,
)
from app.services.setting_service import SettingService


router = APIRouter(prefix="/api/settings", tags=["系统设置"])


async def _require_admin(request: Request, session: AsyncSession) -> None:
    user = await get_current_user(request, session)
    if not is_admin(user):
        raise BusinessError("仅管理员可以管理系统设置", 40310)


@router.get("/public")
async def public_settings(session: AsyncSession = Depends(get_session)) -> dict:
    """公开返回系统介绍和默认外观。"""
    return success(await SettingService(session).get_public())


@router.get("/{group}")
async def get_group(
    group: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """管理员读取指定设置 Tab。"""
    await _require_admin(request, session)
    return success(await SettingService(session).get_group(group))


@router.put("/email/save")
async def save_email(
    payload: EmailSettingsRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _require_admin(request, session)
    data = await SettingService(session).save_group("email", payload.model_dump())
    return success(data, "发件人设置已保存")


@router.put("/appearance/save")
async def save_appearance(
    payload: AppearanceSettingsRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _require_admin(request, session)
    data = await SettingService(session).save_group("appearance", payload.model_dump())
    return success(data, "界面设置已保存")


@router.put("/introduction/save")
async def save_introduction(
    payload: IntroductionSettingsRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _require_admin(request, session)
    data = await SettingService(session).save_group("introduction", payload.model_dump())
    return success(data, "系统介绍已保存")


@router.put("/animated-emoji/save")
async def save_animated_emoji(
    payload: AnimatedEmojiSettingsRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """保存动画表情分类提示词。"""
    await _require_admin(request, session)
    data = await SettingService(session).save_group(
        "animated_emoji", payload.model_dump()
    )
    return success(data, "动画表情分类提示词已保存")
