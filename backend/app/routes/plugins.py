"""插件注册表、能力目录、上传通道与插件安装接口。

说明：本模块提供插件提交、列表与详情查询、能力目录查询，以及插件的安装与启停。
审核流转与信任名单管理放在 plugin_admin 路由，仅管理员可访问。
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.responses import BusinessError, success
from app.dependencies import get_current_user, get_session
from app.schemas_plugins import (
    PluginInstallRequest,
    PluginInstallationUpdateRequest,
    PluginRepoProbeRequest,
    PluginRepoSubmitRequest,
    PluginUpgradeRequest,
)
from app.services.plugin_service import (
    CODE_PLUGIN_REQUEST_INVALID,
    SOURCE_TYPE_REPO,
    SOURCE_TYPE_ZIP,
    PluginService,
)

# 允许的压缩包请求类型，multipart 由接口内部转换为更明确的中文提示。
ZIP_CONTENT_TYPES = frozenset(
    {
        "application/zip",
        "application/x-zip-compressed",
        "application/octet-stream",
        "binary/octet-stream",
    }
)


router = APIRouter(prefix="/api/plugins", tags=["插件"])


def _plugin_service(request: Request) -> PluginService:
    """取出应用级插件服务实例。"""
    return request.app.state.container.plugin_service


def _install_service(request: Request):
    """取出应用级插件安装服务实例。"""
    return request.app.state.container.plugin_install_service


@router.get("")
async def list_plugins(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query("", max_length=100),
    source_type: str = Query("", max_length=20),
    published: bool | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """按当前用户可见范围分页查询插件。"""
    service = _plugin_service(request)
    service.ensure_enabled(request.app.state.container.settings)
    user = await get_current_user(request, session)
    data = await service.list_plugins(
        session,
        user,
        page,
        page_size,
        keyword.strip(),
        source_type.strip(),
        published,
    )
    return success(data)


@router.get("/mine")
async def list_my_plugins(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query("", max_length=100),
    review_status: str = Query("", max_length=20),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """返回当前账号提交过的插件与最新审核状态，供「我的插件」页展示。"""
    service = _plugin_service(request)
    service.ensure_enabled(request.app.state.container.settings)
    user = await get_current_user(request, session)
    data = await service.list_my_plugins(
        session,
        user,
        page,
        page_size,
        keyword.strip(),
        review_status.strip(),
    )
    return success(data)


@router.get("/capabilities")
async def list_capabilities(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """返回插件能力目录，供审核面板按风险等级展示。"""
    service = _plugin_service(request)
    service.ensure_enabled(request.app.state.container.settings)
    await get_current_user(request, session)
    return success(service.list_capability_catalog())


@router.post("/repo/probe")
async def probe_repo_latest(
    payload: PluginRepoProbeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """探测仓库最新版本：返回插件标识、版本号与引用来源，不登记、不落库。"""
    service = _plugin_service(request)
    service.ensure_enabled(request.app.state.container.settings)
    await get_current_user(request, session)
    data = await service.probe_repo_latest(payload.repo_url, payload.source_ref)
    return success(data, "已探测到仓库最新版本")


@router.post("/repo")
async def submit_from_repo(
    payload: PluginRepoSubmitRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """从代码仓库按固定 tag 或 commit 提交一个插件版本。"""
    service = _plugin_service(request)
    service.ensure_enabled(request.app.state.container.settings)
    user = await get_current_user(request, session)
    data = await service.submit_package(
        session,
        user,
        SOURCE_TYPE_REPO,
        repo_url=payload.repo_url,
        source_ref=payload.source_ref,
    )
    return success(data, "插件版本已提交，等待管理员审核")


@router.post("/package")
async def submit_from_package(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """直接上传插件压缩包提交版本，压缩包根目录必须包含 manifest.json。"""
    service = _plugin_service(request)
    settings = request.app.state.container.settings
    service.ensure_enabled(settings)
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in ZIP_CONTENT_TYPES:
        raise BusinessError(
            "请以压缩包原始字节上传（Content-Type 使用 application/zip），"
            "不要使用表单方式提交",
            CODE_PLUGIN_REQUEST_INVALID,
        )
    content = await request.body()
    # 体积校验统一放在来源服务内，避免两处维护同一套上限。
    service.source_service.ensure_archive_size(content)
    user = await get_current_user(request, session)
    data = await service.submit_package(
        session, user, SOURCE_TYPE_ZIP, content=content
    )
    return success(data, "插件版本已提交，等待管理员审核")


@router.get("/installations")
async def list_installations(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    bot_qq: str = Query("", max_length=20),
    plugin_id: str = Query("", max_length=100),
    enabled: bool | None = Query(None),
    mine: bool = Query(False),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """分页查询当前账号可管理的插件安装记录，mine=true 时只返回自己的。"""
    service = _install_service(request)
    PluginService.ensure_enabled(request.app.state.container.settings)
    user = await get_current_user(request, session)
    data = await service.list_installations(
        session,
        user,
        page,
        page_size,
        bot_qq.strip(),
        plugin_id.strip(),
        enabled,
        mine,
    )
    return success(data)


@router.post("/installations")
async def install_plugin(
    payload: PluginInstallRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """为当前账号安装插件并分配机器人，安装后默认停用。"""
    service = _install_service(request)
    PluginService.ensure_enabled(request.app.state.container.settings)
    user = await get_current_user(request, session)
    data = await service.install(session, user, payload.plugin_id, payload.bot_qqs)
    return success(data, "插件已安装，可按需开启")


@router.put("/installations/{install_id}")
async def update_installation(
    install_id: int,
    payload: PluginInstallationUpdateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """修改插件安装的启用状态、插件配置、生效能力或生效机器人。"""
    service = _install_service(request)
    PluginService.ensure_enabled(request.app.state.container.settings)
    user = await get_current_user(request, session)
    data = await service.update_installation(
        session,
        user,
        install_id,
        payload.is_enabled,
        payload.config,
        payload.granted_capabilities,
        payload.bot_qqs,
    )
    return success(data, "插件配置已保存")


@router.post("/installations/{install_id}/upgrade")
async def upgrade_installation(
    install_id: int,
    payload: PluginUpgradeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """把安装记录升级到插件的新版本，配置与机器人分配尽量保留。"""
    service = _install_service(request)
    PluginService.ensure_enabled(request.app.state.container.settings)
    user = await get_current_user(request, session)
    data = await service.upgrade(session, user, install_id, payload.version)
    message = "插件已升级" if not data.get("auto_disabled") else "插件已升级，需补填配置后重新开启"
    return success(data, message)


@router.post("/installations/{install_id}/delete")
async def uninstall_plugin(
    install_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """卸载插件，记录保留但不再参与事件处理。"""
    service = _install_service(request)
    PluginService.ensure_enabled(request.app.state.container.settings)
    user = await get_current_user(request, session)
    await service.uninstall(session, user, install_id)
    return success(None, "插件已卸载")


@router.post("/{plugin_id}/delete")
async def delete_plugin(
    plugin_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """删除插件，管理员可删任意插件，提交者只能删自己上传的插件。"""
    service = _plugin_service(request)
    service.ensure_enabled(request.app.state.container.settings)
    user = await get_current_user(request, session)
    data = await service.delete_plugin(session, user, plugin_id.strip())
    return success(data, "插件已删除")


@router.post("/{plugin_id}/check-update")
async def check_plugin_update(
    plugin_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """检查仓库来源插件的仓库里是否有新版本，只探测、不提交。"""
    service = _plugin_service(request)
    service.ensure_enabled(request.app.state.container.settings)
    user = await get_current_user(request, session)
    data = await service.check_repo_update(session, user, plugin_id.strip())
    message = (
        f"发现新版本 {data['version']}，去提交即可升级"
        if data["has_new_version"]
        else "仓库里暂未有新版本"
    )
    return success(data, message)


@router.get("/{plugin_id}")
async def get_plugin_detail(
    plugin_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """查询插件详情及其全部版本。"""
    service = _plugin_service(request)
    service.ensure_enabled(request.app.state.container.settings)
    user = await get_current_user(request, session)
    data = await service.get_plugin_detail(session, user, plugin_id)
    return success(data)
