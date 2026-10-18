"""动画表情分类人工维护服务。

功能：
1. 按勾选 ID 批量修改动画表情分类；
2. 校验当前用户的数据范围并同步分类状态。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.permissions import owner_scope
from app.common.responses import BusinessError
from app.common.time_utils import beijing_now
from app.models.entities import AnimatedEmoji, User


UNCATEGORIZED = "未分类"


class AnimatedEmojiCategoryService:
    """管理动画表情的人工分类。"""

    @staticmethod
    async def update_many(
        session: AsyncSession,
        user: User,
        record_ids: list[int],
        category: str,
    ) -> int:
        """批量更新当前用户有权访问的动画表情分类。

        Args:
            session: 当前数据库会话。
            user: 当前登录用户。
            record_ids: 待修改的动画表情记录 ID。
            category: 目标分类；未分类表示重新进入自动分类队列。
        Returns:
            成功更新的记录数量。
        """
        ids = sorted({int(item) for item in record_ids})
        if not ids:
            raise BusinessError("请至少勾选一条动画表情记录", 40060)
        normalized_category = category.strip()
        if not normalized_category:
            raise BusinessError("请填写动画表情分类", 40061)
        if len(normalized_category) > 100:
            raise BusinessError("动画表情分类不能超过 100 个字符", 40062)

        query = select(AnimatedEmoji).where(
            AnimatedEmoji.id.in_(ids),
            AnimatedEmoji.is_deleted.is_(False),
        )
        owner_id = owner_scope(user)
        if owner_id is not None:
            query = query.where(AnimatedEmoji.owner_id == owner_id)
        records = (await session.scalars(query)).all()
        if len(records) != len(ids):
            raise BusinessError("部分动画表情记录不存在或无权访问", 40460)

        now = beijing_now()
        for record in records:
            record.category = normalized_category
            record.classification_error = ""
            record.classified_at = None if normalized_category == UNCATEGORIZED else now
            record.updated_at = now
        await session.commit()
        return len(records)
