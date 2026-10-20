"""仓库来源插件的自动同步服务。

轮询所有登记在库的代码仓库插件，探测作者仓库的最新 tag：

- 发现新版本 → 下载归档、校验清单、登记为版本记录；
- 按审核策略决定是否需要人工审核：
  * 插件从未有过审核通过的版本 → 首次入库，保留待审核；
  * 插件已有审核通过的版本 → 视为作者正常迭代，免审直接上架；
  * 提交者在信任名单内（或管理员）→ 信用作者，免审直接上架。

设计约定：
- 只处理 source_type=repo 且有 repo_url 的插件，压缩包来源无法自动同步；
- 单轮处理数量与轮询间隔都可配置，避免打满 GitHub 未认证配额（60 次/小时）；
- 任何单个仓库失败只记录原因，不影响同轮其它插件。
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.config import Settings
from app.common.database import Database
from app.common.permissions import is_admin
from app.common.responses import BusinessError
from app.common.time_utils import beijing_now
from app.models.entities import User
from app.models.plugin import (
    Plugin,
    PluginReview,
    PluginTrustedAuthor,
    PluginVersion,
)
from app.services.plugin_review_log_service import append_review_log
from app.services.plugin_serializers import load_code_list
from app.services.plugin_service import SOURCE_TYPE_REPO, PluginService


logger = logging.getLogger("uvicorn.error")

REVIEW_STATUS_APPROVED = "approved"
REVIEW_STATUS_PENDING = "pending"

# 启动后先等一会再跑第一轮，避免和数据库初始化、其它启动任务抢资源。
STARTUP_DELAY_SECONDS = 90


class PluginRepoSyncService:
    """定时把作者仓库里的新版本同步进插件库。"""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        plugin_service: PluginService,
    ) -> None:
        self.settings = settings
        self.database = database
        self.plugin_service = plugin_service
        self._task: asyncio.Task[None] | None = None
        self._running = False
        # 单轮上限小于插件总数时用游标轮转，保证每个仓库都会被轮到。
        self._cursor = 0
        self._last_run_at = ""
        self._last_result: dict[str, Any] = {}

    # ------------------------------------------------------------------ 生命周期

    async def start(self) -> None:
        """按配置启动常驻轮询任务；关闭时不创建任何协程。"""
        if not self.settings.plugin_repo_sync_enabled:
            logger.info("插件仓库自动同步已关闭（PLUGIN_REPO_SYNC_ENABLED=false）")
            return
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "插件仓库自动同步已启动，间隔=%s 秒，单轮上限=%s 个",
            self.settings.plugin_repo_sync_interval,
            self.settings.plugin_repo_sync_batch,
        )

    async def close(self) -> None:
        """停止轮询任务并等待退出。"""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        """常驻循环：启动延迟 → 同步一轮 → 睡到下一轮。

        间隔与开关每 30 秒重新读一次配置，后台改完不用重启服务。
        """
        try:
            await asyncio.sleep(STARTUP_DELAY_SECONDS)
            while True:
                if self.settings.plugin_repo_sync_enabled:
                    try:
                        await self.run_once()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - 单轮失败不能让循环退出
                        logger.warning("插件仓库自动同步失败：%s", exc)
                await self._sleep_between_rounds()
        except asyncio.CancelledError:
            raise

    async def _sleep_between_rounds(self) -> None:
        """按当前配置等待下一轮，等待期间配置变化能尽快生效。"""
        step = 30
        elapsed = 0
        while True:
            await asyncio.sleep(step)
            elapsed += step
            # 关闭后立即返回，由外层循环每 30 秒检查一次开关，避免一直空转。
            if not self.settings.plugin_repo_sync_enabled:
                return
            target = max(300, int(self.settings.plugin_repo_sync_interval))
            if elapsed >= target:
                return

    # ------------------------------------------------------------------ 核心逻辑

    async def run_once(self, limit: int | None = None) -> dict[str, Any]:
        """
        执行一轮同步。

        Args:
            limit: 本轮最多处理的插件数，默认取配置值。
        Returns:
            本轮统计：扫描数、登记数、免审上架数、跳过数、失败明细。
        """
        if self._running:
            return {"skipped": True, "reason": "上一轮同步尚未结束"}
        self._running = True
        batch = max(1, int(limit or self.settings.plugin_repo_sync_batch))
        stats: dict[str, Any] = {
            "scanned": 0,
            "total": 0,
            "submitted": 0,
            "auto_published": 0,
            "auto_approved": 0,
            "up_to_date": 0,
            "failed": [],
            "checked_at": "",
        }
        try:
            async with self.database.session_factory() as session:
                total = await self._repo_plugin_count(session)
                stats["total"] = total
                if total > batch:
                    # 超过单轮上限时按游标轮转，避免固定取前 N 个导致后面的仓库
                    # 永远排不到，长期得不到检查。
                    self._cursor %= total
                else:
                    self._cursor = 0
                plugins = await self._repo_plugins(session, batch, self._cursor)
                if total > batch:
                    self._cursor = (self._cursor + batch) % total
                stats["scanned"] = len(plugins)
                for plugin in plugins:
                    result = await self._sync_plugin(session, plugin)
                    if result == "submitted":
                        stats["submitted"] += 1
                    elif result == "auto_published":
                        stats["submitted"] += 1
                        stats["auto_published"] += 1
                    elif result == "auto_approved":
                        stats["submitted"] += 1
                        stats["auto_approved"] += 1
                    elif result == "up_to_date":
                        stats["up_to_date"] += 1
                    elif result:
                        stats["failed"].append(result)
        finally:
            self._running = False
            stats["checked_at"] = datetime.now(timezone.utc).isoformat()
            self._last_run_at = stats["checked_at"]
            self._last_result = stats
        if stats["submitted"] or stats["failed"]:
            logger.info(
                "插件仓库自动同步完成：扫描=%s，登记=%s，免审上架=%s，免审待上架=%s，已是最新=%s，失败=%s",
                stats["scanned"],
                stats["submitted"],
                stats["auto_published"],
                stats["auto_approved"],
                stats["up_to_date"],
                len(stats["failed"]),
            )
        # 失败原因逐条落到日志：否则只看到「失败=1」，要排查还得去翻接口内存态。
        for reason in stats["failed"]:
            logger.warning("插件仓库自动同步失败：%s", reason)
        return stats

    def last_result(self) -> dict[str, Any]:
        """返回最近一轮同步结果，供后台展示。"""
        return {"last_run_at": self._last_run_at, "result": self._last_result}

    async def _repo_plugin_count(self, session: AsyncSession) -> int:
        """统计需要检查的仓库来源插件数量，用于游标轮转。"""
        count = await session.scalar(
            select(func.count(Plugin.id)).where(
                Plugin.source_type == SOURCE_TYPE_REPO,
                Plugin.repo_url != "",
                Plugin.is_deleted.is_(False),
            )
        )
        return int(count or 0)

    async def _repo_plugins(
        self, session: AsyncSession, limit: int, offset: int
    ) -> list[Plugin]:
        """按插件标识稳定排序后取一段，配合游标保证长期均匀覆盖。"""
        rows = await session.execute(
            select(Plugin)
            .where(
                Plugin.source_type == SOURCE_TYPE_REPO,
                Plugin.repo_url != "",
                Plugin.is_deleted.is_(False),
            )
            .order_by(Plugin.plugin_id)
            .offset(offset)
            .limit(limit)
        )
        return list(rows.scalars())

    async def _sync_plugin(self, session: AsyncSession, plugin: Plugin) -> str:
        """
        同步单个插件。

        Returns:
            "up_to_date" | "submitted" | "auto_published" | 失败说明字符串。
        """
        try:
            probe = await self.plugin_service.probe_repo_latest(plugin.repo_url)
        except BusinessError as exc:
            return f"{plugin.plugin_id}: {exc.message}"
        except Exception as exc:  # noqa: BLE001 - 网络错误统一记录
            return f"{plugin.plugin_id}: 探测仓库失败（{exc}）"

        version = str(probe.get("version") or "").strip()
        if not version:
            return f"{plugin.plugin_id}: 仓库清单缺少版本号"
        if str(probe.get("plugin_id") or "") != plugin.plugin_id:
            return (
                f"{plugin.plugin_id}: 仓库清单的 id 变成了 {probe.get('plugin_id')}，"
                "与登记的插件标识不一致，已跳过"
            )

        exists = await session.scalar(
            select(PluginVersion.id).where(
                PluginVersion.plugin_id == plugin.plugin_id,
                PluginVersion.version == version,
                PluginVersion.is_deleted.is_(False),
            )
        )
        if exists is not None:
            return "up_to_date"

        owner = await session.get(User, plugin.owner_id)
        if owner is None:
            return f"{plugin.plugin_id}: 提交者账号已不存在，无法登记新版本"

        try:
            # 复用正常提交链路：下载归档、校验清单、落盘、写版本与审核流水，
            # 保证自动同步与人工提交产出的数据完全同构。
            await self.plugin_service.submit_package(
                session,
                owner,
                SOURCE_TYPE_REPO,
                repo_url=plugin.repo_url,
                source_ref=str(probe.get("ref") or ""),
            )
        except BusinessError as exc:
            return f"{plugin.plugin_id}@{version}: {exc.message}"
        except Exception as exc:  # noqa: BLE001
            return f"{plugin.plugin_id}@{version}: 登记失败（{exc}）"

        auto_reason = await self._auto_approve_reason(session, plugin, owner)
        if not auto_reason:
            logger.info(
                "插件新版本已登记待审核，插件=%s，版本=%s（首次入库需人工审核）",
                plugin.plugin_id,
                version,
            )
            return "submitted"

        publish = await self._should_publish(session, plugin, owner)
        await self._approve(session, plugin, version, auto_reason, publish)
        logger.info(
            "插件新版本已免审%s，插件=%s，版本=%s，原因=%s",
            "上架" if publish else "通过（等待管理员上架）",
            plugin.plugin_id,
            version,
            auto_reason,
        )
        return "auto_published" if publish else "auto_approved"

    async def _auto_approve_reason(
        self, session: AsyncSession, plugin: Plugin, owner: User
    ) -> str:
        """判断是否满足免审条件，返回写进审核流水的说明，不满足时返回空串。"""
        approved_count = await session.scalar(
            select(PluginVersion.id)
            .where(
                PluginVersion.plugin_id == plugin.plugin_id,
                PluginVersion.review_status == REVIEW_STATUS_APPROVED,
                PluginVersion.is_deleted.is_(False),
            )
            .limit(1)
        )
        if approved_count is not None:
            return "自动同步：该插件已有审核通过的版本，按后续版本免审规则自动通过"
        if is_admin(owner):
            return "自动同步：提交者为管理员，免审自动通过"
        trusted = await session.scalar(
            select(PluginTrustedAuthor.id).where(
                PluginTrustedAuthor.user_id == owner.id,
                PluginTrustedAuthor.is_active.is_(True),
                PluginTrustedAuthor.is_deleted.is_(False),
            )
        )
        if trusted is not None:
            return "自动同步：提交者在信任名单内，免审自动通过"
        return ""

    async def _should_publish(
        self, session: AsyncSession, plugin: Plugin, owner: User
    ) -> bool:
        """
        判断新版本能否直接上架。

        规则：
        - 插件当前在架 → 正常迭代，直接换成新版本；
        - 插件被管理员下架过 → 只免审通过，不自动上架，避免覆盖管理员的下架决定；
        - 从未上架且提交者是信任作者或管理员 → 直接上架。
        """
        if plugin.is_published:
            return True
        revoked = await session.scalar(
            select(PluginReview.id)
            .where(
                PluginReview.plugin_id == plugin.plugin_id,
                PluginReview.action == "unpublish",
            )
            .limit(1)
        )
        if revoked is not None:
            return False
        if is_admin(owner):
            return True
        trusted = await session.scalar(
            select(PluginTrustedAuthor.id).where(
                PluginTrustedAuthor.user_id == owner.id,
                PluginTrustedAuthor.is_active.is_(True),
                PluginTrustedAuthor.is_deleted.is_(False),
            )
        )
        return trusted is not None

    async def _approve(
        self,
        session: AsyncSession,
        plugin: Plugin,
        version: str,
        comment: str,
        publish: bool,
    ) -> None:
        """把刚登记的版本置为审核通过；publish 为真时同步切换上架版本。"""
        row = await session.scalar(
            select(PluginVersion).where(
                PluginVersion.plugin_id == plugin.plugin_id,
                PluginVersion.version == version,
            )
        )
        if row is None:
            return
        # 免审场景直接批准清单里申请的全部能力：作者已被信任，
        # 少批能力会导致插件运行时被网关拒绝，反而更难排查。
        granted = load_code_list(row.capabilities_json)
        previous = row.review_status
        row.review_status = REVIEW_STATUS_APPROVED
        row.reviewed_by = None
        row.reviewed_at = beijing_now()
        row.review_comment = comment
        row.granted_capabilities_json = json.dumps(granted, ensure_ascii=False)
        row.updated_at = beijing_now()

        if publish:
            plugin.current_version_id = row.id
            plugin.is_published = True
            plugin.updated_at = beijing_now()

        append_review_log(
            session,
            plugin.plugin_id,
            version,
            "approve",
            previous,
            REVIEW_STATUS_APPROVED,
            None,
            comment,
        )
        await session.commit()
        await session.refresh(row)
