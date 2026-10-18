"""插件库公开索引导出服务。

从数据库读取已上架插件及其当前生效版本，输出供 NapCatQQ Service
插件市场定时同步的 registry/index.json 数据结构。
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plugin import Plugin, PluginVersion


SCHEMA_VERSION = 1


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

    def _version_payload(self, plugin: Plugin, version: PluginVersion) -> dict[str, Any]:
        """把插件与当前版本转换为索引条目。"""
        manifest = self._parse_manifest(version.manifest_json)
        published_at = ""
        if version.published_at:
            published_at = version.published_at.astimezone(timezone.utc).isoformat()
        return {
            "plugin_id": plugin.plugin_id,
            "name": str(manifest.get("name") or plugin.name),
            "version": version.version,
            "summary": str(manifest.get("description") or plugin.summary or ""),
            "description": str(manifest.get("description_long") or plugin.description or ""),
            "author": str(manifest.get("author") or plugin.author_name or ""),
            "homepage": str(manifest.get("homepage") or plugin.homepage or ""),
            "source_type": plugin.source_type,
            "repo_url": plugin.repo_url or "",
            "source_ref": version.source_ref or "",
            "package_url": "",
            "checksum": version.checksum or "",
            "capabilities": manifest.get("capabilities") or [],
            "min_host_version": version.min_host_version or "",
            "max_host_version": version.max_host_version or "",
            "changelog": version.changelog or "",
            "published_at": published_at,
        }

    async def build_index(self, session: AsyncSession) -> dict[str, Any]:
        """生成完整的公开索引数据。"""
        result = await session.execute(
            select(Plugin).where(Plugin.is_published.is_(True), Plugin.is_deleted.is_(False))
        )
        plugins = list(result.scalars())
        entries: list[dict[str, Any]] = []
        for plugin in plugins:
            version = await session.get(PluginVersion, plugin.current_version_id)
            if version is None or version.review_status != "approved":
                continue
            entries.append(self._version_payload(plugin, version))
        entries.sort(key=lambda item: item["plugin_id"])
        return {
            "schema_version": SCHEMA_VERSION,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "plugins": entries,
        }
