"""AI 触发关键词分页、新增、修改和软删除接口。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.responses import success
from app.dependencies import get_current_user, get_session
from app.schemas import (
    AiTriggerKeywordBatchDeleteRequest,
    AiTriggerKeywordCreateRequest,
    AiTriggerKeywordUpdateRequest,
)


router = APIRouter(prefix="/api/ai_trigger_keywords", tags=["AI 触发关键词"])


@router.get("")
async def list_rules(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query("", max_length=100),
    bot_qq: str = Query("", max_length=20),
    enabled: bool | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """按当前用户权限分页查询 AI 触发关键词。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.ai_trigger_keyword_service.list_rules(
        session, user, page, page_size, keyword.strip(), bot_qq.strip(), enabled
    )
    return success(data)


@router.post("")
async def create_rule(
    payload: AiTriggerKeywordCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """为一个机器人保存一条逗号分隔 AI 触发关键词列表。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.ai_trigger_keyword_service.create_rule(
        session, user, payload
    )
    return success(data, "AI 触发关键词已保存")


@router.post("/batch-delete")
async def delete_rules_batch(
    payload: AiTriggerKeywordBatchDeleteRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """按勾选 ID 批量软删除 AI 触发关键词规则。"""
    user = await get_current_user(request, session)
    count = await request.app.state.container.ai_trigger_keyword_service.soft_delete_many(
        session, user, payload.ids
    )
    return success({"deleted_count": count}, f"已删除 {count} 条 AI 触发关键词")


@router.put("/{rule_id}")
async def update_rule(
    rule_id: int,
    payload: AiTriggerKeywordUpdateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """修改一条 AI 触发关键词列表。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.ai_trigger_keyword_service.update_rule(
        session, user, rule_id, payload
    )
    return success(data, "AI 触发关键词已保存")


@router.post("/{rule_id}/delete")
async def delete_rule(
    rule_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """软删除一条 AI 触发关键词规则。"""
    user = await get_current_user(request, session)
    await request.app.state.container.ai_trigger_keyword_service.soft_delete(
        session, user, rule_id
    )
    return success(message="AI 触发关键词已删除")
