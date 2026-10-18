"""Bot accounts, group allowlists, API catalog and action execution."""

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.responses import BusinessError, success
from app.dependencies import get_current_user, get_session
from app.schemas import (
    BotActionRequest,
    BotAiConfigRequest,
    BotCreateRequest,
    BotGroupRequest,
    BotUpdateRequest,
)
from app.services.onebot_catalog import (
    ACTION_CATALOG,
    ACTION_EXECUTION_MAP,
    GROUP_SCOPED_ACTIONS,
)


router = APIRouter(prefix="/api/bots", tags=["机器人与动作"])


@router.get("/ai/providers")
async def ai_providers(
    request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    """返回 AI 平台字典、默认地址和模型列表能力。"""
    await get_current_user(request, session)
    return success(
        await request.app.state.container.bot_ai_service.provider_options(session)
    )


@router.get("")
async def list_bots(
    request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    user = await get_current_user(request, session)
    connected = set(request.app.state.container.onebot_service.reverse_manager.connected_self_ids)
    return success(
        await request.app.state.container.bot_service.list_bots(session, user, connected)
    )


@router.post("")
async def create_bot(
    payload: BotCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = await get_current_user(request, session)
    return success(
        await request.app.state.container.bot_service.create_bot(session, user, payload),
        "机器人账号已添加",
    )


@router.put("/{bot_qq}")
async def update_bot(
    bot_qq: str,
    payload: BotUpdateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = await get_current_user(request, session)
    return success(
        await request.app.state.container.bot_service.update_bot(
            session, user, bot_qq, payload
        ),
        "机器人配置已保存",
    )


@router.post("/{bot_qq}/delete")
async def delete_bot(
    bot_qq: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = await get_current_user(request, session)
    await request.app.state.container.bot_service.delete_bot(session, user, bot_qq)
    return success(message="机器人账号已删除")


@router.get("/{bot_qq}/groups")
async def list_groups(
    bot_qq: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = await get_current_user(request, session)
    return success(
        await request.app.state.container.bot_service.list_groups(session, user, bot_qq)
    )


@router.post("/{bot_qq}/groups")
async def upsert_group(
    bot_qq: str,
    payload: BotGroupRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = await get_current_user(request, session)
    return success(
        await request.app.state.container.bot_service.upsert_group(
            session, user, bot_qq, payload
        ),
        "群配置已保存",
    )


@router.post("/{bot_qq}/groups/sync")
async def sync_groups(
    bot_qq: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = await get_current_user(request, session)
    bot = await request.app.state.container.bot_service.get_bot(
        session, user, bot_qq
    )
    if not bot.is_enabled:
        raise BusinessError("机器人账号已停用", 40034)
    existing_groups = {
        item["group_id"]: item
        for item in await request.app.state.container.bot_service.list_groups(
            session, user, bot_qq
        )
    }
    result = await request.app.state.container.onebot_service.call_action(
        "get_group_list", {}, self_id=bot_qq
    )
    groups = result.get("data") if isinstance(result, dict) else None
    if not isinstance(groups, list):
        raise BusinessError("NapCat 未返回有效群列表", 50210)
    saved = []
    for item in groups:
        if not isinstance(item, dict) or item.get("group_id") is None:
            continue
        saved.append(
            await request.app.state.container.bot_service.upsert_group(
                session,
                user,
                bot_qq,
                BotGroupRequest(
                    group_id=str(item["group_id"]),
                    group_name=str(item.get("group_name") or ""),
                    is_enabled=existing_groups.get(
                        str(item["group_id"]),
                        {"is_enabled": bot.group_mode != "allowlist"},
                    )["is_enabled"],
                ),
            )
        )
    return success(saved, f"已同步 {len(saved)} 个群")


@router.post("/{bot_qq}/groups/{group_id}/delete")
async def delete_group(
    bot_qq: str,
    group_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = await get_current_user(request, session)
    await request.app.state.container.bot_service.delete_group(
        session, user, bot_qq, group_id
    )
    return success(message="群配置已删除")


@router.get("/{bot_qq}/ai")
async def get_ai_config(
    bot_qq: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """读取指定机器人的 AI 配置，API Key 仅返回给已授权用户。"""
    user = await get_current_user(request, session)
    return success(
        await request.app.state.container.bot_ai_service.get_config(
            session, user, bot_qq
        )
    )


@router.put("/{bot_qq}/ai")
async def save_ai_config(
    bot_qq: str,
    payload: BotAiConfigRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """保存指定机器人的 AI 配置。"""
    user = await get_current_user(request, session)
    return success(
        await request.app.state.container.bot_ai_service.save_config(
            session, user, bot_qq, payload
        ),
        "AI 配置已保存",
    )


@router.post("/{bot_qq}/ai/test")
async def test_ai_config(
    bot_qq: str,
    payload: BotAiConfigRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    model_type: Literal["text", "image", "generation"] = Query("text"),
) -> dict:
    """使用当前表单参数测试指定 AI 模型模块。"""
    user = await get_current_user(request, session)
    result = await request.app.state.container.bot_ai_service.test_config(
        session, user, bot_qq, payload, model_type
    )
    return success(result, "AI 模型测试成功")


@router.post("/{bot_qq}/ai/models")
async def list_ai_models(
    bot_qq: str,
    payload: BotAiConfigRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    model_type: Literal["text", "image", "generation"] = Query("text"),
) -> dict:
    """使用当前表单参数查询指定 AI 模型模块的平台模型。"""
    user = await get_current_user(request, session)
    models = await request.app.state.container.bot_ai_service.list_models(
        session, user, bot_qq, payload, model_type
    )
    return success(models, f"已获取 {len(models)} 个模型")


@router.get("/catalog")
async def action_catalog() -> dict:
    return success(ACTION_CATALOG)


@router.post("/{bot_qq}/actions")
async def execute_action(
    bot_qq: str,
    payload: BotActionRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = await get_current_user(request, session)
    bot = await request.app.state.container.bot_service.get_bot(session, user, bot_qq)
    if not bot.is_enabled:
        raise BusinessError("机器人账号已停用", 40034)
    if payload.bot_qq != bot_qq:
        raise BusinessError("机器人 QQ 不一致", 40033)
    group_id = payload.params.get("group_id")
    if payload.action in GROUP_SCOPED_ACTIONS and group_id is None:
        raise BusinessError("群动作必须选择目标群", 40036)
    if group_id is not None:
        await request.app.state.container.bot_service.ensure_group_allowed(
            session, bot_qq, str(group_id)
        )
    result = await request.app.state.container.onebot_service.call_action(
        ACTION_EXECUTION_MAP.get(payload.action, payload.action),
        payload.params,
        self_id=bot_qq,
    )
    return success(result, "动作执行完成")
