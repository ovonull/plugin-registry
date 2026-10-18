"""插件库公开索引路由。

对外提供无需鉴权的 registry/index.json，供 NapCatQQ Service
插件市场定时拉取并安装远程插件。
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_session
from app.services.plugin_registry_export_service import RegistryExportService


router = APIRouter(prefix="/api/registry", tags=["插件库公开索引"])


@router.get("/index.json")
async def registry_index(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """输出当前已上架插件的公开索引。"""
    service = RegistryExportService()
    data = await service.build_index(session)
    return JSONResponse(content=data, headers={"Cache-Control": "no-store"})
