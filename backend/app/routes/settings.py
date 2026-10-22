"""系统设置接口，每个 Tab 使用独立保存接口。"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.permissions import is_admin
from app.common.responses import BusinessError, success
from app.dependencies import get_current_user, get_session
from app.schemas import (
    AppearanceSettingsRequest,
    AnimatedEmojiSettingsRequest,
    EmailSettingsRequest,
    GithubSettingsRequest,
    IntroductionSettingsRequest,
    PluginSyncSettingsRequest,
    RegistrySettingsRequest,
    RegistryTokenCreateRequest,
)
from app.services.registry_token_service import RegistryTokenService, token_item
from app.services.setting_service import SettingService


router = APIRouter(prefix="/api/settings", tags=["系统设置"])


async def _require_admin(request: Request, session: AsyncSession) -> None:
    user = await get_current_user(request, session)
    if not is_admin(user):
        raise BusinessError("仅管理员可以管理系统设置", 40310)


@router.get("/public")
async def public_settings(session: AsyncSession = Depends(get_session)) -> dict:
    """公开返回系统介绍和默认外观。"""
    return success(await SettingService(session).get_public())


@router.get("/{group}")
async def get_group(
    group: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """管理员读取指定设置 Tab。"""
    await _require_admin(request, session)
    return success(await SettingService(session).get_group(group))


@router.put("/email/save")
async def save_email(
    payload: EmailSettingsRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _require_admin(request, session)
    data = await SettingService(session).save_group("email", payload.model_dump())
    return success(data, "发件人设置已保存")


@router.put("/appearance/save")
async def save_appearance(
    payload: AppearanceSettingsRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _require_admin(request, session)
    data = await SettingService(session).save_group("appearance", payload.model_dump())
    return success(data, "界面设置已保存")


@router.put("/introduction/save")
async def save_introduction(
    payload: IntroductionSettingsRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _require_admin(request, session)
    data = await SettingService(session).save_group("introduction", payload.model_dump())
    return success(data, "系统介绍已保存")


@router.put("/github/save")
async def save_github(
    payload: GithubSettingsRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """保存 GitHub 仓库接口令牌，并同步更新运行时配置。"""
    await _require_admin(request, session)
    data = await SettingService(session).save_group("github", payload.model_dump())
    token = str(payload.plugin_repo_token or "").strip()
    # 前端把掩码原样回传表示「不修改」，此时不能把内存里的令牌清空。
    if token and token != "******":
        object.__setattr__(
            request.app.state.container.settings, "plugin_repo_token", token
        )
    return success(data, "GitHub 接口设置已保存")


@router.put("/registry/save")
async def save_registry(
    payload: RegistrySettingsRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """保存插件库接入令牌，并立即作用于分发接口的鉴权。"""
    await _require_admin(request, session)
    data = await SettingService(session).save_group("registry", payload.model_dump())
    token = str(payload.token or "").strip()
    if token != "******":
        object.__setattr__(
            request.app.state.container.settings, "registry_token", token
        )
    return success(data, "接入令牌已保存")


@router.put("/plugin-sync/save")
async def save_plugin_sync(
    payload: PluginSyncSettingsRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """保存仓库插件自动同步设置，并立即作用于运行时。"""
    await _require_admin(request, session)
    data = await SettingService(session).save_group(
        "plugin_sync", payload.model_dump()
    )
    settings = request.app.state.container.settings
    # Settings 是冻结 dataclass，只能通过 object.__setattr__ 注入运行时配置；
    # 同步服务每轮都会重新读取这三项，因此保存后无需重启即可生效。
    object.__setattr__(settings, "plugin_repo_sync_enabled", bool(payload.enabled))
    object.__setattr__(
        settings, "plugin_repo_sync_interval", int(payload.interval_seconds)
    )
    object.__setattr__(settings, "plugin_repo_sync_batch", int(payload.batch))
    return success(data, "插件自动同步设置已保存")


@router.put("/animated-emoji/save")
async def save_animated_emoji(
    payload: AnimatedEmojiSettingsRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """保存动画表情分类提示词。"""
    await _require_admin(request, session)
    data = await SettingService(session).save_group(
        "animated_emoji", payload.model_dump()
    )
    return success(data, "动画表情分类提示词已保存")


@router.get("/registry/tokens")
async def list_registry_tokens(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """列出系统级接入令牌（只含前缀，不含明文）。

    这里只管 ``owner_id`` 为空的令牌，即管理员发放给下游服务的「公共令牌」；
    普通用户在个人设置里创建的令牌归属自己，只在他们自己的个人中心出现。
    """
    await _require_admin(request, session)
    rows = await RegistryTokenService().list_tokens(session, None)
    return success({"items": [token_item(row) for row in rows]})


@router.post("/registry/tokens")
async def create_registry_token(
    payload: RegistryTokenCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """新建一枚系统级接入令牌，明文只在这次响应里返回。"""
    await _require_admin(request, session)
    row, plain = await RegistryTokenService().create(session, payload.name, None)
    return success(
        {"token": plain, "item": token_item(row)},
        "令牌已创建，请立即复制",
    )


@router.delete("/registry/tokens/{token_id}")
async def revoke_registry_token(
    token_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """吊销一枚接入令牌，立即失效。

    管理员不受归属限制，个人令牌出现滥用时也能在这里直接吊销。
    """
    await _require_admin(request, session)
    await RegistryTokenService().revoke(session, token_id, any_owner=True)
    return success({"id": token_id}, "令牌已吊销")
