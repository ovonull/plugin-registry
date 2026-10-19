"""插件库公开索引导出服务。

从数据库读取已上架插件及其当前生效版本，输出两类公开数据：

1. ``registry/index.json``：供 NapCatQQ Service 插件市场定时同步安装；
2. 插件展示首页需要的列表、详情与统计（无需登录即可访问）。

说明：公开数据只包含已上架且当前版本审核通过的插件，任何待审核、
已拒绝或已下架的内容都不会出现在这里。
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plugin import Plugin, PluginInstallation, PluginVersion


SCHEMA_VERSION = 1

# 首页排序可选值，非法值统一回落到最新发布。
SORT_LATEST = "latest"
SORT_POPULAR = "popular"
SORT_NAME = "name"
SORT_CHOICES = (SORT_LATEST, SORT_POPULAR, SORT_NAME)


@dataclass(slots=True)
class RegistryExportService:
    """公开索引导出服务。"""

    def _parse_manifest(self, raw: str | None) -> dict[str, Any]:
        """安全解析版本清单 JSON，失败时返回空对象。"""
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _parse_list(self, raw: str | None) -> list[str]:
        """安全解析入库的字符串数组，失败时返回空列表。"""
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        return [str(item) for item in data]

    def _iso(self, value: datetime | None) -> str:
        """把数据库时间统一成 UTC ISO 字符串，前端负责本地化展示。"""
        if not value:
            return ""
        return value.astimezone(timezone.utc).isoformat()

    def _author_fields(
        self, manifest: dict[str, Any], plugin: Plugin
    ) -> tuple[str, str]:
        """
        从清单里取出作者名称与主页。

        清单规范里 author 是 ``{name, url}`` 对象，旧清单可能直接写字符串，
        两种情况都要兼容；直接 str(dict) 会把原始 Python 字面量吐到页面上。
        """
        raw = manifest.get("author")
        if isinstance(raw, dict):
            name = str(raw.get("name") or "").strip()
            url = str(raw.get("url") or "").strip()
            return name or plugin.author_name or "", url or plugin.author_url or ""
        if isinstance(raw, str) and raw.strip():
            return raw.strip(), plugin.author_url or ""
        return plugin.author_name or "", plugin.author_url or ""

    def _version_payload(self, plugin: Plugin, version: PluginVersion) -> dict[str, Any]:
        """把插件与当前版本转换为索引条目。"""
        manifest = self._parse_manifest(version.manifest_json)
        author_name, _ = self._author_fields(manifest, plugin)
        # 插件版本表没有独立的发布时间字段，审核通过时间即对外可见时间，
        # 尚未记录审核时间的版本退回创建时间，保证排序不会出现空值抖动。
        published_at = self._iso(version.reviewed_at or version.created_at)
        return {
            "plugin_id": plugin.plugin_id,
            "name": str(manifest.get("name") or plugin.name),
            "version": version.version,
            "summary": str(manifest.get("description") or plugin.summary or ""),
            "description": str(manifest.get("description_long") or plugin.description or ""),
            "author": author_name,
            "homepage": str(manifest.get("homepage") or plugin.homepage or ""),
            "source_type": plugin.source_type,
            "repo_url": plugin.repo_url or "",
            "source_ref": version.source_ref or "",
            "package_url": "",
            "checksum": version.checksum or "",
            "capabilities": manifest.get("capabilities") or [],
            "min_host_version": version.min_host_version or "",
            "max_host_version": version.max_host_version or "",
            "changelog": str(manifest.get("changelog") or ""),
            "published_at": published_at,
        }

    def _public_entry(
        self,
        plugin: Plugin,
        version: PluginVersion,
        install_count: int,
    ) -> dict[str, Any]:
        """构造展示首页用的插件条目，字段与索引条目保持同源。"""
        base = self._version_payload(plugin, version)
        _, author_url = self._author_fields(
            self._parse_manifest(version.manifest_json), plugin
        )
        # 卡片上只放首段短简介，完整说明留在 description 里给详情弹窗，
        # 否则清单里的一段长文本会把卡片撑得高低不齐。
        base["summary"] = (plugin.summary or "").strip() or base["summary"]
        base.update(
            {
                "icon_url": plugin.icon_path or "",
                "author_url": author_url,
                "license": plugin.license or "",
                # 公开页面只暴露已批准的能力，未批准的能力不对外展示。
                "capabilities": self._parse_list(
                    version.granted_capabilities_json
                )
                or self._parse_list(version.capabilities_json),
                "install_count": install_count,
                "created_at": self._iso(plugin.created_at),
                "updated_at": self._iso(plugin.updated_at),
            }
        )
        return base

    async def _install_counts(self, session: AsyncSession) -> dict[str, int]:
        """按插件统计安装份数，作为首页热度的唯一来源。"""
        result = await session.execute(
            select(PluginInstallation.plugin_id, func.count(PluginInstallation.id))
            .where(PluginInstallation.is_deleted.is_(False))
            .group_by(PluginInstallation.plugin_id)
        )
        return {str(plugin_id): int(count) for plugin_id, count in result.all()}

    async def _published_pairs(
        self, session: AsyncSession
    ) -> list[tuple[Plugin, PluginVersion]]:
        """取出所有可公开展示的插件及其当前版本，跳过未审核通过的版本。"""
        result = await session.execute(
            select(Plugin).where(Plugin.is_published.is_(True), Plugin.is_deleted.is_(False))
        )
        plugins = list(result.scalars())
        version_ids = [item.current_version_id for item in plugins if item.current_version_id]
        if not version_ids:
            return []
        rows = await session.execute(
            select(PluginVersion).where(PluginVersion.id.in_(version_ids))
        )
        versions = {item.id: item for item in rows.scalars()}
        pairs: list[tuple[Plugin, PluginVersion]] = []
        for plugin in plugins:
            version = versions.get(plugin.current_version_id) if plugin.current_version_id else None
            if version is None or version.review_status != "approved" or version.is_deleted:
                continue
            pairs.append((plugin, version))
        return pairs

    async def build_index(self, session: AsyncSession) -> dict[str, Any]:
        """生成完整的公开索引数据。"""
        pairs = await self._published_pairs(session)
        entries = [self._version_payload(plugin, version) for plugin, version in pairs]
        entries.sort(key=lambda item: item["plugin_id"])
        return {
            "schema_version": SCHEMA_VERSION,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "plugins": entries,
        }

    async def list_plugins(
        self,
        session: AsyncSession,
        keyword: str = "",
        sort: str = SORT_LATEST,
        page: int = 1,
        page_size: int = 12,
    ) -> dict[str, Any]:
        """分页返回可公开展示的插件，支持关键词过滤与排序。"""
        pairs = await self._published_pairs(session)
        counts = await self._install_counts(session)
        entries = [
            self._public_entry(plugin, version, counts.get(plugin.plugin_id, 0))
            for plugin, version in pairs
        ]

        needle = keyword.strip().lower()
        if needle:
            entries = [
                item
                for item in entries
                if needle in item["name"].lower()
                or needle in item["plugin_id"].lower()
                or needle in item["summary"].lower()
                or needle in item["author"].lower()
                or any(needle in cap.lower() for cap in item["capabilities"])
            ]

        if sort == SORT_POPULAR:
            entries.sort(key=lambda item: (-item["install_count"], item["name"].lower()))
        elif sort == SORT_NAME:
            entries.sort(key=lambda item: item["name"].lower())
        else:
            entries.sort(key=lambda item: (item["published_at"] or "", item["name"].lower()), reverse=True)

        total = len(entries)
        page = max(1, page)
        page_size = max(1, min(page_size, 60))
        start = (page - 1) * page_size
        return {
            "items": entries[start : start + page_size],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    async def plugin_detail(
        self, session: AsyncSession, plugin_id: str
    ) -> dict[str, Any] | None:
        """返回单个公开插件详情，未上架或不存在时返回 None。"""
        pairs = await self._published_pairs(session)
        counts = await self._install_counts(session)
        for plugin, version in pairs:
            if plugin.plugin_id != plugin_id:
                continue
            entry = self._public_entry(plugin, version, counts.get(plugin.plugin_id, 0))
            entry["granted_capabilities"] = self._parse_list(
                version.granted_capabilities_json
            )
            entry["requested_capabilities"] = self._parse_list(
                version.capabilities_json
            )
            entry["entry"] = version.entry or ""
            return entry
        return None

    async def build_stats(self, session: AsyncSession) -> dict[str, Any]:
        """首页顶部统计：插件数、安装数、作者数。"""
        pairs = await self._published_pairs(session)
        counts = await self._install_counts(session)
        authors = {
            (plugin.author_name or "").strip()
            for plugin, _ in pairs
            if (plugin.author_name or "").strip()
        }
        return {
            "plugin_count": len(pairs),
            "install_count": sum(counts.get(plugin.plugin_id, 0) for plugin, _ in pairs),
            "author_count": len(authors),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
