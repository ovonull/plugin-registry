"""自动回复规则分页、新增、修改和软删除接口。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.responses import success
from app.dependencies import get_current_user, get_session
from app.schemas import AutoReplyCreateRequest, AutoReplyUpdateRequest


router = APIRouter(prefix="/api/auto_replies", tags=["自动回复"])


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
    """按当前用户权限查询规则分页。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.auto_reply_service.list_rules(
        session, user, page, page_size, keyword.strip(), bot_qq.strip(), enabled
    )
    return success(data)


@router.post("")
async def create_rule(
    payload: AutoReplyCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """新增自动回复规则。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.auto_reply_service.create_rule(
        session, user, payload
    )
    return success(data, "自动回复规则已添加")


@router.put("/{rule_id}")
async def update_rule(
    rule_id: int,
    payload: AutoReplyUpdateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """修改有权访问的自动回复规则。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.auto_reply_service.update_rule(
        session, user, rule_id, payload
    )
    return success(data, "自动回复规则已保存")


@router.post("/{rule_id}/delete")
async def delete_rule(
    rule_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """软删除有权访问的自动回复规则。"""
    user = await get_current_user(request, session)
    await request.app.state.container.auto_reply_service.soft_delete_rule(
        session, user, rule_id
    )
    return success(message="自动回复规则已删除")

