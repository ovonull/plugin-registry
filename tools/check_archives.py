"""校验插件库里每个版本的归档包与记录的 checksum 是否一致。

用途：下游安装时如果报「插件包校验和不匹配」，先用这个脚本定位是哪几个版本坏了。
常见成因是同一版本号重复上传——旧代码在查重之前就写了归档文件，
新内容覆盖了旧包，但版本记录被拒、checksum 仍是旧的。

用法：
    python tools/check_archives.py
    python tools/check_archives.py --fix-hint
"""

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import asyncio

from sqlalchemy import select

from app.common.config import Settings
from app.models.plugin import Plugin, PluginVersion
from app.services.plugin_source_service import PluginSourceService


async def main(fix_hint: bool) -> int:
    settings = Settings.from_env()
    from app.common.database import Database

    db = Database(settings)
    await db.initialize()
    service = PluginSourceService(settings)
    broken: list[tuple[str, str, str]] = []
    try:
        async with db.session_factory() as session:
            rows = (
                await session.execute(
                    select(PluginVersion, Plugin)
                    .join(Plugin, Plugin.plugin_id == PluginVersion.plugin_id)
                    .where(
                        PluginVersion.is_deleted.is_(False),
                        Plugin.is_deleted.is_(False),
                    )
                    .order_by(Plugin.plugin_id, PluginVersion.version)
                )
            ).all()
            print(f"共 {len(rows)} 个版本待检查\n")
            for version, plugin in rows:
                archive = service.archive_path(version.plugin_id, version.version)
                if not archive.is_file():
                    broken.append(
                        (version.plugin_id, version.version, "归档文件不存在")
                    )
                    continue
                actual = hashlib.sha256(archive.read_bytes()).hexdigest()
                if actual != (version.checksum or ""):
                    broken.append(
                        (
                            version.plugin_id,
                            version.version,
                            f"checksum 不符：库里 {str(version.checksum)[:12]}，"
                            f"实际 {actual[:12]}",
                        )
                    )
    finally:
        await db.close()

    if not broken:
        print("全部一致，没有问题。")
        return 0

    print(f"发现 {len(broken)} 个不一致：\n")
    for plugin_id, version, reason in broken:
        print(f"  {plugin_id} {version}")
        print(f"    {reason}")
        print(f"    已上架：{plugin.name if plugin else '?'}")

    if fix_hint:
        print("\n修复建议：")
        print("  1. 把清单版本号加一（例如 1.0.0 → 1.0.1），重新打包上传；")
        print("  2. 不要复用旧版本号——即使把 checksum 手改对，包的字节已是新的，")
        print("     下次谁再重传同版本还会把文件覆盖掉。")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main("--fix-hint" in sys.argv)))
