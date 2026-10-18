"""
插件包来源归一化。

功能：
1. 把「代码仓库」与「上传压缩包」两种来源统一归一化成同一种落盘结构
   （data/plugins/packages/{插件标识}/{版本}/{包摘要}/）；
2. 解包前逐条校验压缩包内路径，拒绝绝对路径、目录穿越与盘符；
3. 限制包体积、解包后总体积、单文件体积与文件数量，防止压缩炸弹；
4. 从包内读取 manifest.json 并交给清单校验模块做严格校验；
5. 计算包内容 sha256，便于发现提交后内容被偷改。

约定：仓库来源只接受 tag 或 commit 作为版本引用，禁止使用分支名，
避免审核通过后仓库内容被继续修改。
"""

import hashlib
import io
import json
import logging
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.common.config import Settings
from app.common.responses import BusinessError
from app.services.plugin_manifest import normalize_manifest


logger = logging.getLogger(__name__)


# 来源地址、压缩包结构或仓库引用不合法
CODE_SOURCE_INVALID = 40094
# 插件包体积超限
CODE_PACKAGE_TOO_LARGE = 40095
# 拉取远程仓库失败
CODE_REPO_FETCH_FAILED = 40096

MANIFEST_FILE_NAME = "manifest.json"

GITHUB_API_BASE = "https://api.github.com"
# 探测版本只是三次轻量元数据查询，超时给太长会把提交请求拖住。
GITHUB_API_TIMEOUT = 15.0

GITHUB_REPO_PATTERN = re.compile(
    r"^https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
# 只允许版本号形式的 tag 或十六进制 commit，明确拒绝 main / master 这类分支名。
SOURCE_REF_PATTERN = re.compile(r"^(?:v?\d+\.\d+\.\d+|[0-9a-f]{7,40})$")

# 解包后总体积相对包体积的放大上限，以及解包相关硬上限
MAX_UNCOMPRESSED_RATIO = 10
MAX_UNCOMPRESSED_SIZE = 52428800
MAX_SINGLE_FILE_SIZE = 5242880
MAX_MEMBER_COUNT = 500

SKIPPED_DIRECTORIES = (".git/", "__pycache__/", ".idea/", ".vscode/", ".venv/")


@dataclass(slots=True)
class ExtractedPackage:
    """归一化后的插件包信息。"""

    plugin_id: str
    version: str
    manifest: dict[str, Any]
    package_dir: Path
    package_path: str
    checksum: str
    file_count: int


@dataclass(slots=True)
class RepoLatest:
    """仓库最新版本的探测结果。"""

    repo_url: str
    # 实际用于下载压缩包的引用：版本号形式的 tag，或者 tag 对应的提交哈希。
    ref: str
    # 探测来源：release（最新发行版）、tag（最新标签）、commit（默认分支最新提交）。
    kind: str
    # 人类可读的 tag 名；来自 commit 时为空。
    tag: str
    published_at: str


class PluginSourceService:
    """把仓库地址或上传的压缩包归一化成本地插件包目录。"""

    def __init__(self, settings: Settings) -> None:
        """记录插件数据根目录与体积限制。"""
        self.settings = settings
        self.data_root = Path(settings.plugin_data_dir).expanduser().resolve()

    @staticmethod
    def parse_github_repo(repo_url: str) -> tuple[str, str]:
        """
        解析 GitHub 仓库地址。

        Args:
            repo_url: 形如 https://github.com/owner/repo 的地址。
        Returns:
            (仓库所有者, 仓库名)。
        Raises:
            BusinessError: 地址不是受支持的 GitHub 仓库地址。
        """
        matched = GITHUB_REPO_PATTERN.match(str(repo_url or "").strip())
        if matched is None:
            raise BusinessError(
                "仓库地址只支持 https://github.com/所有者/仓库名 的形式",
                CODE_SOURCE_INVALID,
            )
        return matched.group(1), matched.group(2)

    @staticmethod
    def validate_source_ref(source_ref: str) -> str:
        """
        校验仓库版本引用。

        Args:
            source_ref: 提交上来的 tag 或 commit。
        Returns:
            规范化后的版本引用。
        Raises:
            BusinessError: 为空或使用了分支名。
        """
        value = str(source_ref or "").strip()
        if not value:
            raise BusinessError(
                "填写仓库地址时必须同时填写版本引用（tag 或 commit）",
                CODE_SOURCE_INVALID,
            )
        if not SOURCE_REF_PATTERN.match(value):
            raise BusinessError(
                "版本引用必须是 tag 或 commit（例如 v1.0.0、1.2.3 或提交哈希），"
                "不能填写分支名，避免审核通过后仓库内容被修改",
                CODE_SOURCE_INVALID,
            )
        return value

    async def download_repo_archive(self, repo_url: str, source_ref: str) -> bytes:
        """
        按固定 tag 或 commit 下载仓库压缩包。

        Args:
            repo_url: GitHub 仓库地址。
            source_ref: 已校验的 tag 或 commit。
        Returns:
            压缩包原始字节。
        Raises:
            BusinessError: 拉取失败或体积超限。失败时如实报错，不返回任何占位数据。
        """
        owner, repo = self.parse_github_repo(repo_url)
        # 归档统一走 codeload.github.com：它才是 GitHub 的归档 CDN 主机，
        # github.com/.../archive/ 只是 302 跳转到它。少一跳不仅更快，
        # 也避免了只放行 CDN 主机（企业代理、部分网络环境）时下载被拦——
        # 实测 github.com 被代理拦死时 codeload 仍 5/5 可用。
        # tag 名 / 分支名 / 提交哈希三种 ref 写法在 codeload 上通用。
        archive_url = f"https://codeload.github.com/{owner}/{repo}/zip/{source_ref}"
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=self.settings.plugin_repo_timeout,
            ) as client:
                response = await client.get(archive_url)
        except httpx.HTTPError as error:
            logger.error(
                "拉取插件仓库失败，地址=%s，引用=%s，错误类型=%s，错误=%s",
                repo_url,
                source_ref,
                type(error).__name__,
                error,
            )
            raise BusinessError(
                f"拉取仓库压缩包失败：{error}",
                CODE_REPO_FETCH_FAILED,
            ) from error
        if response.status_code == 404:
            raise BusinessError(
                "仓库地址或版本引用不存在，请确认该 tag 或 commit 已推送到远端",
                CODE_REPO_FETCH_FAILED,
            )
        if response.status_code != 200:
            raise BusinessError(
                f"拉取仓库压缩包失败，远端返回状态码 {response.status_code}",
                CODE_REPO_FETCH_FAILED,
            )
        content = response.content
        self.ensure_archive_size(content)
        return content

    def _github_headers(self) -> dict[str, str]:
        """GitHub 接口请求头，配置了令牌就带上以放宽频率限制。"""
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "NapCatQQ-Service-PluginMarket",
        }
        token = str(self.settings.plugin_repo_token or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _github_json(self, url: str) -> Any:
        """
        请求 GitHub 元数据接口。

        Args:
            url: 接口地址。
        Returns:
            解析后的 JSON；404、其他非 200 或响应不是 JSON 时返回 None，
            由调用方继续尝试下一种探测方式。
        Raises:
            BusinessError: 网络失败，或未认证调用次数已用尽。
        """
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=GITHUB_API_TIMEOUT
            ) as client:
                response = await client.get(url, headers=self._github_headers())
        except httpx.HTTPError as error:
            logger.error(
                "访问 GitHub 接口失败，地址=%s，错误类型=%s，错误=%s",
                url,
                type(error).__name__,
                error,
            )
            raise BusinessError(
                f"访问 GitHub 接口失败：{error}", CODE_REPO_FETCH_FAILED
            ) from error
        if response.status_code == 403 and response.headers.get(
            "x-ratelimit-remaining"
        ) == "0":
            raise BusinessError(
                "GitHub 接口调用次数已用尽（未认证每小时 60 次），"
                "请稍后再试，或在服务端配置 PLUGIN_REPO_TOKEN",
                CODE_REPO_FETCH_FAILED,
            )
        if response.status_code != 200:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    async def _ref_of_tag(self, api: str, tag: str) -> str:
        """
        把 tag 转成可以安全用于归档下载的引用。

        版本号形式的 tag 直接用（可读、且不会被当成分支）；
        其他写法（例如 release/1.0）转成它指向的提交哈希，
        避免斜杠破坏归档地址，也让引用不可变。
        """
        if SOURCE_REF_PATTERN.match(tag):
            return tag
        payload = await self._github_json(f"{api}/commits/{quote(tag, safe='')}")
        if isinstance(payload, dict):
            sha = str(payload.get("sha") or "").strip()
            if sha:
                return sha
        return tag

    async def resolve_latest_ref(self, repo_url: str) -> RepoLatest:
        """
        探测仓库的最新版本引用，不需要作者手填 tag。

        顺序：最新 release → 最新 tag → 默认分支最新 commit。
        用不可变的引用而不是分支名，保证审核通过后仓库内容不会被悄悄改掉。

        Args:
            repo_url: GitHub 仓库地址。
        Returns:
            探测结果，含实际引用、来源与 tag 名。
        Raises:
            BusinessError: 地址非法、接口失败，或仓库里连提交都没有。
        """
        owner, repo = self.parse_github_repo(repo_url)
        api = f"{GITHUB_API_BASE}/repos/{owner}/{repo}"

        release = await self._github_json(f"{api}/releases/latest")
        if isinstance(release, dict):
            tag = str(release.get("tag_name") or "").strip()
            if tag:
                return RepoLatest(
                    repo_url=repo_url,
                    ref=await self._ref_of_tag(api, tag),
                    kind="release",
                    tag=tag,
                    published_at=str(release.get("published_at") or ""),
                )

        tags = await self._github_json(f"{api}/tags?per_page=1")
        if isinstance(tags, list) and tags:
            tag = str((tags[0] or {}).get("name") or "").strip()
            if tag:
                return RepoLatest(
                    repo_url=repo_url,
                    ref=await self._ref_of_tag(api, tag),
                    kind="tag",
                    tag=tag,
                    published_at="",
                )

        commits = await self._github_json(f"{api}/commits?per_page=1")
        if isinstance(commits, list) and commits:
            sha = str((commits[0] or {}).get("sha") or "").strip()
            if sha:
                return RepoLatest(
                    repo_url=repo_url, ref=sha, kind="commit", tag="", published_at=""
                )
        raise BusinessError(
            "没有在该仓库探测到可用的 tag 或 commit，请确认仓库已推送版本标签，"
            "或改为手动填写 tag",
            CODE_REPO_FETCH_FAILED,
        )

    def peek_manifest(self, content: bytes) -> dict[str, Any]:
        """
        只读取压缩包内的清单，不落盘。

        用于「探测最新版本」时先告诉作者这个版本号，避免为了看一眼清单就写磁盘。

        Args:
            content: 压缩包原始字节。
        Returns:
            严格校验后的清单字典。
        Raises:
            BusinessError: 包体积超限、结构非法或清单校验失败。
        """
        self.ensure_archive_size(content)
        try:
            archive = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile as error:
            raise BusinessError(
                "插件包不是合法的 zip 压缩包", CODE_SOURCE_INVALID
            ) from error
        with archive:
            members = self._strip_single_root(self._safe_members(archive))
            self._check_uncompressed_size(members)
            return self._load_manifest(archive, members)

    async def fetch_repo_latest(self, repo_url: str) -> tuple[RepoLatest, dict[str, Any]]:
        """
        探测最新版本，并把该版本的压缩包下载下来读出清单。

        Args:
            repo_url: GitHub 仓库地址。
        Returns:
            (探测结果, 清单字典)。
        Raises:
            BusinessError: 探测、下载或清单校验失败。
        """
        latest = await self.resolve_latest_ref(repo_url)
        archive = await self.download_repo_archive(latest.repo_url, latest.ref)
        return latest, self.peek_manifest(archive)

    def ensure_archive_size(self, content: bytes) -> None:
        """校验提交的压缩包体积是否超过配置上限。"""
        if not content:
            raise BusinessError("插件包内容为空", CODE_SOURCE_INVALID)
        limit = self.settings.plugin_package_max_size
        if len(content) > limit:
            raise BusinessError(
                f"插件包不能超过 {limit // 1048576} MB，当前 {len(content) / 1048576:.1f} MB",
                CODE_PACKAGE_TOO_LARGE,
            )

    @staticmethod
    def _safe_members(archive: zipfile.ZipFile) -> list[tuple[str, zipfile.ZipInfo]]:
        """读取压缩包成员并校验路径，拒绝压缩包目录穿越。"""
        members: list[tuple[str, zipfile.ZipInfo]] = []
        for info in archive.infolist():
            if info.is_dir():
                continue
            raw_name = info.filename.replace("\\", "/")
            segments = [segment for segment in raw_name.split("/") if segment]
            if not segments:
                continue
            if raw_name.startswith("/") or ".." in segments or ":" in segments[0]:
                raise BusinessError(
                    f"压缩包内存在非法路径：{info.filename}",
                    CODE_SOURCE_INVALID,
                )
            members.append(("/".join(segments), info))
        if not members:
            raise BusinessError("压缩包内没有可用文件", CODE_SOURCE_INVALID)
        if len(members) > MAX_MEMBER_COUNT:
            raise BusinessError(
                f"插件包内文件数量不能超过 {MAX_MEMBER_COUNT} 个，当前 {len(members)} 个",
                CODE_SOURCE_INVALID,
            )
        return members

    @staticmethod
    def _strip_single_root(
        members: list[tuple[str, zipfile.ZipInfo]],
    ) -> list[tuple[str, zipfile.ZipInfo]]:
        """当全部文件位于同一个顶层目录时剥掉该层，兼容仓库 tar/zip 归档结构。"""
        tops = {name.split("/", 1)[0] for name, _info in members}
        if len(tops) != 1:
            return members
        top = next(iter(tops))
        if not all(name.startswith(top + "/") for name, _info in members):
            return members
        return [(name.split("/", 1)[1], info) for name, info in members]

    def _check_uncompressed_size(
        self, members: list[tuple[str, zipfile.ZipInfo]]
    ) -> int:
        """校验解包后的总体积与单文件体积，防止压缩炸弹。"""
        ratio_limit = self.settings.plugin_package_max_size * MAX_UNCOMPRESSED_RATIO
        size_limit = min(ratio_limit, MAX_UNCOMPRESSED_SIZE)
        total = 0
        for name, info in members:
            if info.file_size > MAX_SINGLE_FILE_SIZE:
                raise BusinessError(
                    f"插件包内单个文件不能超过 {MAX_SINGLE_FILE_SIZE // 1048576} MB：{name}",
                    CODE_PACKAGE_TOO_LARGE,
                )
            total += info.file_size
        if total > size_limit:
            raise BusinessError(
                f"插件包解压后总体积不能超过 {size_limit // 1048576} MB，当前 {total / 1048576:.1f} MB",
                CODE_PACKAGE_TOO_LARGE,
            )
        return total

    @staticmethod
    def _load_manifest(
        archive: zipfile.ZipFile, members: list[tuple[str, zipfile.ZipInfo]]
    ) -> dict[str, Any]:
        """从压缩包根部读取 manifest.json 并做严格校验。"""
        target = next(
            (info for name, info in members if name == MANIFEST_FILE_NAME), None
        )
        if target is None:
            raise BusinessError(
                "插件包根目录必须包含 manifest.json 清单文件",
                CODE_SOURCE_INVALID,
            )
        try:
            raw = archive.read(target).decode("utf-8")
        except UnicodeDecodeError as error:
            raise BusinessError(
                "插件包的 manifest.json 必须使用 UTF-8 编码",
                CODE_SOURCE_INVALID,
            ) from error
        except (zipfile.BadZipFile, OSError) as error:
            raise BusinessError(
                f"读取插件包内的 manifest.json 失败：{error}",
                CODE_SOURCE_INVALID,
            ) from error
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise BusinessError(
                f"插件包的 manifest.json 不是合法 JSON：{error.msg}",
                CODE_SOURCE_INVALID,
            ) from error
        return normalize_manifest(payload)

    def remove_plugin_packages(self, plugin_id: str) -> tuple[bool, str, str]:
        """
        删除某个插件在磁盘上的全部版本包目录。

        Args:
            plugin_id: 插件标识。
        Returns:
            (是否已删除干净, 实际删除的根目录, 失败或跳过原因)。
        """
        key = str(plugin_id or "").strip()
        if not key or "/" in key or "\\" in key or key in {".", ".."}:
            return False, "", "插件标识不合法，已跳过文件删除"
        packages_root = self.data_root / "packages"
        target = (packages_root / key).resolve()
        if target == packages_root or not str(target).startswith(
            str(packages_root) + os.sep
        ):
            # 只允许删 packages 目录下的插件子目录，脏数据指向别处时坚决不动。
            return False, "", "插件包目录不在插件数据目录内，已跳过文件删除"
        if not target.exists():
            return True, "", ""
        try:
            shutil.rmtree(target)
        except OSError as error:
            logger.error(
                "插件包目录删除失败，插件=%s，目录=%s，错误类型=%s，错误=%s",
                key,
                target,
                type(error).__name__,
                error,
            )
            return False, "", f"插件包目录删除失败：{error}"
        return True, str(target), ""

    def prepare_package(self, content: bytes) -> ExtractedPackage:
        """
        校验压缩包并归一化落盘。

        Args:
            content: 压缩包原始字节。
        Returns:
            归一化后的插件包信息，含清单、落盘目录与包摘要。
        Raises:
            BusinessError: 包体积超限、结构非法、清单校验失败或落盘失败。
        """
        self.ensure_archive_size(content)
        checksum = hashlib.sha256(content).hexdigest()
        try:
            archive = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile as error:
            raise BusinessError(
                "插件包不是合法的 zip 压缩包",
                CODE_SOURCE_INVALID,
            ) from error
        with archive:
            members = self._strip_single_root(self._safe_members(archive))
            self._check_uncompressed_size(members)
            manifest = self._load_manifest(archive, members)

            plugin_id = str(manifest["id"])
            version = str(manifest["version"])
            package_dir = (
                self.data_root / "packages" / plugin_id / version / checksum[:12]
            )
            self._write_members(archive, members, package_dir)
        entry = package_dir / str(manifest["entry"])
        if not entry.is_file():
            raise BusinessError(
                f"插件包内找不到清单声明的入口文件：{manifest['entry']}",
                CODE_SOURCE_INVALID,
            )
        return ExtractedPackage(
            plugin_id=plugin_id,
            version=version,
            manifest=manifest,
            package_dir=package_dir,
            package_path=str(package_dir),
            checksum=checksum,
            file_count=len(members),
        )

    @staticmethod
    def _write_members(
        archive: zipfile.ZipFile,
        members: list[tuple[str, zipfile.ZipInfo]],
        package_dir: Path,
    ) -> None:
        """按已校验的相对路径逐文件落盘，路径始终锁定在目标目录内。"""
        try:
            package_dir.mkdir(parents=True, exist_ok=True)
            for name, info in members:
                if name.startswith(SKIPPED_DIRECTORIES):
                    continue
                target = package_dir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(info))
        except OSError as error:
            logger.error(
                "写入插件包失败，目录=%s，错误类型=%s，错误=%s",
                package_dir,
                type(error).__name__,
                error,
            )
            raise BusinessError(
                f"写入插件包失败：{error}",
                CODE_PACKAGE_TOO_LARGE,
            ) from error
