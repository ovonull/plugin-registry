"""
插件注册表服务。

功能：
1. 幂等补齐插件系统用到的数据字典项；
2. 接收上传的插件包或仓库地址，归一化后登记插件与版本记录（初始为待审核）；
3. 按权限分页查询插件列表与详情，普通用户只能看到已上架插件和自己提交的插件；
4. 所有注册用户都可以提交插件；管理员与信任名单内的作者提交后免审核直接上架。

说明：审核状态流转、信任名单管理与插件安装启用不在本模块，属于后续阶段。
"""

import json
import logging
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.config import Settings
from app.common.permissions import is_admin
from app.common.responses import BusinessError
from app.common.time_utils import beijing_now
from app.models.entities import DataDictionary, User
from app.models.plugin import (
    Plugin,
    PluginInstallation,
    PluginReview,
    PluginTrustedAuthor,
    PluginVersion,
)
from app.services.plugin_binding_service import soft_delete_bindings
from app.services.plugin_capability import capability_catalog
from app.services.plugin_defaults import PLUGIN_DICTIONARY_DEFAULTS
from app.services.plugin_manifest import manifest_summary
from app.services.plugin_review_log_service import append_review_log
from app.services.plugin_serializers import serialize_plugin, serialize_version
from app.services.plugin_source_service import PluginSourceService, RepoLatest


logger = logging.getLogger(__name__)


# 插件功能已被关闭
CODE_PLUGIN_DISABLED = 40380
# 无权操作他人插件
CODE_PLUGIN_FORBIDDEN = 40382
# 插件不存在
CODE_PLUGIN_NOT_FOUND = 40480
# 版本已存在
CODE_PLUGIN_VERSION_EXISTS = 40097
# 插件标识已被占用
CODE_PLUGIN_ID_TAKEN = 40098
# 提交参数不完整或不合法
CODE_PLUGIN_REQUEST_INVALID = 40099

SOURCE_TYPE_REPO = "repo"
SOURCE_TYPE_ZIP = "zip"
VALID_SOURCE_TYPES = (SOURCE_TYPE_REPO, SOURCE_TYPE_ZIP)
REVIEW_STATUS_PENDING = "pending"
REVIEW_STATUS_APPROVED = "approved"
REVIEW_STATUS_REJECTED = "rejected"

# 审核流水里的上下架状态，与 plugin_review_service 保持同一套取值。
VISIBILITY_PUBLISHED = "published"
VISIBILITY_UNPUBLISHED = "unpublished"


class PluginService:
    """插件与版本记录的注册、查询与序列化。"""

    def __init__(self, settings: Settings) -> None:
        """记录配置并构造来源归一化服务。"""
        self.settings = settings
        self.source_service = PluginSourceService(settings)

    @staticmethod
    def ensure_enabled(settings: Settings) -> None:
        """
        校验插件系统总开关。

        Args:
            settings: 应用配置。
        Raises:
            BusinessError: 总开关未打开时拒绝访问，避免未配置环境被误用。
        """
        if not settings.plugin_enabled:
            raise BusinessError(
                "插件功能已被关闭，请将环境变量 PLUGIN_ENABLED 改为 true 后重启服务",
                CODE_PLUGIN_DISABLED,
            )

    async def initialize_defaults(self, session: AsyncSession) -> None:
        """幂等补齐插件系统数据字典，已存在则跳过，被软删除则恢复。"""
        for dict_type, code, name, order in PLUGIN_DICTIONARY_DEFAULTS:
            existing = await session.scalar(
                select(DataDictionary).where(
                    DataDictionary.dict_type == dict_type,
                    DataDictionary.item_code == code,
                )
            )
            if existing is None:
                session.add(
                    DataDictionary(
                        dict_type=dict_type,
                        item_code=code,
                        item_name=name,
                        sort_order=order,
                    )
                )
            elif existing.is_deleted:
                existing.is_deleted = False
                existing.item_name = name
                existing.sort_order = order
        await session.commit()

    async def _is_trusted_author(
        self, session: AsyncSession, user: User
    ) -> bool:
        """判断当前用户是否属于「免审核」作者。

        所有注册用户都可以提交插件；差别只在审核：管理员与信任名单内的作者
        提交的版本免审核直接上架，其余用户提交的版本进入待审核队列。

        Args:
            session: 数据库会话。
            user: 当前登录用户。
        Returns:
            True 表示提交后免审核。
        """
        if is_admin(user):
            return True
        trusted = await session.scalar(
            select(PluginTrustedAuthor).where(
                PluginTrustedAuthor.user_id == user.id,
                PluginTrustedAuthor.is_active.is_(True),
                PluginTrustedAuthor.is_deleted.is_(False),
            )
        )
        return trusted is not None

    async def submit_package(
        self,
        session: AsyncSession,
        user: User,
        source_type: str,
        content: bytes | None = None,
        repo_url: str = "",
        source_ref: str = "",
    ) -> dict[str, Any]:
        """
        登记一个插件版本，提交后状态为待审核。

        Args:
            session: 数据库会话。
            user: 当前登录用户。
            source_type: repo 或 zip。
            content: 上传压缩包的原始字节，source_type 为 zip 时必填。
            repo_url: 仓库地址，source_type 为 repo 时必填。
            source_ref: 仓库版本引用，只允许 tag 或 commit。
        Returns:
            新登记的插件与版本信息。
        Raises:
            BusinessError: 来源参数、清单校验或登记任一环节不通过。
        """
        if source_type not in VALID_SOURCE_TYPES:
            raise BusinessError("插件来源只能选择代码仓库或上传压缩包", CODE_PLUGIN_REQUEST_INVALID)

        if source_type == SOURCE_TYPE_REPO:
            repo_url = str(repo_url or "").strip()
            # 版本引用留空即自动探测最新 release / tag / commit，作者不用每次手填。
            if str(source_ref or "").strip():
                reference = self.source_service.validate_source_ref(source_ref)
            else:
                latest = await self.source_service.resolve_latest_ref(repo_url)
                reference = latest.ref
            archive = await self.source_service.download_repo_archive(repo_url, reference)
        else:
            if not content:
                raise BusinessError("请先选择要上传的插件压缩包", CODE_PLUGIN_REQUEST_INVALID)
            archive = content
            repo_url = ""
            reference = ""

        # 先只读清单做查重，避免重复上传把已归档的原始包冲掉：
        # save_archive 是直接覆盖写，一旦写了新内容而版本记录被拒，
        # 库里 checksum 还是旧的、磁盘上却已经是新包，下游安装必然校验失败。
        peeked = self.source_service.peek_manifest(archive)
        peeked_id = str(peeked.get("id") or "")
        peeked_version = str(peeked.get("version") or "")
        if peeked_id and peeked_version:
            duplicate = await session.scalar(
                select(PluginVersion).where(
                    PluginVersion.plugin_id == peeked_id,
                    PluginVersion.version == peeked_version,
                )
            )
            # 被驳回的版本允许修完问题后重新提交同一个版本号。
            if duplicate is not None and duplicate.review_status != REVIEW_STATUS_REJECTED:
                raise BusinessError(
                    f"插件 {peeked_id} 的版本 {peeked_version} 已提交过，"
                    "请修改清单中的版本号后重新上传",
                    CODE_PLUGIN_VERSION_EXISTS,
                )

        package = self.source_service.prepare_package(archive)
        manifest = package.manifest
        # 校验通过后再存档原始压缩包：下游插件市场下载这份字节，
        # sha256 与入库 checksum 一致，可以做到下载后直接校验。
        self.source_service.save_archive(
            archive, package.plugin_id, package.version
        )

        existing_version = await session.scalar(
            select(PluginVersion).where(
                PluginVersion.plugin_id == package.plugin_id,
                PluginVersion.version == package.version,
            )
        )
        # 被驳回的版本允许复用原记录重新走审核，保证一个版本只有一条记录。
        reuse_rejected = (
            existing_version is not None
            and existing_version.review_status == REVIEW_STATUS_REJECTED
        )
        if existing_version is not None and not reuse_rejected:
            hint = "请修改清单中的版本号后重新上传"
            if source_type == SOURCE_TYPE_REPO and not str(source_ref or "").strip():
                hint = (
                    "自动探测拿到的已是最新版本，"
                    "请先在仓库里打新的 tag，或者更新清单里的版本号"
                )
            raise BusinessError(
                f"插件 {package.plugin_id} 的版本 {package.version} 已提交过，{hint}",
                CODE_PLUGIN_VERSION_EXISTS,
            )

        plugin = await self._load_or_create_plugin(
            session, user, manifest, source_type, repo_url
        )
        # 管理员与信任名单作者提交视为自审通过：直接批准清单申请的全部能力并上架；
        # 其余注册用户提交的版本进入待审核队列，由管理员核对能力声明。
        auto_review = await self._is_trusted_author(session, user)
        if is_admin(user):
            auto_comment = "管理员提交，免审直接上架"
        elif auto_review:
            auto_comment = "信任作者提交，免审直接上架"
        else:
            auto_comment = ""
        version_values = {
            "manifest_json": json.dumps(manifest, ensure_ascii=False, sort_keys=True),
            "capabilities_json": json.dumps(manifest["capabilities"], ensure_ascii=False),
            "granted_capabilities_json": (
                json.dumps(manifest["capabilities"], ensure_ascii=False)
                if auto_review
                else "[]"
            ),
            "entry": str(manifest["entry"]),
            "package_path": package.package_path,
            "checksum": package.checksum,
            "source_ref": reference,
            "review_status": REVIEW_STATUS_APPROVED if auto_review else REVIEW_STATUS_PENDING,
            "reviewed_by": user.id if auto_review else None,
            "reviewed_at": beijing_now() if auto_review else None,
            "review_comment": auto_comment,
            "min_host_version": str(manifest["min_host_version"]),
            "max_host_version": str(manifest["max_host_version"]),
        }
        if reuse_rejected:
            # 复用被驳回的记录：包路径、清单与审核状态全部换成这次提交的内容。
            version_row = existing_version
            for field, value in version_values.items():
                setattr(version_row, field, value)
        else:
            version_row = PluginVersion(
                plugin_id=package.plugin_id,
                version=package.version,
                owner_id=user.id,
                **version_values,
            )
            session.add(version_row)
        await session.flush()
        session.add(
            PluginReview(
                plugin_id=package.plugin_id,
                version=package.version,
                action="submit",
                from_status=REVIEW_STATUS_REJECTED if reuse_rejected else "",
                to_status=REVIEW_STATUS_PENDING,
                reviewer_id=user.id,
                comment=(
                    "被驳回后重新提交同一版本，重新进入审核"
                    if reuse_rejected
                    else "提交插件版本，等待管理员审核"
                ),
            )
        )
        if auto_review:
            plugin.current_version_id = version_row.id
            plugin.is_published = True
            plugin.updated_at = beijing_now()
            session.add(
                PluginReview(
                    plugin_id=package.plugin_id,
                    version=package.version,
                    action="approve",
                    from_status=REVIEW_STATUS_PENDING,
                    to_status=REVIEW_STATUS_APPROVED,
                    reviewer_id=user.id,
                    comment=auto_comment,
                )
            )
        await session.commit()
        await session.refresh(plugin)
        await session.refresh(version_row)
        logger.info(
            "插件版本已%s，插件=%s，版本=%s，提交人=%s，包文件数=%s",
            "免审上架" if auto_review else "提交",
            package.plugin_id,
            package.version,
            user.username,
            package.file_count,
        )
        return {
            "plugin": serialize_plugin(plugin, version_row, user.username),
            "version": serialize_version(version_row),
        }

    async def probe_repo_latest(
        self, repo_url: str, source_ref: str = ""
    ) -> dict[str, Any]:
        """
        探测仓库最新版本，返回清单里的插件标识、版本号与引用来源。

        只读元数据 + 下载一次压缩包读清单，不写数据库、不落盘，
        供上传表单在提交前告诉作者「这次会提交哪个版本」。

        Args:
            repo_url: GitHub 仓库地址。
            source_ref: 指定 tag 或 commit；留空表示自动探测最新版本。
        Returns:
            探测结果，含 ref、kind、tag、published_at、plugin_id、version。
        Raises:
            BusinessError: 地址非法、探测或清单校验失败。
        """
        repo_url = str(repo_url or "").strip()
        if str(source_ref or "").strip():
            reference = self.source_service.validate_source_ref(source_ref)
            latest = RepoLatest(
                repo_url=repo_url,
                ref=reference,
                kind="ref",
                tag=reference,
                published_at="",
            )
        else:
            latest = await self.source_service.resolve_latest_ref(repo_url)
        archive = await self.source_service.download_repo_archive(
            latest.repo_url, latest.ref
        )
        manifest = self.source_service.peek_manifest(archive)
        return {
            "repo_url": repo_url,
            "ref": latest.ref,
            "kind": latest.kind,
            "tag": latest.tag,
            "published_at": latest.published_at,
            "plugin_id": str(manifest["id"]),
            "version": str(manifest["version"]),
            "name": str(manifest["name"]),
        }

    async def check_repo_update(
        self, session: AsyncSession, user: User, plugin_id: str
    ) -> dict[str, Any]:
        """
        检查已登记插件的仓库里是否有比已登记版本更新的可安装版本。

        用于「已装插件」页的检查更新：只探测、不提交，
        作者在仓库打好新 tag 后，这里就能看出「有新版本」并给出引导。

        Args:
            session: 数据库会话。
            user: 当前登录用户。
            plugin_id: 插件标识。
        Returns:
            探测结果与对比结论，含 has_new_version 与本地已有版本号。
        Raises:
            BusinessError: 插件不存在、无权查看、不是仓库来源或探测失败。
        """
        plugin = await self._get_visible_plugin(session, user, plugin_id)
        if plugin.source_type != SOURCE_TYPE_REPO or not plugin.repo_url:
            raise BusinessError(
                "该插件是压缩包来源，没有关联的代码仓库，无法自动检查更新",
                CODE_PLUGIN_REQUEST_INVALID,
            )
        probe = await self.probe_repo_latest(plugin.repo_url)
        known_versions = (
            await session.scalars(
                select(PluginVersion.version).where(
                    PluginVersion.plugin_id == plugin_id,
                    PluginVersion.is_deleted.is_(False),
                )
            )
        ).all()
        current_version = ""
        if plugin.current_version_id:
            current = await session.scalar(
                select(PluginVersion).where(PluginVersion.id == plugin.current_version_id)
            )
            current_version = current.version if current is not None else ""
        has_new = str(probe["version"]) not in set(known_versions)
        return {
            **probe,
            "has_new_version": has_new,
            "current_version": current_version,
        }

    async def list_plugins(
        self,
        session: AsyncSession,
        user: User,
        page: int,
        page_size: int,
        keyword: str = "",
        source_type: str = "",
        published: bool | None = None,
    ) -> dict[str, Any]:
        """
        分页查询当前用户可见的插件。

        Args:
            session: 数据库会话。
            user: 当前登录用户。
            page: 页码，从 1 开始。
            page_size: 每页条数。
            keyword: 插件标识或名称关键字。
            source_type: 来源类型筛选。
            published: 上架状态筛选。
        Returns:
            含 items、page、page_size、total、pages 的分页结果。
        """
        conditions = [Plugin.is_deleted.is_(False)]
        if keyword:
            conditions.append(
                or_(
                    Plugin.plugin_id.contains(keyword, autoescape=True),
                    Plugin.name.contains(keyword, autoescape=True),
                )
            )
        if source_type:
            conditions.append(Plugin.source_type == source_type)
        if published is not None:
            conditions.append(Plugin.is_published.is_(published))

        base_query = self._scope_visible(select(Plugin).where(*conditions), user)
        count_query = self._scope_visible(
            select(func.count(Plugin.id)).where(*conditions), user
        )
        total = int(await session.scalar(count_query) or 0)
        rows = (
            await session.scalars(
                base_query.order_by(Plugin.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        current_versions = await self._current_versions(session, rows)
        owner_names = await self._owner_names(session, {row.owner_id for row in rows})
        return {
            "items": [
                serialize_plugin(
                    row,
                    current_versions.get(row.current_version_id),
                    owner_names.get(row.owner_id),
                )
                for row in rows
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    async def list_my_plugins(
        self,
        session: AsyncSession,
        user: User,
        page: int,
        page_size: int,
        keyword: str = "",
        review_status: str = "",
    ) -> dict[str, Any]:
        """
        分页查询当前账号提交过的插件及其最新提交版本。

        与「已安装插件」不同，这里回答的是「我传了哪些插件、审到哪一步了」，
        因此按 owner_id 过滤，并额外带上最新版本、版本总数与待审核数量。

        Args:
            session: 数据库会话。
            user: 当前登录用户。
            page: 页码，从 1 开始。
            page_size: 每页条数。
            keyword: 插件标识或名称关键字。
            review_status: 按最新提交版本的审核状态过滤。
        Returns:
            含 items、page、page_size、total、pages 的分页结果。
        """
        conditions = [Plugin.is_deleted.is_(False), Plugin.owner_id == user.id]
        if keyword:
            conditions.append(
                or_(
                    Plugin.plugin_id.contains(keyword, autoescape=True),
                    Plugin.name.contains(keyword, autoescape=True),
                )
            )
        rows = list(
            (
                await session.scalars(
                    select(Plugin).where(*conditions).order_by(Plugin.id.desc())
                )
            ).all()
        )

        plugin_ids = [row.plugin_id for row in rows]
        versions: list[PluginVersion] = []
        if plugin_ids:
            versions = list(
                (
                    await session.scalars(
                        select(PluginVersion)
                        .where(
                            PluginVersion.plugin_id.in_(plugin_ids),
                            PluginVersion.is_deleted.is_(False),
                        )
                        .order_by(PluginVersion.id.desc())
                    )
                ).all()
            )
        latest: dict[str, PluginVersion] = {}
        version_counts: dict[str, int] = {}
        pending_counts: dict[str, int] = {}
        for version in versions:
            version_counts[version.plugin_id] = version_counts.get(version.plugin_id, 0) + 1
            if version.review_status == REVIEW_STATUS_PENDING:
                pending_counts[version.plugin_id] = pending_counts.get(version.plugin_id, 0) + 1
            # 已按 id 倒序，第一次出现的即最新提交版本。
            latest.setdefault(version.plugin_id, version)

        status = str(review_status or "").strip()
        if status:
            rows = [
                row
                for row in rows
                if latest.get(row.plugin_id) is not None
                and latest[row.plugin_id].review_status == status
            ]

        total = len(rows)
        page = max(1, page)
        page_size = max(1, page_size)
        start = (page - 1) * page_size
        page_rows = rows[start : start + page_size]
        current_versions = await self._current_versions(session, page_rows)
        owner_names = await self._owner_names(
            session, {row.owner_id for row in page_rows}
        )
        items = []
        for row in page_rows:
            item = serialize_plugin(
                row,
                current_versions.get(row.current_version_id),
                owner_names.get(row.owner_id),
            )
            latest_version = latest.get(row.plugin_id)
            item["latest_version"] = (
                serialize_version(latest_version) if latest_version is not None else None
            )
            item["version_count"] = version_counts.get(row.plugin_id, 0)
            item["pending_count"] = pending_counts.get(row.plugin_id, 0)
            items.append(item)
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    async def get_plugin_detail(
        self, session: AsyncSession, user: User, plugin_id: str
    ) -> dict[str, Any]:
        """
        查询单个插件详情及其全部版本。

        Args:
            session: 数据库会话。
            user: 当前登录用户。
            plugin_id: 插件标识。
        Returns:
            插件信息与版本列表。
        Raises:
            BusinessError: 插件不存在或无权查看。
        """
        plugin = await self._get_visible_plugin(session, user, plugin_id)
        versions = (
            await session.scalars(
                select(PluginVersion)
                .where(
                    PluginVersion.plugin_id == plugin.plugin_id,
                    PluginVersion.is_deleted.is_(False),
                )
                .order_by(PluginVersion.id.desc())
            )
        ).all()
        current = next(
            (item for item in versions if item.id == plugin.current_version_id), None
        )
        owner_names = await self._owner_names(session, {plugin.owner_id})
        return {
            "plugin": serialize_plugin(
                plugin, current, owner_names.get(plugin.owner_id)
            ),
            "versions": [serialize_version(item) for item in versions],
        }

    @staticmethod
    def list_capability_catalog() -> list[dict[str, Any]]:
        """返回能力目录，供审核面板按风险等级展示可选能力。"""
        return capability_catalog()

    async def delete_plugin(
        self, session: AsyncSession, user: User, plugin_id: str
    ) -> dict[str, Any]:
        """
        删除插件：管理员可删任意插件，其他用户只能删自己提交的插件。

        删除范围包括插件主记录、全部版本、全部安装记录与磁盘包目录，并释放
        机器人分配。版本记录物删除，删除后同一个插件标识与版本号可以重新提交；
        审核流水保留，用于追溯谁在什么时候删掉了哪个插件。

        Args:
            session: 数据库会话。
            user: 当前登录用户。
            plugin_id: 插件标识。
        Returns:
            删除结果，含版本数、受影响的安装数与清理掉的包目录。
        Raises:
            BusinessError: 插件不存在、无权删除或包目录清理失败。
        """
        plugin = await session.scalar(
            select(Plugin).where(
                Plugin.plugin_id == plugin_id, Plugin.is_deleted.is_(False)
            )
        )
        if plugin is None:
            raise BusinessError("插件不存在或已被删除", CODE_PLUGIN_NOT_FOUND)
        if not is_admin(user) and plugin.owner_id != user.id:
            raise BusinessError(
                "只有管理员或插件提交者本人可以删除该插件",
                CODE_PLUGIN_FORBIDDEN,
            )

        plugin_name = plugin.name
        was_published = plugin.is_published
        versions = (
            await session.scalars(
                select(PluginVersion).where(
                    PluginVersion.plugin_id == plugin_id,
                    PluginVersion.is_deleted.is_(False),
                )
            )
        ).all()
        installations = (
            await session.scalars(
                select(PluginInstallation).where(
                    PluginInstallation.plugin_id == plugin_id,
                    PluginInstallation.is_deleted.is_(False),
                )
            )
        ).all()

        # 先清磁盘：文件删不掉就整体失败，避免留下“插件已删、包还在”的孤儿目录。
        removable, removed_dir, failure = self.source_service.remove_plugin_packages(
            plugin_id
        )
        if not removable:
            raise BusinessError(failure, CODE_PLUGIN_REQUEST_INVALID)

        for installation in installations:
            installation.is_deleted = True
            installation.is_enabled = False
            installation.updated_at = beijing_now()
            await soft_delete_bindings(session, installation.id)
        for version in versions:
            await session.delete(version)

        plugin.is_deleted = True
        plugin.is_published = False
        plugin.current_version_id = None
        plugin.updated_at = beijing_now()
        append_review_log(
            session,
            plugin_id,
            "",
            "delete",
            VISIBILITY_PUBLISHED if was_published else VISIBILITY_UNPUBLISHED,
            "deleted",
            user.id,
            f"删除插件：清理 {len(versions)} 个版本、{len(installations)} 个安装",
        )
        await session.commit()
        logger.info(
            "插件已删除，插件=%s，操作人=%s，版本数=%s，安装数=%s，包目录=%s",
            plugin_id,
            user.username,
            len(versions),
            len(installations),
            removed_dir or "（无）",
        )
        return {
            "plugin_id": plugin_id,
            "name": plugin_name,
            "deleted_versions": len(versions),
            "affected_installations": len(installations),
            "removed_package_dir": removed_dir,
        }

    async def _load_or_create_plugin(
        self,
        session: AsyncSession,
        user: User,
        manifest: dict[str, Any],
        source_type: str,
        repo_url: str,
    ) -> Plugin:
        """按插件标识查找或创建主表记录，并校验归属。"""
        plugin_id = str(manifest["id"])
        plugin = await session.scalar(
            select(Plugin).where(Plugin.plugin_id == plugin_id)
        )
        author = manifest["author"]
        values = {
            "name": str(manifest["name"]),
            "summary": manifest_summary(str(manifest["description"])),
            "description": str(manifest["description"]),
            "author_name": str(author["name"]),
            "author_url": str(author.get("url") or ""),
            "homepage": str(manifest["homepage"]),
            "license": str(manifest["license"]),
            "source_type": source_type,
            "repo_url": repo_url,
            "icon_path": str(manifest["icon"]),
        }
        if plugin is None:
            plugin = Plugin(plugin_id=plugin_id, owner_id=user.id, **values)
            session.add(plugin)
            await session.flush()
            return plugin
        if not is_admin(user) and plugin.owner_id != user.id:
            raise BusinessError(
                f"插件标识 {plugin_id} 已被其他用户占用，请修改清单中的 id",
                CODE_PLUGIN_ID_TAKEN,
            )
        if plugin.is_deleted:
            plugin.is_deleted = False
        for key, value in values.items():
            setattr(plugin, key, value)
        plugin.updated_at = beijing_now()
        await session.flush()
        return plugin

    async def _get_visible_plugin(
        self, session: AsyncSession, user: User, plugin_id: str
    ) -> Plugin:
        """读取当前用户有权查看的插件，未上架且非本人提交时拒绝。"""
        plugin = await session.scalar(
            self._scope_visible(
                select(Plugin).where(
                    Plugin.plugin_id == plugin_id,
                    Plugin.is_deleted.is_(False),
                ),
                user,
            )
        )
        if plugin is None:
            raise BusinessError("插件不存在或无权查看", CODE_PLUGIN_NOT_FOUND)
        return plugin

    @staticmethod
    def _scope_visible(query: Select, user: User) -> Select:
        """为普通用户追加可见范围：已上架插件或本人提交的插件。"""
        if is_admin(user):
            return query
        return query.where(
            or_(Plugin.is_published.is_(True), Plugin.owner_id == user.id)
        )

    @staticmethod
    async def _current_versions(
        session: AsyncSession, plugins: list[Plugin]
    ) -> dict[int, PluginVersion]:
        """批量读取插件当前生效版本。"""
        version_ids = {
            plugin.current_version_id
            for plugin in plugins
            if plugin.current_version_id
        }
        if not version_ids:
            return {}
        rows = (
            await session.scalars(
                select(PluginVersion).where(PluginVersion.id.in_(version_ids))
            )
        ).all()
        return {row.id: row for row in rows}

    @staticmethod
    async def _owner_names(
        session: AsyncSession, owner_ids: set[int]
    ) -> dict[int, str]:
        """批量读取提交者用户名。"""
        if not owner_ids:
            return {}
        rows = await session.execute(
            select(User.id, User.username).where(User.id.in_(owner_ids))
        )
        return {row.id: row.username for row in rows}
