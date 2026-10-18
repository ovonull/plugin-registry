"""数据字典读取服务。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import DataDictionary


class DictionaryService:
    """按类型返回已启用的数据字典选项。"""

    @staticmethod
    async def list_types(session: AsyncSession, types: list[str]) -> dict:
        if not types:
            return {}
        rows = (
            await session.execute(
                select(DataDictionary)
                .where(
                    DataDictionary.dict_type.in_(types),
                    DataDictionary.is_enabled.is_(True),
                    DataDictionary.is_deleted.is_(False),
                )
                .order_by(DataDictionary.dict_type, DataDictionary.sort_order)
            )
        ).scalars().all()
        output = {dict_type: [] for dict_type in types}
        for row in rows:
            output[row.dict_type].append(
                {"value": row.item_code, "label": row.item_name}
            )
        return output
