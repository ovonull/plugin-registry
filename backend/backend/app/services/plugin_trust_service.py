"""
代码插件信任名单服务。

功能：
1. 维护可上传代码插件的用户白名单，管理员默认具备上传权限，无需加入名单；
2. 授权与撤销都保留历史记录，撤销只把 is_active 置为 False，不做物理删除；
3. 恢复授权时沿用原记录，避免同一用户出现多条历史记录。

约定：名单只决定「谁能提交插件代码」，不决定插件能做什么，
插件能力仍由 PluginReviewService 在审核时逐项批准。
"""

import logging
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.config import Settings
from app.common.permissions import is_admin
from app.common.responses import BusinessError
from app.common.time_utils import beijing_now, format_beijing
from app.models.entities import User
from app.models.plugin import PluginTrustedAuthor
from app.services.plugin_common import load_user_names


logger = logging.getLogger("uvicorn.error")


# 该用户已在信任名单内
CODE_TRUST_AUTHOR_EXISTS = 40123
# 只有管理员可以管理信任名单
CODE_TRUST_FORBIDDEN = 40385
# 信任名单记录不存在
CODE_TRUST_RECORD_NOT_FOUND = 40483
# 用户不存在
CODE_USER_NOT_FOUND = 40401


class PluginTrustService:
    """代码插件信任名单管理。"""

    def __init__(self, settings: Settings) -> None:
        """记录应用配置。"""
        self.settings = settings

    @staticmethod
    def ensure_admin(user: User) -> None:
        """
        校验当前用户是管理员。

        Args:
            user: 当前登录用户。
        Raises:
            BusinessError: 非管理员时拒绝访问。
        """
        if not is_admin(user):
            raise BusinessError("只有管理员可以管理信任名单", CODE_TRUST_FORBIDDEN)

    async def list_trusted_authors(
        self,
        session: AsyncSession,
        page: int,
        page_size: int,
        keyword: str = "",
        active: bool | None = None,
    ) -> dict[str, Any]:
        """
        分页查询代码插件信任名单，含授权记录与撤销状态。

        Args:
            session: 数据库会话。
            page: 页码，从 1 开始。
            page_size: 每页条数。
            keyword: 用户名或邮箱关键字。
            active: 按授权状态筛选。
        Returns:
            含 items、page、page_size、total、pages 的分页结果。
        """
        conditions = [PluginTrustedAuthor.is_deleted.is_(False)]
        if active is not None:
            conditions.append(PluginTrustedAuthor.is_active.is_(active))
        if keyword:
            matched = (
                await session.execute(
                    select(User.id).where(
                        User.is_deleted.is_(False),
                        or_(
                            User.username.contains(keyword, autoescape=True),
                            User.email.contains(keyword, autoescape=True),
                        ),
                    )
                )
            ).all()
            user_ids = [row.id for row in matched]
            if not user_ids:
                return {
                    "items": [],
                    "page": page,
                    "page_size": page_size,
                    "total": 0,
                    "pages": 1,
                }
            conditions.append(PluginTrustedAuthor.user_id.in_(user_ids))
        total = int(
            await session.scalar(
                select(func.count(PluginTrustedAuthor.id)).where(*conditions)
            )
            or 0
        )
        rows = (
            await session.scalars(
                select(PluginTrustedAuthor)
                .where(*conditions)
                .order_by(PluginTrustedAuthor.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        names = await load_user_names(
            session,
            {row.user_id for row in rows}
            | {row.granted_by for row in rows if row.granted_by},
        )
        return {
            "items": [self.serialize(row, names) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    async def grant_trust(
        self,
        session: AsyncSession,
        reviewer: User,
        user_id: int,
        reason: str = "",
    ) -> dict[str, Any]:
        """
        把某个用户加入代码插件信任名单，已撤销的授权会被恢复。

        Args:
            session: 数据库会话。
            reviewer: 执行授权的管理员。
            user_id: 被授权的用户 ID。
            reason: 授权原因。
        Returns:
            信任名单记录。
        Raises:
            BusinessError: 用户不存在、已是管理员或已在名单内时拒绝。
        """
        self.ensure_admin(reviewer)
        target = await session.scalar(
            select(User).where(User.id == user_id, User.is_deleted.is_(False))
        )
        if target is None:
            raise BusinessError("用户不存在", CODE_USER_NOT_FOUND)
        if is_admin(target):
            raise BusinessError(
                "管理员默认具备上传权限，无需加入信任名单", CODE_TRUST_AUTHOR_EXISTS
            )
        existing = await session.scalar(
            select(PluginTrustedAuthor).where(PluginTrustedAuthor.user_id == user_id)
        )
        if existing is not None and existing.is_active and not existing.is_deleted:
            raise BusinessError("该用户已在信任名单内", CODE_TRUST_AUTHOR_EXISTS)
        if existing is None:
            existing = PluginTrustedAuthor(
                user_id=user_id,
                granted_by=reviewer.id,
                reason=str(reason or "").strip(),
            )
            session.add(existing)
        else:
            existing.is_deleted = False
            existing.is_active = True
            existing.granted_by = reviewer.id
            existing.reason = str(reason or "").strip()
            existing.updated_at = beijing_now()
        await session.commit()
        await session.refresh(existing)
        logger.info(
            "已加入代码插件信任名单，用户=%s，操作人=%s",
            target.username,
            reviewer.username,
        )
        return self.serialize(
            existing,
            {existing.user_id: target.username, reviewer.id: reviewer.username},
        )

    async def revoke_trust(
        self, session: AsyncSession, reviewer: User, trust_id: int
    ) -> None:
        """
        撤销信任名单授权，保留历史记录，不做物理删除。

        Args:
            session: 数据库会话。
            reviewer: 执行操作的管理员。
            trust_id: 信任名单记录 ID。
        Raises:
            BusinessError: 记录不存在或已被撤销时拒绝。
        """
        self.ensure_admin(reviewer)
        row = await session.scalar(
            select(PluginTrustedAuthor).where(
                PluginTrustedAuthor.id == trust_id,
                PluginTrustedAuthor.is_deleted.is_(False),
            )
        )
        if row is None or not row.is_active:
            raise BusinessError("信任名单记录不存在或授权已被撤销", CODE_TRUST_RECORD_NOT_FOUND)
        row.is_active = False
        row.updated_at = beijing_now()
        await session.commit()
        logger.info(
            "已撤销代码插件信任授权，用户编号=%s，操作人=%s",
            row.user_id,
            reviewer.username,
        )

    @staticmethod
    def serialize(
        row: PluginTrustedAuthor, user_names: dict[int, str]
    ) -> dict[str, Any]:
        """把信任名单记录转换为统一接口响应。"""
        return {
            "id": row.id,
            "user_id": row.user_id,
            "username": user_names.get(row.user_id, "未知用户"),
            "granted_by": row.granted_by,
            "granted_by_username": user_names.get(row.granted_by, "未知用户"),
            "reason": row.reason,
            "is_active": row.is_active,
            "created_at": format_beijing(row.created_at),
            "updated_at": format_beijing(row.updated_at),
        }
