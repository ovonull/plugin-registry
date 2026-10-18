"""进群欢迎词分页、配置和软删除接口。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.responses import success
from app.dependencies import get_current_user, get_session
from app.schemas import WelcomeMessageCreateRequest, WelcomeMessageUpdateRequest


router = APIRouter(prefix="/api/welcome_messages", tags=["进群欢迎词"])


@router.get("")
async def list_welcome_messages(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    bot_qq: str = Query("", max_length=20),
    group_id: str = Query("", max_length=30),
    enabled: bool | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """按当前用户权限分页查询欢迎词。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.welcome_message_service.list_rules(
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
async def create_welcome_message(
    payload: WelcomeMessageCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """新增机器人默认或指定群欢迎词。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.welcome_message_service.create_rule(
        session, user, payload
    )
    return success(data, "进群欢迎词已添加")


@router.put("/{rule_id}")
async def update_welcome_message(
    rule_id: int,
    payload: WelcomeMessageUpdateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """修改有权访问的欢迎词。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.welcome_message_service.update_rule(
        session, user, rule_id, payload
    )
    return success(data, "进群欢迎词已保存")


@router.post("/{rule_id}/delete")
async def delete_welcome_message(
    rule_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """软删除一条欢迎词。"""
    user = await get_current_user(request, session)
    await request.app.state.container.welcome_message_service.soft_delete(
        session, user, rule_id
    )
    return success(message="进群欢迎词已删除")
