"""聊天记录和 AI 主动回复记录查询接口。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.responses import success
from app.dependencies import get_current_user, get_session
from app.schemas import ChatMessageBatchDeleteRequest


router = APIRouter(prefix="/api/chat_records", tags=["聊天记录"])


@router.get("/messages")
async def list_chat_messages(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    bot_qq: str = Query("", max_length=20),
    group_id: str = Query("", max_length=30),
    keyword: str = Query("", max_length=255),
    post_type: str = Query("", max_length=30),
    ai_triggered: bool | None = Query(None),
    user_qq: str = Query("", max_length=30),
    private_only: bool = Query(False),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """按当前用户权限分页查询聊天记录。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.chat_record_service.list_chat_messages(
        session,
        user,
        page,
        page_size,
        bot_qq.strip(),
        group_id.strip(),
        keyword.strip(),
        post_type.strip(),
        ai_triggered,
        user_qq.strip(),
        private_only,
    )
    return success(data)


@router.get("/messages/export")
async def export_chat_messages(
    request: Request,
    bot_qq: str = Query("", max_length=20),
    group_id: str = Query("", max_length=30),
    keyword: str = Query("", max_length=255),
    post_type: str = Query("", max_length=30),
    ai_triggered: bool | None = Query(None),
    user_qq: str = Query("", max_length=30),
    private_only: bool = Query(False),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """按当前筛选条件导出聊天记录 CSV，内容以 Base64 返回给前端下载。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.chat_record_service.export_chat_messages(
        session,
        user,
        bot_qq.strip(),
        group_id.strip(),
        keyword.strip(),
        post_type.strip(),
        ai_triggered,
        user_qq.strip(),
        private_only,
    )
    return success(data, f"已导出 {data['total']} 条聊天记录")


@router.post("/messages/batch-delete")
async def delete_chat_messages_batch(
    payload: ChatMessageBatchDeleteRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """按勾选 ID 批量软删除聊天记录。"""
    user = await get_current_user(request, session)
    count = await request.app.state.container.chat_record_service.soft_delete_messages(
        session, user, payload.ids
    )
    return success({"deleted_count": count}, f"已删除 {count} 条聊天记录")


@router.get("/ai_replies")
async def list_ai_replies(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    bot_qq: str = Query("", max_length=20),
    group_id: str = Query("", max_length=30),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """按当前用户权限分页查询 AI 主动回复处理记录。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.chat_record_service.list_ai_replies(
        session,
        user,
        page,
        page_size,
        bot_qq.strip(),
        group_id.strip(),
    )
    return success(data)
