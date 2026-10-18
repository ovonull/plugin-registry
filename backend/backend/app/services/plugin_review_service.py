"""
插件审核流转与上架控制服务。

功能：
1. 按版本维度维护审核状态机：待审核 → 已通过 / 已驳回，流转全部落在 plugin_versions；
2. 审核通过时按清单声明批准能力，高风险能力必须由管理员显式勾选才生效；
3. 管理员上架 / 下架插件，控制插件是否可被安装，并可切换当前生效版本；
4. 每次审核动作都写入只追加的 plugin_reviews 流水，便于事后追责。

约定：
- 审核只审「能力清单」，不审代码内容，因此能力声明与批准结果必须逐项留痕；
- plugin_reviews 只追加，本模块不提供任何修改与删除入口；
- 信任名单管理在 PluginTrustService，安装启停在 PluginInstallService。
"""

import json
import logging
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.config import Settings
from app.common.permissions import is_admin
from app.common.responses import BusinessError
from app.common.time_utils import beijing_now
from app.models.entities import User
from app.models.plugin import Plugin, PluginVersion
from app.services.plugin_common import load_plugins, load_user_names
from app.services.plugin_gate import resolve_granted_capabilities
from app.services.plugin_review_log_service import (
    append_review_log,
    build_approve_comment,
    list_review_logs,
)
from app.services.plugin_serializers import (
    load_code_list,
    serialize_plugin_state,
    serialize_review_version,
)


logger = logging.getLogger("uvicorn.error")


# 审核参数不完整或不合法
CODE_REVIEW_REQUEST_INVALID = 40120
# 当前审核状态不允许该操作
CODE_REVIEW_STATUS_INVALID = 40121
# 驳回必须填写原因
CODE_REVIEW_COMMENT_REQUIRED = 40122
# 只有管理员可以审核插件
CODE_REVIEW_FORBIDDEN = 40384
# 插件不存在
CODE_PLUGIN_NOT_FOUND = 40480
# 插件版本不存在
CODE_PLUGIN_VERSION_NOT_FOUND = 40482

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
VALID_REVIEW_STATUS = (STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED)

# 上架状态在审核流水中的取值，用于记录上架与下架的可见性变化。
VISIBILITY_PUBLISHED = "published"
VISIBILITY_UNPUBLISHED = "unpublished"


class PluginReviewService:
    """插件审核流转与上架控制。"""

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
            raise BusinessError("只有管理员可以审核与管理插件", CODE_REVIEW_FORBIDDEN)

    @staticmethod
    def _name_matched_ids(text: str):
        """
        按插件显示名称反查插件标识，供筛选条件做名称到标识的映射。

        Args:
            text: 管理员输入的关键字。
        Returns:
            子查询，产出名称命中的插件标识。
        """
        return select(Plugin.plugin_id).where(
            Plugin.is_deleted.is_(False),
            Plugin.name.contains(text, autoescape=True),
        )

    async def list_versions(
        self,
        session: AsyncSession,
        page: int,
        page_size: int,
        plugin_id: str = "",
        review_status: str = "",
        keyword: str = "",
    ) -> dict[str, Any]:
        """
        分页查询待审核与已审核的插件版本，供审核台展示。

        Args:
            session: 数据库会话。
            page: 页码，从 1 开始。
            page_size: 每页条数。
            plugin_id: 按插件名称或标识模糊筛选，只返回每个插件的最新版本。
            review_status: 按审核状态筛选。
            keyword: 插件名称、标识或版本号关键字。
        Returns:
            含 items、page、page_size、total、pages 的分页结果。
        """
        # 审核台按插件收敛成一行：同一插件只保留最新提交的版本，
        # 旧版本仍留在 plugin_versions 里供回滚与流水追溯，只是不再重复占位。
        latest_ids = (
            select(func.max(PluginVersion.id))
            .where(PluginVersion.is_deleted.is_(False))
            .group_by(PluginVersion.plugin_id)
        )
        conditions = [
            PluginVersion.is_deleted.is_(False),
            PluginVersion.id.in_(latest_ids),
        ]
        if plugin_id:
            # 管理员可能记不住插件标识，输入名称或标识片段都要能命中。
            conditions.append(
                or_(
                    PluginVersion.plugin_id.contains(plugin_id, autoescape=True),
                    PluginVersion.plugin_id.in_(self._name_matched_ids(plugin_id)),
                )
            )
        if review_status:
            conditions.append(PluginVersion.review_status == review_status)
        if keyword:
            conditions.append(
                or_(
                    PluginVersion.plugin_id.contains(keyword, autoescape=True),
                    PluginVersion.version.contains(keyword, autoescape=True),
                    PluginVersion.plugin_id.in_(self._name_matched_ids(keyword)),
                )
            )
        total = int(
            await session.scalar(
                select(func.count(PluginVersion.id)).where(*conditions)
            )
            or 0
        )
        rows = (
            await session.scalars(
                select(PluginVersion)
                .where(*conditions)
                .order_by(PluginVersion.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        plugins = await load_plugins(session, {row.plugin_id for row in rows})
        names = await load_user_names(
            session,
            {row.owner_id for row in rows}
            | {row.reviewed_by for row in rows if row.reviewed_by},
        )
        # 列表按插件收敛后，管理员仍需要知道当前生效的是哪一版，这里把版本号一并带出。
        current_ids = {
            plugin.current_version_id
            for plugin in plugins.values()
            if plugin.current_version_id
        }
        current_versions: dict[int, str] = {}
        if current_ids:
            version_rows = (
                await session.execute(
                    select(PluginVersion.id, PluginVersion.version).where(
                        PluginVersion.id.in_(current_ids)
                    )
                )
            ).all()
            current_versions = {row[0]: row[1] for row in version_rows}
        items = [
            serialize_review_version(row, plugins.get(row.plugin_id), names)
            for row in rows
        ]
        for item in items:
            plugin = plugins.get(item["plugin_id"])
            current_id = plugin.current_version_id if plugin is not None else None
            item["current_version"] = current_versions.get(current_id, "") if current_id else ""
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    async def approve(
        self,
        session: AsyncSession,
        reviewer: User,
        plugin_id: str,
        version: str,
        granted_capabilities: list[str] | None = None,
        comment: str = "",
    ) -> dict[str, Any]:
        """
        审核通过一个插件版本，并记录管理员实际批准的能力。

        Args:
            session: 数据库会话。
            reviewer: 执行审核的管理员。
            plugin_id: 插件标识。
            version: 版本号。
            granted_capabilities: 管理员勾选批准的能力，为空表示只批准非高风险能力。
            comment: 审核意见。
        Returns:
            审核后的版本信息与插件状态。
        Raises:
            BusinessError: 版本不存在、已通过审核或勾选了未申请的能力时拒绝。
        """
        self.ensure_admin(reviewer)
        row = await self._load_version(session, plugin_id, version)
        if row.review_status == STATUS_APPROVED:
            raise BusinessError(
                "该版本已通过审核，无需重复操作", CODE_REVIEW_STATUS_INVALID
            )
        declared = load_code_list(row.capabilities_json)
        granted = resolve_granted_capabilities(declared, granted_capabilities)
        previous = row.review_status
        row.review_status = STATUS_APPROVED
        row.reviewed_by = reviewer.id
        row.reviewed_at = beijing_now()
        row.review_comment = str(comment or "").strip()
        row.granted_capabilities_json = json.dumps(granted, ensure_ascii=False)
        row.updated_at = beijing_now()
        plugin = await self._load_plugin(session, plugin_id)
        # 首个通过审核的版本自动成为当前生效版本；已上架插件换版本需调用上架接口。
        if plugin.current_version_id is None:
            plugin.current_version_id = row.id
            plugin.updated_at = beijing_now()
        append_review_log(
            session,
            plugin_id,
            row.version,
            "approve",
            previous,
            STATUS_APPROVED,
            reviewer.id,
            build_approve_comment(granted, declared, comment),
        )
        await session.commit()
        await session.refresh(row)
        logger.info(
            "插件版本审核通过，插件=%s，版本=%s，审核人=%s，批准能力=%s",
            plugin_id,
            row.version,
            reviewer.username,
            "、".join(granted) or "无",
        )
        return {
            "version": serialize_review_version(row, plugin, {reviewer.id: reviewer.username}),
            "plugin": serialize_plugin_state(plugin),
        }

    async def reject(
        self,
        session: AsyncSession,
        reviewer: User,
        plugin_id: str,
        version: str,
        comment: str,
    ) -> dict[str, Any]:
        """
        驳回一个插件版本，必须填写驳回原因。

        Args:
            session: 数据库会话。
            reviewer: 执行审核的管理员。
            plugin_id: 插件标识。
            version: 版本号。
            comment: 驳回原因，必填。
        Returns:
            驳回后的版本信息。
        Raises:
            BusinessError: 版本不存在、已驳回或未填写原因时拒绝。
        """
        self.ensure_admin(reviewer)
        reason = str(comment or "").strip()
        if not reason:
            raise BusinessError(
                "驳回插件必须填写原因，便于作者修改", CODE_REVIEW_COMMENT_REQUIRED
            )
        row = await self._load_version(session, plugin_id, version)
        if row.review_status == STATUS_REJECTED:
            raise BusinessError(
                "该版本已被驳回，无需重复操作", CODE_REVIEW_STATUS_INVALID
            )
        previous = row.review_status
        row.review_status = STATUS_REJECTED
        row.reviewed_by = reviewer.id
        row.reviewed_at = beijing_now()
        row.review_comment = reason
        row.granted_capabilities_json = "[]"
        row.updated_at = beijing_now()
        append_review_log(
            session,
            plugin_id,
            row.version,
            "reject",
            previous,
            STATUS_REJECTED,
            reviewer.id,
            reason,
        )
        await session.commit()
        await session.refresh(row)
        plugin = await self._load_plugin(session, plugin_id)
        logger.info(
            "插件版本已驳回，插件=%s，版本=%s，审核人=%s",
            plugin_id,
            row.version,
            reviewer.username,
        )
        return {
            "version": serialize_review_version(row, plugin, {reviewer.id: reviewer.username}),
            "plugin": serialize_plugin_state(plugin),
        }

    async def publish(
        self,
        session: AsyncSession,
        reviewer: User,
        plugin_id: str,
        version: str = "",
    ) -> dict[str, Any]:
        """
        上架插件，可选把某个已通过审核的版本切换为当前生效版本。

        Args:
            session: 数据库会话。
            reviewer: 执行操作的管理员。
            plugin_id: 插件标识。
            version: 目标版本号，为空时沿用插件当前生效版本。
        Returns:
            上架后的插件状态。
        Raises:
            BusinessError: 插件不存在、目标版本未通过审核或已上架时拒绝。
        """
        self.ensure_admin(reviewer)
        plugin = await self._load_plugin(session, plugin_id)
        target = plugin.current_version_id
        if version:
            row = await self._load_version(session, plugin_id, version)
            if row.review_status != STATUS_APPROVED:
                raise BusinessError(
                    "只能把已通过审核的版本切换为当前生效版本",
                    CODE_REVIEW_STATUS_INVALID,
                )
            target = row.id
        if not target:
            raise BusinessError(
                "插件还没有通过审核的版本，不能上架", CODE_REVIEW_STATUS_INVALID
            )
        current = await session.scalar(
            select(PluginVersion).where(PluginVersion.id == target)
        )
        if current is None or current.review_status != STATUS_APPROVED:
            raise BusinessError(
                "插件当前生效版本未通过审核，不能上架", CODE_REVIEW_STATUS_INVALID
            )
        if plugin.is_published and plugin.current_version_id == target:
            raise BusinessError("该插件已上架，无需重复操作", CODE_REVIEW_STATUS_INVALID)
        previous = (
            VISIBILITY_PUBLISHED if plugin.is_published else VISIBILITY_UNPUBLISHED
        )
        plugin.is_published = True
        plugin.current_version_id = target
        plugin.updated_at = beijing_now()
        append_review_log(
            session,
            plugin_id,
            current.version,
            "publish",
            previous,
            VISIBILITY_PUBLISHED,
            reviewer.id,
            (
                f"切换生效版本为 {current.version}"
                if previous == VISIBILITY_PUBLISHED
                else f"上架插件，当前生效版本 {current.version}"
            ),
        )
        await session.commit()
        logger.info(
            "插件已上架，插件=%s，版本=%s，操作人=%s",
            plugin_id,
            current.version,
            reviewer.username,
        )
        return serialize_plugin_state(plugin)

    async def unpublish(
        self, session: AsyncSession, reviewer: User, plugin_id: str
    ) -> dict[str, Any]:
        """
        下架插件，已安装的实例会因插件未上架而无法继续启用。

        Args:
            session: 数据库会话。
            reviewer: 执行操作的管理员。
            plugin_id: 插件标识。
        Returns:
            下架后的插件状态。
        Raises:
            BusinessError: 插件不存在或本就未上架时拒绝。
        """
        self.ensure_admin(reviewer)
        plugin = await self._load_plugin(session, plugin_id)
        if not plugin.is_published:
            raise BusinessError(
                "该插件尚未上架，无需重复操作", CODE_REVIEW_STATUS_INVALID
            )
        version = (
            await session.scalar(
                select(PluginVersion).where(
                    PluginVersion.id == plugin.current_version_id
                )
            )
            if plugin.current_version_id
            else None
        )
        plugin.is_published = False
        plugin.updated_at = beijing_now()
        append_review_log(
            session,
            plugin_id,
            version.version if version else "",
            "unpublish",
            VISIBILITY_PUBLISHED,
            VISIBILITY_UNPUBLISHED,
            reviewer.id,
            "下架插件",
        )
        await session.commit()
        logger.info("插件已下架，插件=%s，操作人=%s", plugin_id, reviewer.username)
        return serialize_plugin_state(plugin)

    async def list_review_logs(
        self,
        session: AsyncSession,
        page: int,
        page_size: int,
        plugin_id: str = "",
        action: str = "",
    ) -> dict[str, Any]:
        """分页查询只追加的审核流水，实现落在审核流水模块。"""
        return await list_review_logs(session, page, page_size, plugin_id, action)

    @staticmethod
    async def _load_plugin(session: AsyncSession, plugin_id: str) -> Plugin:
        """读取插件主表记录。"""
        plugin = await session.scalar(
            select(Plugin).where(
                Plugin.plugin_id == str(plugin_id or "").strip(),
                Plugin.is_deleted.is_(False),
            )
        )
        if plugin is None:
            raise BusinessError("插件不存在", CODE_PLUGIN_NOT_FOUND)
        return plugin

    @staticmethod
    async def _load_version(
        session: AsyncSession, plugin_id: str, version: str
    ) -> PluginVersion:
        """读取指定插件的版本记录。"""
        row = await session.scalar(
            select(PluginVersion).where(
                PluginVersion.plugin_id == str(plugin_id or "").strip(),
                PluginVersion.version == str(version or "").strip(),
                PluginVersion.is_deleted.is_(False),
            )
        )
        if row is None:
            raise BusinessError("插件版本不存在", CODE_PLUGIN_VERSION_NOT_FOUND)
        return row
