"""违禁词规则分页、新增、修改和软删除接口。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.responses import success
from app.dependencies import get_current_user, get_session
from app.schemas import (
    ForbiddenWordBatchDeleteRequest,
    ForbiddenWordCreateRequest,
    ForbiddenWordUpdateRequest,
)


router = APIRouter(prefix="/api/forbidden_words", tags=["违禁词"])


@router.get("")
async def list_rules(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query("", max_length=100),
    bot_qq: str = Query("", max_length=20),
    group_id: str = Query("", max_length=30),
    enabled: bool | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """按当前用户权限分页查询违禁词。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.forbidden_word_service.list_rules(
        session,
        user,
        page,
        page_size,
        keyword.strip(),
        bot_qq.strip(),
        group_id.strip(),
        enabled,
    )
    return success(data)


@router.post("")
async def create_rules(
    payload: ForbiddenWordCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """按英文逗号保存一条违禁词列表。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.forbidden_word_service.create_rules(
        session, user, payload
    )
    return success(data, "违禁词列表已添加")


@router.post("/batch-delete")
async def delete_rules_batch(
    payload: ForbiddenWordBatchDeleteRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """按勾选 ID 批量软删除违禁词规则。"""
    user = await get_current_user(request, session)
    count = await request.app.state.container.forbidden_word_service.soft_delete_many(
        session, user, payload.ids
    )
    return success({"deleted_count": count}, f"已删除 {count} 条违禁词列表")


@router.put("/{rule_id}")
async def update_rule(
    rule_id: int,
    payload: ForbiddenWordUpdateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """修改一条逗号分隔违禁词列表。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.forbidden_word_service.update_rule(
        session, user, rule_id, payload
    )
    return success(data, "违禁词规则已保存")


@router.post("/{rule_id}/delete")
async def delete_rule(
    rule_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """软删除一条违禁词规则。"""
    user = await get_current_user(request, session)
    await request.app.state.container.forbidden_word_service.soft_delete(
        session, user, rule_id
    )
    return success(message="违禁词规则已删除")
