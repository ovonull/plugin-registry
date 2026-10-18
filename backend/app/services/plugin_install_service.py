"""
插件安装与启用服务。

功能：
1. 把已上架、且当前版本已通过审核的插件安装到账号上，安装后默认停用；
2. 一个账号对同一个插件只有一条安装记录，可分配给自己名下的多台机器人共用；
3. 按插件所属用户与机器人归属双重校验操作权限，配置与启停分开保存；
4. 调用插件配置模块校验并加密插件配置，路由层不接触配置细节；
5. 允许在版本已批准的能力范围内进一步收窄本安装实际生效的能力。

约定：
- 插件安装表只由本模块写入，路由层与事件主干都不直接操作该表；
- 启用状态默认关闭，避免新装插件立即影响线上群；
- 能力只允许收窄，不允许超出管理员批准的集合；
- 机器人分配关系全部委托 plugin_binding_service，本模块不重复实现。
"""

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.config import Settings
from app.common.permissions import owner_scope
from app.common.responses import BusinessError
from app.common.time_utils import beijing_now
from app.models.entities import User
from app.models.plugin import (
    Plugin,
    PluginInstallation,
    PluginInstallationBot,
    PluginVersion,
)
from app.services.plugin_binding_service import (
    load_binding_map,
    replace_bindings,
    resolve_bot_qqs,
    soft_delete_bindings,
)
from app.services.plugin_common import (
    CODE_INSTALL_REQUEST_INVALID,
    load_plugins,
    load_versions,
)
from app.services.plugin_config_service import (
    apply_config,
    config_schema,
    default_config,
    load_config,
    load_raw_config,
    missing_required,
)
from app.services.plugin_runtime_installations import load_enabled_installations
from app.services.plugin_serializers import load_code_list, serialize_installation


logger = logging.getLogger("uvicorn.error")


# 该账号已安装此插件
CODE_INSTALL_EXISTS = 40111
# 插件尚未上架
CODE_PLUGIN_NOT_PUBLISHED = 40112
# 插件当前版本未通过审核
CODE_PLUGIN_VERSION_NOT_APPROVED = 40113
# 已是当前生效版本，无需升级
CODE_INSTALL_UP_TO_DATE = 40114
# 指定的目标版本不存在
CODE_INSTALL_VERSION_NOT_FOUND = 40115
# 插件不存在
CODE_PLUGIN_NOT_FOUND = 40480
# 安装记录不存在
CODE_INSTALL_NOT_FOUND = 40481


class PluginInstallService:
    """插件安装、启停与配置管理。"""

    def __init__(self, settings: Settings) -> None:
        """记录应用配置。"""
        self.settings = settings

    async def list_installations(
        self,
        session: AsyncSession,
        user: User,
        page: int,
        page_size: int,
        bot_qq: str = "",
        plugin_id: str = "",
        enabled: bool | None = None,
        mine: bool = False,
    ) -> dict[str, Any]:
        """
        分页查询当前用户有权管理的插件安装。

        Args:
            session: 数据库会话。
            user: 当前登录用户。
            page: 页码，从 1 开始。
            page_size: 每页条数。
            bot_qq: 按已分配该机器人的安装筛选。
            plugin_id: 按插件名称或标识模糊筛选。
            enabled: 按启用状态筛选。
            mine: 只看当前账号自己的安装，管理员查看市场页时以本项为准。
        Returns:
            含 items、page、page_size、total、pages 的分页结果。
        """
        conditions = [PluginInstallation.is_deleted.is_(False)]
        if bot_qq:
            conditions.append(
                PluginInstallation.id.in_(
                    select(PluginInstallationBot.installation_id).where(
                        PluginInstallationBot.bot_qq == bot_qq,
                        PluginInstallationBot.is_deleted.is_(False),
                    )
                )
            )
        if plugin_id:
            # 界面上只给用户看插件名称，筛选时名称与标识片段都要能命中。
            conditions.append(
                or_(
                    PluginInstallation.plugin_id.contains(
                        plugin_id, autoescape=True
                    ),
                    PluginInstallation.plugin_id.in_(
                        select(Plugin.plugin_id).where(
                            Plugin.name.contains(plugin_id, autoescape=True)
                        )
                    ),
                )
            )
        if enabled is not None:
            conditions.append(PluginInstallation.is_enabled.is_(enabled))
        base_query = self._scope(select(PluginInstallation).where(*conditions), user)
        count_query = self._scope(
            select(func.count(PluginInstallation.id)).where(*conditions), user
        )
        if mine:
            base_query = base_query.where(PluginInstallation.owner_id == user.id)
            count_query = count_query.where(PluginInstallation.owner_id == user.id)
        total = int(await session.scalar(count_query) or 0)
        rows = (
            await session.scalars(
                base_query.order_by(PluginInstallation.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        plugins = await load_plugins(session, {row.plugin_id for row in rows})
        versions = await load_versions(
            session, {row.version_id for row in rows if row.version_id}
        )
        # 插件的 current_version_id 就是最新上架版本，用来提示「可升级」。
        latest_versions = await load_versions(
            session,
            {
                plugin.current_version_id
                for plugin in plugins.values()
                if plugin.current_version_id
            },
        )
        bindings = await load_binding_map(session, {row.id for row in rows})
        return {
            "items": [
                serialize_installation(
                    row,
                    plugins.get(row.plugin_id),
                    versions.get(row.version_id),
                    bindings.get(row.id, []),
                    latest_versions.get(
                        plugins[row.plugin_id].current_version_id
                        if row.plugin_id in plugins
                        else None
                    ),
                )
                for row in rows
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    async def install(
        self,
        session: AsyncSession,
        user: User,
        plugin_id: str,
        bot_qqs: list[str],
    ) -> dict[str, Any]:
        """
        为当前账号安装插件，并把安装分配给指定的机器人。

        同一账号对同一个插件只会有一条安装记录，之后新增机器人只需修改分配，
        不需要重新安装；不同账号各自安装、各自配置，数据互不可见。

        Args:
            session: 数据库会话。
            user: 当前登录用户。
            plugin_id: 插件标识。
            bot_qqs: 首次分配到哪些机器人，至少一台。
        Returns:
            新建或恢复后的安装记录。
        Raises:
            BusinessError: 插件未上架、版本未通过审核、机器人无权限或重复安装时拒绝。
        """
        plugin_key = str(plugin_id or "").strip()
        targets = await resolve_bot_qqs(session, user, list(bot_qqs or []))
        if not plugin_key or not targets:
            raise BusinessError(
                "请先选择要安装的插件，并至少选择一台机器人",
                CODE_INSTALL_REQUEST_INVALID,
            )
        plugin = await session.scalar(
            select(Plugin).where(
                Plugin.plugin_id == plugin_key, Plugin.is_deleted.is_(False)
            )
        )
        if plugin is None:
            raise BusinessError("插件不存在或已下架", 40480)
        if not plugin.is_published:
            raise BusinessError("该插件尚未上架，暂不能安装", CODE_PLUGIN_NOT_PUBLISHED)
        version = await self._current_approved_version(session, plugin)

        existing = await session.scalar(
            select(PluginInstallation).where(
                PluginInstallation.owner_id == user.id,
                PluginInstallation.plugin_id == plugin_key,
            )
        )
        if existing is not None and not existing.is_deleted:
            raise BusinessError(
                "当前账号已安装该插件，请到已装插件中修改配置或调整生效机器人",
                CODE_INSTALL_EXISTS,
            )

        values = {
            "owner_id": user.id,
            "version_id": version.id,
            "config_json": json.dumps(
                default_config(config_schema(version)), ensure_ascii=False
            ),
            "granted_capabilities_json": json.dumps(
                load_code_list(version.granted_capabilities_json), ensure_ascii=False
            ),
        }
        if existing is None:
            existing = PluginInstallation(
                plugin_id=plugin_key, is_enabled=False, **values
            )
            session.add(existing)
        else:
            existing.is_deleted = False
            existing.is_enabled = False
            for key, value in values.items():
                setattr(existing, key, value)
            existing.updated_at = beijing_now()
        await session.flush()
        await replace_bindings(session, existing.id, user.id, targets)
        await session.commit()
        await session.refresh(existing)
        logger.info(
            "插件已安装，插件=%s，版本=%s，机器人=%s，操作用户=%s",
            plugin_key,
            version.version,
            "、".join(targets),
            user.username,
        )
        return serialize_installation(existing, plugin, version, targets)

    async def update_installation(
        self,
        session: AsyncSession,
        user: User,
        install_id: int,
        is_enabled: bool | None = None,
        config: dict[str, Any] | None = None,
        granted_capabilities: list[str] | None = None,
        bot_qqs: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        修改某条安装记录的启用状态、插件配置、生效能力或生效机器人。

        Args:
            session: 数据库会话。
            user: 当前登录用户。
            install_id: 安装记录 ID。
            is_enabled: 新的启用状态，为空表示不修改。
            config: 新的插件配置，为空表示不修改。
            granted_capabilities: 本安装实际生效的能力，只能收窄。
            bot_qqs: 新的生效机器人列表，为空列表表示取消全部分配。
        Returns:
            更新后的安装记录。
        Raises:
            BusinessError: 无权操作、插件已下架、配置不合法或启用时没有机器人时拒绝。
        """
        row = await self._get_installation(session, user, install_id)
        plugin = await session.scalar(
            select(Plugin).where(Plugin.plugin_id == row.plugin_id)
        )
        version = await self._load_version(session, row.version_id)
        schema = config_schema(version)
        stored = load_raw_config(row.config_json)
        targets: list[str] | None = None
        if bot_qqs is not None:
            targets = await resolve_bot_qqs(session, user, list(bot_qqs))
        if config is not None:
            stored = apply_config(schema, config, stored)
            row.config_json = json.dumps(stored, ensure_ascii=False)
        if is_enabled is not None:
            if is_enabled:
                if plugin is None or plugin.is_deleted or not plugin.is_published:
                    raise BusinessError(
                        "插件已下架，不能启用，请联系管理员重新上架",
                        CODE_PLUGIN_NOT_PUBLISHED,
                    )
                if version is None or version.review_status != "approved":
                    raise BusinessError(
                        "插件当前版本未通过审核，不能启用",
                        CODE_PLUGIN_VERSION_NOT_APPROVED,
                    )
                # 没有分配到任何机器人时启用没有意义，先拦这一层再校验配置。
                effective = targets
                if effective is None:
                    bindings = await load_binding_map(session, {row.id})
                    effective = bindings.get(row.id, [])
                if not effective:
                    raise BusinessError(
                        "请先选择至少一台生效机器人，再开启该插件",
                        CODE_INSTALL_REQUEST_INVALID,
                    )
                missing = missing_required(schema, stored)
                if missing:
                    raise BusinessError(
                        "启用前请先填写必填配置项：" + "、".join(missing),
                        CODE_INSTALL_REQUEST_INVALID,
                    )
            row.is_enabled = bool(is_enabled)
        if granted_capabilities is not None:
            approved = load_code_list(
                version.granted_capabilities_json if version else ""
            )
            selected = [str(item) for item in granted_capabilities]
            illegal = sorted({item for item in selected if item not in approved})
            if illegal:
                raise BusinessError(
                    "不能启用管理员未批准的能力：" + "、".join(illegal),
                    CODE_INSTALL_REQUEST_INVALID,
                )
            row.granted_capabilities_json = json.dumps(
                [code for code in approved if code in selected], ensure_ascii=False
            )
        if targets is not None:
            await replace_bindings(session, row.id, row.owner_id, targets)
            # 机器人被全部取消时自动停用，避免出现「已开启但没有生效范围」的矛盾状态。
            if not targets and row.is_enabled and not is_enabled:
                row.is_enabled = False
                logger.info(
                    "插件安装已无生效机器人，自动停用，插件=%s，安装=%s",
                    row.plugin_id,
                    row.id,
                )
        row.updated_at = beijing_now()
        await session.commit()
        await session.refresh(row)
        if targets is None:
            bindings = await load_binding_map(session, {row.id})
            targets = bindings.get(row.id, [])
        return serialize_installation(row, plugin, version, targets)

    async def upgrade(
        self,
        session: AsyncSession,
        user: User,
        install_id: int,
        version: str = "",
    ) -> dict[str, Any]:
        """
        把一条安装记录切到插件的当前生效版本，或切到显式指定的已审核版本。

        升级只在用户主动触发时发生，不会因为插件发版而自动切换。切换时：

        - 配置按新版本的清单迁移，同名项保留原值（含加密的敏感项），
          新版本已移除的键丢弃，新增的键取默认值；
        - 生效能力取「管理员新批准」与「本安装原有」的交集，
          新版本新增的能力不会自动授予，需要用户自己到配置里勾选；
        - 新版本若加了必填项且当前留空，会把该安装自动停用，避免带病运行；
        - 升级成功后删除被替换掉的旧版本（记录 + 磁盘包目录），旧版本若仍被
          其他安装引用、或仍是插件当前生效版本，则原样保留。

        Args:
            session: 数据库会话。
            user: 当前登录用户。
            install_id: 安装记录 ID。
            version: 目标版本号，留空表示升级到插件当前生效版本；
                传入已审核的旧版本号即可回滚。
        Returns:
            升级后的安装信息，另附来源版本、被丢弃的配置键、待补勾能力与缺失必填项。
        Raises:
            BusinessError: 无权操作、插件已下架、目标版本不存在或未审核、已是最新版本时拒绝。
        """
        row = await self._get_installation(session, user, install_id)
        plugin = await session.scalar(
            select(Plugin).where(
                Plugin.plugin_id == row.plugin_id, Plugin.is_deleted.is_(False)
            )
        )
        if plugin is None:
            raise BusinessError("插件不存在或已删除，无法升级", CODE_PLUGIN_NOT_FOUND)
        if not plugin.is_published:
            raise BusinessError(
                "插件已被下架，暂时不能升级，请联系管理员重新上架",
                CODE_PLUGIN_NOT_PUBLISHED,
            )
        target = await self._resolve_target_version(session, plugin, version)
        if target.id == row.version_id:
            raise BusinessError(
                "当前已经是这个版本，无需升级", CODE_INSTALL_UP_TO_DATE
            )
        previous = await self._load_version(session, row.version_id)
        # 旧版本记录随后可能被物理删除，先把版本号取出来，避免删除后再读已失效的对象。
        previous_version = previous.version if previous else ""

        schema = config_schema(target)
        stored = load_raw_config(row.config_json)
        declared = {str(item.get("key") or "") for item in schema if item.get("key")}
        migrated = default_config(schema)
        migrated.update(
            {key: value for key, value in stored.items() if key in declared}
        )
        dropped = sorted(key for key in stored if key not in declared)
        row.config_json = json.dumps(migrated, ensure_ascii=False)

        approved = load_code_list(target.granted_capabilities_json)
        granted_before = load_code_list(row.granted_capabilities_json)
        granted_after = [code for code in approved if code in granted_before]
        new_capabilities = [
            code for code in approved if code not in granted_before
        ]
        row.granted_capabilities_json = json.dumps(granted_after, ensure_ascii=False)

        row.version_id = target.id
        row.updated_at = beijing_now()
        missing = missing_required(schema, migrated)
        auto_disabled = False
        if missing and row.is_enabled:
            # 新版本要求填的必填项还没填，先停用，用户在配置里补完再手动开启。
            row.is_enabled = False
            auto_disabled = True
        await session.commit()
        await session.refresh(row)

        # 升级已经生效，接着把被替换掉的旧版本连同包目录一起清掉。
        purged = await self._purge_replaced_version(session, plugin, previous)

        bindings = await load_binding_map(session, {row.id})
        # 回滚到一个旧版本时，界面仍要提示「可以升回去」，所以这里按插件的
        # 当前生效版本判断是否可升级，而不是按刚换上的目标版本。
        latest = await self._load_version(session, plugin.current_version_id)
        logger.info(
            "插件已升级，插件=%s，%s -> %s，安装=%s，操作用户=%s",
            row.plugin_id,
            previous_version or "未知",
            target.version,
            row.id,
            user.username,
        )
        data = serialize_installation(
            row, plugin, target, bindings.get(row.id, []), latest
        )
        data.update(
            {
                "from_version": previous_version,
                "to_version": target.version,
                "dropped_config": dropped,
                "new_capabilities": new_capabilities,
                "missing_config": missing,
                "auto_disabled": auto_disabled,
                **purged,
            }
        )
        return data

    async def _purge_replaced_version(
        self,
        session: AsyncSession,
        plugin: Plugin,
        version: PluginVersion | None,
    ) -> dict[str, Any]:
        """
        清理升级后被替换掉的旧版本，记录与磁盘包目录一起删除。

        只有「没有任何有效安装在使用、也不是插件当前生效版本」的旧版本才会被清掉；
        任一条件不满足就原样保留，避免删掉别人还在跑的版本或唯一可安装的在线版本。

        Args:
            session: 数据库会话。
            plugin: 插件主记录。
            version: 升级前的版本记录，可能为空。
        Returns:
            含被清理版本号、删除目录与跳过原因的字典；清理成功时前两项有值。
        """
        result: dict[str, Any] = {
            "purged_version": "",
            "purged_package_dir": "",
            "purge_skipped_reason": "",
        }
        if version is None:
            result["purge_skipped_reason"] = "升级前的版本记录已不存在"
            return result
        if plugin.current_version_id == version.id:
            result["purge_skipped_reason"] = "该版本仍是插件当前生效版本，保留以便安装与回滚"
            return result
        in_use = await session.scalar(
            select(func.count())
            .select_from(PluginInstallation)
            .where(
                PluginInstallation.version_id == version.id,
                PluginInstallation.is_deleted.is_(False),
            )
        )
        if in_use:
            result["purge_skipped_reason"] = f"还有 {in_use} 个安装在使用该版本"
            return result

        removable, removed_dir, failure = await self._purge_package_dir(
            version.package_path
        )
        if not removable:
            result["purge_skipped_reason"] = failure
            return result

        purged_version = version.version
        await session.delete(version)
        await session.commit()
        result["purged_version"] = purged_version
        result["purged_package_dir"] = removed_dir
        logger.info(
            "插件旧版本已清理，插件=%s，版本=%s，包目录=%s",
            plugin.plugin_id,
            purged_version,
            removed_dir or "（记录中没有包目录）",
        )
        return result

    async def _purge_package_dir(self, package_path: str) -> tuple[bool, str, str]:
        """
        删除某个版本的插件包目录。

        Windows 上刚被插件子进程读过的文件可能短暂占用，因此删除失败会重试几次，
        仍然失败就保留版本记录，避免出现「记录没了但目录还在」的孤儿包。

        Args:
            package_path: 版本记录里的落盘目录。
        Returns:
            (是否可以继续删除版本记录, 实际删除的目录, 失败或跳过原因)。
        """
        if not package_path:
            return True, "", ""
        root = Path(self.settings.plugin_data_dir).expanduser().resolve() / "packages"
        try:
            target = Path(package_path).expanduser().resolve()
        except OSError as error:
            return False, "", f"插件包目录无法解析：{error}"
        if target == root or not str(target).startswith(str(root) + os.sep):
            # 只允许删 packages 目录内部的子目录，历史脏数据指向别处时坚决不动。
            return False, "", "插件包目录不在插件数据目录内，已跳过文件删除"
        if not target.exists():
            return True, "", ""
        for attempt in range(3):
            try:
                shutil.rmtree(target)
                return True, str(target), ""
            except OSError as error:
                if attempt == 2:
                    logger.error(
                        "插件包目录删除失败，目录=%s，错误类型=%s，错误=%s",
                        target,
                        type(error).__name__,
                        error,
                    )
                    return False, "", f"插件包目录删除失败：{error}"
                await asyncio.sleep(0.2)
        return True, str(target), ""

    async def uninstall(
        self, session: AsyncSession, user: User, install_id: int
    ) -> None:
        """软删除一条安装记录，同时关闭启用状态并释放全部机器人分配。"""
        row = await self._get_installation(session, user, install_id)
        row.is_deleted = True
        row.is_enabled = False
        row.updated_at = beijing_now()
        await soft_delete_bindings(session, row.id)
        await session.commit()
        logger.info(
            "插件已卸载，插件=%s，操作用户=%s",
            row.plugin_id,
            user.username,
        )

    @staticmethod
    async def enabled_installations(
        session: AsyncSession, bot_qq: str
    ) -> list[dict[str, Any]]:
        """
        读取某机器人上已启用、且插件仍处于上架状态的安装。

        Args:
            session: 数据库会话。
            bot_qq: 机器人 QQ。
        Returns:
            运行期所需的精简信息，具体取数逻辑见 plugin_runtime_installations。
        """
        return await load_enabled_installations(session, bot_qq)

    @staticmethod
    def _scope(query: Select, user: User) -> Select:
        """为普通用户追加安装归属过滤。"""
        owner_id = owner_scope(user)
        if owner_id is not None:
            query = query.where(PluginInstallation.owner_id == owner_id)
        return query

    @staticmethod
    async def _load_version(
        session: AsyncSession, version_id: int | None
    ) -> PluginVersion | None:
        """按 ID 读取版本记录，未关联版本时返回空。"""
        if not version_id:
            return None
        return await session.scalar(
            select(PluginVersion).where(PluginVersion.id == version_id)
        )

    @staticmethod
    async def _current_approved_version(
        session: AsyncSession, plugin: Plugin
    ) -> PluginVersion:
        """读取插件当前生效版本，未通过审核时给出明确提示。"""
        version = (
            await session.scalar(
                select(PluginVersion).where(
                    PluginVersion.id == plugin.current_version_id,
                    PluginVersion.is_deleted.is_(False),
                )
            )
            if plugin.current_version_id
            else None
        )
        if version is None or version.review_status != "approved":
            raise BusinessError(
                "插件当前版本未通过审核，暂时不能安装",
                CODE_PLUGIN_VERSION_NOT_APPROVED,
            )
        return version

    async def _resolve_target_version(
        self,
        session: AsyncSession,
        plugin: Plugin,
        version: str,
    ) -> PluginVersion:
        """
        解析升级目标版本：留空取插件当前生效版本，否则按版本号取指定版本。

        Args:
            session: 数据库会话。
            plugin: 插件主表记录。
            version: 目标版本号，可为空。
        Returns:
            可用于升级的版本记录。
        Raises:
            BusinessError: 目标版本不存在或未通过审核。
        """
        if not version:
            try:
                return await self._current_approved_version(session, plugin)
            except BusinessError as error:
                raise BusinessError(
                    "插件当前生效版本未通过审核，暂时不能升级",
                    CODE_PLUGIN_VERSION_NOT_APPROVED,
                ) from error
        row = await session.scalar(
            select(PluginVersion).where(
                PluginVersion.plugin_id == plugin.plugin_id,
                PluginVersion.version == version,
                PluginVersion.is_deleted.is_(False),
            )
        )
        if row is None:
            raise BusinessError(
                f"插件没有 {version} 这个版本", CODE_INSTALL_VERSION_NOT_FOUND
            )
        if row.review_status != "approved":
            raise BusinessError(
                f"版本 {version} 未通过审核，不能切换", CODE_PLUGIN_VERSION_NOT_APPROVED
            )
        return row

    async def _get_installation(
        self, session: AsyncSession, user: User, install_id: int
    ) -> PluginInstallation:
        """读取当前用户有权管理的安装记录。"""
        row = await session.scalar(
            self._scope(
                select(PluginInstallation).where(
                    PluginInstallation.id == install_id,
                    PluginInstallation.is_deleted.is_(False),
                ),
                user,
            )
        )
        if row is None:
            raise BusinessError("插件安装记录不存在或无权操作", CODE_INSTALL_NOT_FOUND)
        return row


