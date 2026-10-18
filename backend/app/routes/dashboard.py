"""管理后台仪表盘统计接口。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.permissions import is_admin
from app.common.responses import success
from app.dependencies import get_current_user, get_session


router = APIRouter(prefix="/api/dashboard", tags=["仪表盘"])


def _resolve_public_service_url(request: Request) -> str:
    """解析业务方应使用的后端公开服务基址。

    Args:
        request: 当前仪表盘请求，用于取得访问协议和主机名。
    Returns:
        配置的公开基址，或按当前主机名和后端监听端口生成的基址。
    """
    settings = request.app.state.container.settings
    if settings.public_service_url:
        return settings.public_service_url
    hostname = request.url.hostname
    if not hostname:
        return ""
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0]
    scheme = forwarded_proto.strip().lower()
    if scheme not in {"http", "https"}:
        scheme = request.url.scheme
    formatted_host = f"[{hostname}]" if ":" in hostname else hostname
    return f"{scheme}://{formatted_host}:{settings.port}"


async def _get_dashboard_summary(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """获取当前权限范围内的规则统计。"""
    user = await get_current_user(request, session)
    data = await request.app.state.container.auto_reply_service.dashboard_summary(
        session, user
    )
    onebot = request.app.state.container.onebot_service
    configured_bots = await request.app.state.container.bot_service.list_bots(
        session, user, set(onebot.reverse_manager.connected_self_ids)
    )
    data["bot_count"] = len(configured_bots)
    data["websocket_connections"] = onebot.reverse_manager.connection_count
    connected_bots = onebot.reverse_manager.connected_self_ids
    data["connected_bot_count"] = len(connected_bots)
    data["connected_bots"] = connected_bots if is_admin(user) else []
    data["event_subscribers"] = onebot.broker.subscriber_count
    data["http_upstream_configured"] = any(
        bool(item.get("http_url")) for item in configured_bots
    )
    data["public_service_url"] = _resolve_public_service_url(request)
    return success(data)


@router.get("/summary")
async def dashboard_summary(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """兼容当前项目原有的 /summary 地址。"""
    return await _get_dashboard_summary(request, session)


@router.get("")
async def dashboard(
    request: Request,
    days: int = Query(default=7, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """兼容 api-system 的 /api/dashboard?days=7 地址。"""
    del days
    return await _get_dashboard_summary(request, session)
