"""插件库公开路由。

对外提供无需鉴权的接口，供插件展示首页与 NapCatQQ Service 插件市场使用：

- ``/api/registry/index.json``：插件市场定时同步的完整索引；
- ``/api/registry/plugins``：展示首页的分页列表；
- ``/api/registry/plugins/{plugin_id}``：插件详情；
- ``/api/registry/stats``：首页顶部统计。

说明：这些接口只读，且只包含已上架、当前版本审核通过的插件。
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.responses import success
from app.dependencies import get_session
from app.services.plugin_registry_export_service import (
    SORT_CHOICES,
    SORT_LATEST,
    RegistryExportService,
)


router = APIRouter(prefix="/api/registry", tags=["插件库公开索引"])

# 公开数据允许被浏览器短暂缓存，避免首页反复刷新时压满数据库，
# 同时保证上架变更能在半分钟内对外生效。
PUBLIC_CACHE_HEADERS = {"Cache-Control": "public, max-age=30"}


@router.get("/index.json")
async def registry_index(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """输出当前已上架插件的公开索引。"""
    service = RegistryExportService()
    data = await service.build_index(session)
    return JSONResponse(content=data, headers={"Cache-Control": "no-store"})


@router.get("/plugins")
async def registry_plugins(
    session: AsyncSession = Depends(get_session),
    keyword: str = Query("", max_length=100),
    sort: str = Query(SORT_LATEST, max_length=20),
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=60),
) -> JSONResponse:
    """分页返回公开插件列表，供展示首页使用。"""
    service = RegistryExportService()
    data = await service.list_plugins(
        session,
        keyword=keyword,
        sort=sort if sort in SORT_CHOICES else SORT_LATEST,
        page=page,
        page_size=page_size,
    )
    return JSONResponse(content=success(data), headers=PUBLIC_CACHE_HEADERS)


@router.get("/stats")
async def registry_stats(
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """返回首页顶部统计数字。"""
    service = RegistryExportService()
    data = await service.build_stats(session)
    return JSONResponse(content=success(data), headers=PUBLIC_CACHE_HEADERS)


@router.get("/plugins/{plugin_id}")
async def registry_plugin_detail(
    plugin_id: str,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """返回单个公开插件详情，不存在或未上架时 data 为空。

    不走 HTTP 404：前端统一按业务响应解析，404 会被当成网络异常，
    提示语反而变成「无法连接服务」。
    """
    service = RegistryExportService()
    data = await service.plugin_detail(session, plugin_id)
    return JSONResponse(content=success(data), headers=PUBLIC_CACHE_HEADERS)
