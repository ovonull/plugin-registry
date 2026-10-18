"""机器人好友列表同步与查询接口。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.responses import success
from app.dependencies import get_current_user, get_session


router = APIRouter(prefix="/api/friends", tags=["好友列表"])


@router.get("")
async def list_friends(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    bot_qq: str = Query("", max_length=20),
    keyword: str = Query("", max_length=255),
    category_name: str = Query("", max_length=100),
    sex: str = Query("", max_length=20),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """按当前用户权限分页查询已同步的好友。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.friend_service.list_friends(
        session,
        user,
        page,
        page_size,
        bot_qq.strip(),
        keyword.strip(),
        category_name.strip(),
        sex.strip(),
    )
    return success(data)


@router.post("/{bot_qq}/sync")
async def sync_friends(
    bot_qq: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """从 NapCat 同步指定机器人的全部好友。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.friend_service.sync_friends(
        session, user, bot_qq.strip()
    )
    return success(
        data,
        f"已同步 {data['total_count']} 个好友，新增 {data['created_count']} 个，"
        f"更新 {data['updated_count']} 个，失效 {data['invalid_count']} 个",
    )


@router.post("/{friend_id}/block")
async def block_friend(
    friend_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """屏蔽指定好友，屏蔽后其私聊和群聊消息在接收时直接跳过处理。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.friend_service.set_block(
        session, user, friend_id, True
    )
    return success(data, "已屏蔽该好友，其消息将不再处理")


@router.post("/{friend_id}/unblock")
async def unblock_friend(
    friend_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """取消屏蔽指定好友，恢复其消息的正常处理。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.friend_service.set_block(
        session, user, friend_id, False
    )
    return success(data, "已取消屏蔽该好友")


@router.post("/{bot_qq}/{friend_qq}/messages/sync")
async def sync_friend_messages(
    bot_qq: str,
    friend_qq: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """从 NapCat 同步与指定好友的私聊历史记录，重复消息自动去重。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.friend_service.sync_friend_messages(
        session, user, bot_qq.strip(), friend_qq.strip()
    )
    return success(
        data,
        f"已读取 {data['fetched_count']} 条聊天记录，新增 {data['created_count']} 条，"
        f"重复跳过 {data['existing_count']} 条",
    )
