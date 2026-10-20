"""插件库分发接口。

对外提供插件索引、列表、详情与插件包下载，供展示首页与下游插件市场使用：

- ``/api/registry/index.json``：插件市场定时同步的完整索引；
- ``/api/registry/plugins``：展示首页的分页列表；
- ``/api/registry/plugins/{plugin_id}``：插件详情；
- ``/api/registry/stats``：首页顶部统计；
- ``/api/registry/packages/{plugin_id}/{version}.zip``：插件包下载。

鉴权：全部接口都要求通过 ``require_registry_access``，接受「后台配置的接入令牌」
或「已登录账号会话」；数据本身只包含已上架、当前版本审核通过的插件。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.responses import success
from app.dependencies import get_session, require_registry_access
from app.services.plugin_registry_export_service import (
    SORT_ALL,
    SORT_CHOICES,
    RegistryExportService,
)


router = APIRouter(prefix="/api/registry", tags=["插件库分发"])

# 接口带鉴权，响应只允许在请求方私有缓存里短暂保存，不能被共享代理缓存。
PRIVATE_CACHE_HEADERS = {"Cache-Control": "private, max-age=30"}


@router.get("/index.json")
async def registry_index(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _access: str = Depends(require_registry_access),
) -> JSONResponse:
    """输出当前已上架插件的索引，供下游插件市场同步。"""
    service = RegistryExportService()
    data = await service.build_index(session, request.app.state.container.settings)
    return JSONResponse(content=data, headers={"Cache-Control": "private, no-store"})


@router.get("/packages/{plugin_id}/{version}.zip")
async def registry_package(
    plugin_id: str,
    version: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _access: str = Depends(require_registry_access),
) -> Response:
    """下载某个插件当前生效版本的压缩包，供下游插件市场安装。

    只分发已上架且审核通过的当前版本；包内容与提交时校验过的完全一致，
    下游可用索引里的 checksum 校验后再落盘。
    """
    service = RegistryExportService()
    content = await service.package_archive(
        session, plugin_id, version, request.app.state.container.settings
    )
    if content is None:
        raise HTTPException(status_code=404, detail="插件包不存在或已下架")
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{plugin_id}-{version}.zip"',
            # 同一版本号内容不会变，允许请求方私有缓存；上架变更会换版本号。
            "Cache-Control": "private, max-age=600",
        },
    )


@router.get("/plugins")
async def registry_plugins(
    session: AsyncSession = Depends(get_session),
    keyword: str = Query("", max_length=100),
    sort: str = Query(SORT_ALL, max_length=20),
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=60),
    _access: str = Depends(require_registry_access),
) -> JSONResponse:
    """分页返回插件列表，供展示首页使用。"""
    service = RegistryExportService()
    data = await service.list_plugins(
        session,
        keyword=keyword,
        sort=sort if sort in SORT_CHOICES else SORT_ALL,
        page=page,
        page_size=page_size,
    )
    return JSONResponse(content=success(data), headers=PRIVATE_CACHE_HEADERS)


@router.get("/stats")
async def registry_stats(
    session: AsyncSession = Depends(get_session),
    _access: str = Depends(require_registry_access),
) -> JSONResponse:
    """返回首页顶部统计数字。"""
    service = RegistryExportService()
    data = await service.build_stats(session)
    return JSONResponse(content=success(data), headers=PRIVATE_CACHE_HEADERS)


@router.get("/plugins/{plugin_id}")
async def registry_plugin_detail(
    plugin_id: str,
    session: AsyncSession = Depends(get_session),
    _access: str = Depends(require_registry_access),
) -> JSONResponse:
    """返回单个插件详情，不存在或未上架时 data 为空。

    不走 HTTP 404：前端统一按业务响应解析，404 会被当成网络异常，
    提示语反而变成「无法连接服务」。
    """
    service = RegistryExportService()
    data = await service.plugin_detail(session, plugin_id)
    return JSONResponse(content=success(data), headers=PRIVATE_CACHE_HEADERS)
