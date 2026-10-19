"""
插件审核流水与审核备注。

功能：
1. 以只追加方式写入审核流水，任何审核、上架、下架动作都必须留痕；
2. 拼装审核通过备注，明确记录「批准了哪些能力、未批准哪些能力」；
3. 提供审核流水分页查询，供审核台按插件与动作回溯。

约定：本模块不提供任何修改与删除入口，写入后不可变更。
"""

import logging
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.time_utils import format_beijing
from app.models.plugin import Plugin, PluginReview
from app.services.plugin_common import load_plugins, load_user_names


logger = logging.getLogger("uvicorn.error")


def append_review_log(
    session: AsyncSession,
    plugin_id: str,
    version: str,
    action: str,
    from_status: str,
    to_status: str,
    reviewer_id: int | None,
    comment: str,
) -> None:
    """
    写入一条只追加的审核流水。

    Args:
        session: 数据库会话。
        plugin_id: 插件标识。
        version: 版本号，上架与下架时填当前生效版本。
        action: 动作，取值 submit / approve / reject / publish / unpublish。
        from_status: 变更前状态。
        to_status: 变更后状态。
        reviewer_id: 操作人用户 ID；自动同步等系统动作传 None。
        comment: 备注。
    """
    session.add(
        PluginReview(
            plugin_id=plugin_id,
            version=version,
            action=action,
            from_status=from_status,
            to_status=to_status,
            reviewer_id=reviewer_id,
            comment=comment,
        )
    )


def build_approve_comment(
    granted: list[str], declared: list[str], comment: str
) -> str:
    """
    拼装审核通过流水的备注。

    Args:
        granted: 管理员实际批准的能力。
        declared: 插件申请的能力。
        comment: 管理员填写的审核意见。
    Returns:
        形如「审核意见；批准能力：…；未批准：…」的备注文本。
    """
    text = str(comment or "").strip()
    skipped = [code for code in declared if code not in granted]
    detail = "批准能力：" + ("、".join(granted) or "无")
    if skipped:
        detail += "；未批准：" + "、".join(skipped)
    return f"{text}；{detail}" if text else detail


async def list_review_logs(
    session: AsyncSession,
    page: int,
    page_size: int,
    plugin_id: str = "",
    action: str = "",
) -> dict[str, Any]:
    """
    分页查询只追加的审核流水。

    Args:
        session: 数据库会话。
        page: 页码，从 1 开始。
        page_size: 每页条数。
        plugin_id: 按插件名称或标识模糊筛选。
        action: 按动作筛选。
    Returns:
        含 items、page、page_size、total、pages 的分页结果。
    """
    conditions = []
    if plugin_id:
        # 流水里只有插件标识，这里把名称也映射进来，管理员输名称同样能筛。
        matched_ids = select(Plugin.plugin_id).where(
            Plugin.is_deleted.is_(False),
            Plugin.name.contains(plugin_id, autoescape=True),
        )
        conditions.append(
            or_(
                PluginReview.plugin_id.contains(plugin_id, autoescape=True),
                PluginReview.plugin_id.in_(matched_ids),
            )
        )
    if action:
        conditions.append(PluginReview.action == action)
    total = int(
        await session.scalar(select(func.count(PluginReview.id)).where(*conditions)) or 0
    )
    rows = (
        await session.scalars(
            select(PluginReview)
            .where(*conditions)
            .order_by(PluginReview.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    names = await load_user_names(
        session, {row.reviewer_id for row in rows if row.reviewer_id}
    )
    # 流水只存标识，列表上要给管理员看名称，这里一次性把插件名补上。
    plugins = await load_plugins(session, {row.plugin_id for row in rows})
    return {
        "items": [
            {
                "id": row.id,
                "plugin_id": row.plugin_id,
                "plugin_name": (
                    plugins[row.plugin_id].name if row.plugin_id in plugins else ""
                ),
                "version": row.version,
                "action": row.action,
                "from_status": row.from_status,
                "to_status": row.to_status,
                "reviewer_id": row.reviewer_id,
                "reviewer_username": names.get(row.reviewer_id, "未知用户"),
                "comment": row.comment,
                "created_at": format_beijing(row.created_at),
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": max(1, (total + page_size - 1) // page_size),
    }
