"""插件审核台、上架控制与信任名单接口，仅管理员可访问。

说明：
1. 审核只审「能力清单」，不读插件代码，因此列表必须同时返回申请与已批准的能力；
2. 每次审核、上架、下架与授权动作都会写入只追加的审核流水；
3. 权限判断统一走 common/permissions，路由层不自行判断角色。
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.permissions import is_admin
from app.common.responses import BusinessError, success
from app.dependencies import get_current_user, get_session
from app.schemas_plugins import (
    PluginPublishRequest,
    PluginReviewApproveRequest,
    PluginReviewRejectRequest,
    PluginTrustGrantRequest,
)
from app.services.plugin_action_log_service import PluginActionLogService
from app.services.plugin_review_service import (
    CODE_REVIEW_FORBIDDEN,
    CODE_REVIEW_REQUEST_INVALID,
    VALID_REVIEW_STATUS,
)
from app.services.plugin_service import PluginService


router = APIRouter(prefix="/api/plugin_admin", tags=["插件审核"])


async def _ensure_admin(request: Request, session: AsyncSession):
    """插件审核与信任名单只允许管理员操作。"""
    user = await get_current_user(request, session)
    if not is_admin(user):
        raise BusinessError("只有管理员可以审核与管理插件", CODE_REVIEW_FORBIDDEN)
    return user


def _review_service(request: Request):
    """取出应用级插件审核服务实例。"""
    return request.app.state.container.plugin_review_service


def _trust_service(request: Request):
    """取出应用级代码插件信任名单服务实例。"""
    return request.app.state.container.plugin_trust_service


def _ensure_plugin_enabled(request: Request) -> None:
    """校验插件系统总开关，关闭时所有插件接口统一给出中文提示。"""
    PluginService.ensure_enabled(request.app.state.container.settings)


@router.get("/versions")
async def list_versions(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    plugin_id: str = Query("", max_length=100),
    review_status: str = Query("", max_length=20),
    keyword: str = Query("", max_length=100),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """分页查询待审核与已审核的插件版本。"""
    service = _review_service(request)
    _ensure_plugin_enabled(request)
    await _ensure_admin(request, session)
    status = review_status.strip()
    if status and status not in VALID_REVIEW_STATUS:
        raise BusinessError("审核状态取值不正确", CODE_REVIEW_REQUEST_INVALID)
    data = await service.list_versions(
        session, page, page_size, plugin_id.strip(), status, keyword.strip()
    )
    return success(data)


@router.post("/versions/approve")
async def approve_version(
    payload: PluginReviewApproveRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """审核通过插件版本，高风险能力需在本次请求中显式勾选。"""
    service = _review_service(request)
    _ensure_plugin_enabled(request)
    reviewer = await _ensure_admin(request, session)
    data = await service.approve(
        session,
        reviewer,
        payload.plugin_id,
        payload.version,
        payload.granted_capabilities,
        payload.comment,
    )
    return success(data, "插件版本已通过审核")


@router.post("/versions/reject")
async def reject_version(
    payload: PluginReviewRejectRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """驳回插件版本，必须填写驳回原因。"""
    service = _review_service(request)
    _ensure_plugin_enabled(request)
    reviewer = await _ensure_admin(request, session)
    data = await service.reject(
        session, reviewer, payload.plugin_id, payload.version, payload.comment
    )
    return success(data, "插件版本已驳回")


@router.post("/publish")
async def publish_plugin(
    payload: PluginPublishRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """上架插件，可同时把指定的已通过版本切换为当前生效版本。"""
    service = _review_service(request)
    _ensure_plugin_enabled(request)
    reviewer = await _ensure_admin(request, session)
    data = await service.publish(
        session, reviewer, payload.plugin_id, payload.version
    )
    return success(data, "插件已上架")


@router.post("/unpublish")
async def unpublish_plugin(
    payload: PluginPublishRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """下架插件，已安装实例将无法继续启用。"""
    service = _review_service(request)
    _ensure_plugin_enabled(request)
    reviewer = await _ensure_admin(request, session)
    data = await service.unpublish(session, reviewer, payload.plugin_id)
    return success(data, "插件已下架")


@router.get("/review-logs")
async def list_review_logs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    plugin_id: str = Query("", max_length=100),
    action: str = Query("", max_length=30),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """分页查询只追加的插件审核流水。"""
    service = _review_service(request)
    _ensure_plugin_enabled(request)
    await _ensure_admin(request, session)
    data = await service.list_review_logs(
        session, page, page_size, plugin_id.strip(), action.strip()
    )
    return success(data)


@router.get("/trusted-authors")
async def list_trusted_authors(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query("", max_length=100),
    active: bool | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """分页查询代码插件信任名单。"""
    service = _trust_service(request)
    _ensure_plugin_enabled(request)
    await _ensure_admin(request, session)
    data = await service.list_trusted_authors(
        session, page, page_size, keyword.strip(), active
    )
    return success(data)


@router.post("/trusted-authors")
async def grant_trusted_author(
    payload: PluginTrustGrantRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """把用户加入代码插件信任名单，已撤销的授权会被恢复。"""
    service = _trust_service(request)
    _ensure_plugin_enabled(request)
    reviewer = await _ensure_admin(request, session)
    data = await service.grant_trust(
        session, reviewer, payload.user_id, payload.reason
    )
    return success(data, "已加入代码插件信任名单")


@router.get("/action-logs")
async def list_action_logs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    plugin_id: str = Query("", max_length=100),
    bot_qq: str = Query("", max_length=20),
    result_code: str = Query("", max_length=20),
    keyword: str = Query("", max_length=50),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """分页查询插件动作审计，记录只追加、不可修改。"""
    _ensure_plugin_enabled(request)
    user = await _ensure_admin(request, session)
    data = await PluginActionLogService.list_logs(
        session,
        user,
        page,
        page_size,
        plugin_id.strip(),
        bot_qq.strip(),
        result_code.strip(),
        keyword.strip(),
    )
    return success(data)


@router.post("/trusted-authors/{trust_id}/revoke")
async def revoke_trusted_author(
    trust_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """撤销信任授权，保留历史记录。"""
    service = _trust_service(request)
    _ensure_plugin_enabled(request)
    reviewer = await _ensure_admin(request, session)
    await service.revoke_trust(session, reviewer, trust_id)
    return success(None, "已撤销该用户的代码插件上传权限")
