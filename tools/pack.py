"""
插件打包脚本：把一个插件目录打成可直接上传的 zip。

用法：
    python tools/pack.py ../plugin-repos/video-parser
    python tools/pack.py ../plugin-repos/video-parser --out dist

也支持只写目录名：脚本会在自己所在目录下找同名插件目录；
推荐直接用插件仓库的克隆路径，避免维护第二份源码。

约定：
- zip 内只有一个顶层目录（目录名即插件目录名），manifest.json 位于该目录根部；
- 跳过 __pycache__、.pyc、tests、.dev 等开发期残留，避免把调试文件带进审核包。
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path


SKIP_DIRS = {"__pycache__", "tests", ".dev", ".git", ".idea"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".log", ".zip"}
PLUGINS_DIR = Path(__file__).resolve().parent


def collect_files(root: Path) -> list[Path]:
    """收集需要打进包的文件，跳过开发期残留。"""
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        files.append(path)
    return files


def build(plugin_name: str, out_dir: Path) -> Path:
    """
    打包指定插件目录。

    Args:
        plugin_name: 插件目录名（位于 plugins/ 下）。
        out_dir: 输出目录。
    Returns:
        生成的 zip 路径。
    Raises:
        SystemExit: 目录或清单缺失、清单不合法。
    """
    # 参数既可以是插件仓库的路径，也可以只是目录名（在脚本同级找）。
    root = Path(plugin_name).resolve()
    if not root.is_dir():
        root = PLUGINS_DIR / plugin_name
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"没有找到清单：{manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    plugin_id = manifest.get("id") or plugin_name
    version = manifest.get("version") or "0.0.0"

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{plugin_id}-{version}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in collect_files(root):
            arcname = str(Path(root.name) / path.relative_to(root)).replace("\\", "/")
            archive.write(path, arcname)
    return target


def main(argv: list[str]) -> int:
    """解析参数并执行打包。"""
    names = [item for item in argv[1:] if not item.startswith("--")]
    out_dir = PLUGINS_DIR.parent / "dist"
    if "--out" in argv:
        index = argv.index("--out")
        if index + 1 < len(argv):
            out_dir = Path(argv[index + 1]).resolve()
            names = [item for item in names if item != argv[index + 1]]
    if not names:
        available = sorted(
            path.name for path in PLUGINS_DIR.iterdir() if (path / "manifest.json").is_file()
        )
        print("用法：python tools/pack.py <插件目录或路径>")
        print("可用插件：" + "、".join(available))
        return 1
    for name in names:
        target = build(name, out_dir)
        size = target.stat().st_size
        print(f"已生成 {target}（{size / 1024:.1f} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
