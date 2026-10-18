"""群 AI 语音角色配置接口。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.responses import BusinessError, success
from app.dependencies import get_current_user, get_session
from app.schemas import AiVoiceCharacterCreateRequest, AiVoiceCharacterUpdateRequest
from app.services.ai_voice_character_rule_service import DEFAULT_CHARACTERS


router = APIRouter(prefix="/api/ai_voice_characters", tags=["AI 语音角色"])


@router.get("")
async def list_rules(request: Request, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), bot_qq: str = Query("", max_length=20), group_id: str = Query("", max_length=30), enabled: bool | None = Query(None), session: AsyncSession = Depends(get_session)) -> dict:
    """分页查询 AI 语音角色配置。"""
    user = await get_current_user(request, session)
    return success(await request.app.state.container.ai_voice_character_rule_service.list_rules(session, user, page, page_size, bot_qq.strip(), group_id.strip(), enabled))


@router.post("")
async def create_rule(payload: AiVoiceCharacterCreateRequest, request: Request, session: AsyncSession = Depends(get_session)) -> dict:
    """新增 AI 语音角色配置。"""
    user = await get_current_user(request, session)
    return success(await request.app.state.container.ai_voice_character_rule_service.create_rule(session, user, payload), "AI 语音角色配置已添加")


@router.get("/options")
async def character_options(request: Request, bot_qq: str = Query(..., min_length=5, max_length=20), group_id: str = Query("", max_length=30), session: AsyncSession = Depends(get_session)) -> dict:
    """查询指定群可用角色；未选群时返回内置默认角色。"""
    user = await get_current_user(request, session)
    bot_service = request.app.state.container.bot_service
    await bot_service.get_bot(session, user, bot_qq.strip())
    group_id = group_id.strip()
    if not group_id:
        return success(list(DEFAULT_CHARACTERS), "已获取默认语音角色")
    await bot_service.ensure_group_allowed(session, bot_qq.strip(), group_id)
    response = await request.app.state.container.onebot_service.call_action("get_ai_characters", {"group_id": group_id}, self_id=bot_qq.strip())
    values = []
    data = response.get("data") if isinstance(response, dict) else None
    categories = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    for category in categories:
        if not isinstance(category, dict):
            continue
        for character in category.get("characters", []):
            if isinstance(character, dict) and character.get("character_id"):
                values.append({"character_id": str(character["character_id"]), "character_name": str(character.get("character_name") or character["character_id"]), "preview_url": str(character.get("preview_url") or "")})
    unique = {item["character_id"]: item for item in values}
    if response.get("status") != "ok" or not unique:
        raise BusinessError("NapCat 未返回有效 AI 语音角色", 50260)
    return success(list(unique.values()), "已获取群 AI 语音角色")


@router.put("/{rule_id}")
async def update_rule(rule_id: int, payload: AiVoiceCharacterUpdateRequest, request: Request, session: AsyncSession = Depends(get_session)) -> dict:
    """修改 AI 语音角色配置。"""
    user = await get_current_user(request, session)
    return success(await request.app.state.container.ai_voice_character_rule_service.update_rule(session, user, rule_id, payload), "AI 语音角色配置已保存")


@router.post("/{rule_id}/delete")
async def delete_rule(rule_id: int, request: Request, session: AsyncSession = Depends(get_session)) -> dict:
    """软删除 AI 语音角色配置。"""
    user = await get_current_user(request, session)
    await request.app.state.container.ai_voice_character_rule_service.soft_delete(session, user, rule_id)
    return success(message="AI 语音角色配置已删除")
