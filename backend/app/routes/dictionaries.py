"""公开数据字典查询接口。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.responses import success
from app.dependencies import get_session
from app.services.dictionary_service import DictionaryService


router = APIRouter(prefix="/api/dictionaries", tags=["数据字典"])


@router.get("")
async def list_dictionaries(
    types: str = Query(default=""),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """按 types 查询前端选择项。"""
    requested = [item.strip() for item in types.split(",") if item.strip()]
    return success(await DictionaryService.list_types(session, requested))
