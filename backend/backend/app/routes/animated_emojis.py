"""动画表情分类记录查询接口。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.responses import success
from app.dependencies import get_current_user, get_session
from app.schemas import AnimatedEmojiBatchDeleteRequest
from app.schemas_animated_emojis import AnimatedEmojiBatchCategoryRequest


router = APIRouter(prefix="/api/animated_emojis", tags=["动画表情分类"])


@router.get("")
async def list_animated_emojis(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    bot_qq: str = Query("", max_length=20),
    category: str = Query("", max_length=100),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """按分类、接收时间倒序分页查询动画表情记录。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.animated_emoji_service.list_records(
        session,
        user,
        page,
        page_size,
        bot_qq.strip(),
        category.strip(),
    )
    return success(data)


@router.post("/batch-delete")
async def delete_animated_emojis_batch(
    payload: AnimatedEmojiBatchDeleteRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """按勾选 ID 批量软删除动画表情记录。"""
    user = await get_current_user(request, session)
    count = await request.app.state.container.animated_emoji_service.soft_delete_many(
        session, user, payload.ids
    )
    return success({"deleted_count": count}, f"已删除 {count} 条动画表情记录")


@router.post("/batch-category")
async def update_animated_emoji_categories(
    payload: AnimatedEmojiBatchCategoryRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """按勾选 ID 批量修改动画表情分类。"""
    user = await get_current_user(request, session)
    count = await request.app.state.container.animated_emoji_category_service.update_many(
        session, user, payload.ids, payload.category
    )
    return success(
        {"updated_count": count, "category": payload.category},
        f"已将 {count} 条动画表情修改为“{payload.category}”",
    )


@router.get("/{record_id}/preview")
async def preview_animated_emoji(
    record_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """按记录 ID 懒加载单张动画表情的 Base64 图片数据。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.animated_emoji_service.get_preview(
        session, user, record_id
    )
    return success(data)


@router.post("/{record_id}/delete")
async def delete_animated_emoji(
    record_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """软删除单条动画表情记录。"""
    user = await get_current_user(request, session)
    await request.app.state.container.animated_emoji_service.soft_delete(
        session, user, record_id
    )
    return success(message="动画表情记录已删除")
