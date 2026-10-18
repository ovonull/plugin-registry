"""AI 随机主动回复规则分页、配置和软删除接口。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.responses import success
from app.dependencies import get_current_user, get_session
from app.schemas import (
    AiProactiveReplyCreateRequest,
    AiProactiveReplyUpdateRequest,
)


router = APIRouter(prefix="/api/ai_proactive_replies", tags=["AI 主动回复"])


@router.get("")
async def list_rules(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    bot_qq: str = Query("", max_length=20),
    group_id: str = Query("", max_length=30),
    enabled: bool | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """按当前用户权限分页查询 AI 主动回复规则。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.ai_proactive_reply_service.list_rules(
        session,
        user,
        page,
        page_size,
        bot_qq.strip(),
        group_id.strip(),
        enabled,
    )
    return success(data)


@router.post("")
async def create_rule(
    payload: AiProactiveReplyCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """新增机器人默认或指定群 AI 主动回复规则。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.ai_proactive_reply_service.create_rule(
        session, user, payload
    )
    return success(data, "AI 主动回复规则已添加")


@router.put("/{rule_id}")
async def update_rule(
    rule_id: int,
    payload: AiProactiveReplyUpdateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """修改 AI 主动回复规则。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.ai_proactive_reply_service.update_rule(
        session, user, rule_id, payload
    )
    return success(data, "AI 主动回复规则已保存")


@router.post("/{rule_id}/delete")
async def delete_rule(
    rule_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """软删除 AI 主动回复规则。"""
    user = await get_current_user(request, session)
    await request.app.state.container.ai_proactive_reply_service.soft_delete(
        session, user, rule_id
    )
    return success(message="AI 主动回复规则已删除")
