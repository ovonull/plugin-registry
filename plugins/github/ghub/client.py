"""
GitHub REST API 客户端。

约定：
- 所有请求经 host.http_request 发出，域名白名单只声明 api.github.com；
- 令牌由用户填在插件配置或仓库条目里，只随请求头带出，不写进任何日志；
- 出网被拒、超时、连接失败统一收敛成 GitHubError，调用方只需要捕获一种；
- 不导入 app.*，本模块可在任意 Python 3.11 环境用假 host 单测。
"""

from __future__ import annotations

import urllib.parse
from typing import Any

API_ROOT = "https://api.github.com"

# GitHub 官方推荐的请求头，版本号固定，避免将来默认值变化带来的行为差异。
API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "napcat-github-plugin",
}

# 每类条目拉取条数。响应体按 20 万字符上限截取，条数太大容易把 JSON 撑爆。
PER_PAGE = 10

# 正文摘要最多保留的字符数，推送里只用到一行，不需要全文。
MAX_BODY_CHARS = 120


class GitHubError(Exception):
    """GitHub 接口调用失败：网络错误、限流、鉴权失败、仓库不存在等。"""


def _text(value: Any, limit: int = 0) -> str:
    """安全取字符串，可按长度截断。"""
    text = str(value if value is not None else "").strip()
    return text[:limit] if limit > 0 else text


def _login(value: Any) -> str:
    """从 user 对象里取登录名，退化时取昵称。"""
    if isinstance(value, dict):
        return _text(value.get("login"), 60) or _text(value.get("name"), 60)
    return _text(value, 60)


def _repo_path(repo: str) -> str:
    """把 owner/repo 转成 URL 路径段，杜绝非法字符。"""
    return urllib.parse.quote(str(repo or "").strip(), safe="/")


def _error_text(response: dict[str, Any], fallback: str) -> str:
    """从错误响应里取 GitHub 的 message 字段，取不到用兜底文案。"""
    payload = response.get("json")
    if isinstance(payload, dict):
        message = _text(payload.get("message"), 120)
        if message:
            return message
    return fallback


def _norm_issue(raw: dict[str, Any]) -> dict[str, Any] | None:
    """归一化 Issue（issues 端点里混着 PR，调用方已剔掉）。"""
    number = raw.get("number")
    if number is None:
        return None
    return {
        "id": str(number),
        "kind": "issue",
        "number": number,
        "title": _text(raw.get("title"), 80),
        "author": _login(raw.get("user")),
        "state": _text(raw.get("state"), 20) or "open",
        "updated_at": _text(raw.get("updated_at"), 40),
        "url": _text(raw.get("html_url"), 200),
        "summary": _text(raw.get("body"), MAX_BODY_CHARS),
    }


def _norm_pull(raw: dict[str, Any]) -> dict[str, Any] | None:
    """归一化 Pull Request，已合并的状态单独标记成 merged。"""
    number = raw.get("number")
    if number is None:
        return None
    state = "merged" if raw.get("merged") else (_text(raw.get("state"), 20) or "open")
    return {
        "id": str(number),
        "kind": "pull",
        "number": number,
        "title": _text(raw.get("title"), 80),
        "author": _login(raw.get("user")),
        "state": state,
        "updated_at": _text(raw.get("updated_at"), 40),
        "url": _text(raw.get("html_url"), 200),
        "summary": _text(raw.get("body"), MAX_BODY_CHARS),
    }


def _norm_commit(raw: dict[str, Any]) -> dict[str, Any] | None:
    """归一化提交，标题取提交信息首行，时间取作者提交时间。"""
    sha = _text(raw.get("sha"), 40)
    if not sha:
        return None
    commit = raw.get("commit") if isinstance(raw.get("commit"), dict) else {}
    author = commit.get("author") if isinstance(commit.get("author"), dict) else {}
    message = _text(commit.get("message"), 400)
    title = message.splitlines()[0] if message else ""
    return {
        "id": sha,
        "kind": "commit",
        "number": None,
        "title": title[:80],
        "author": _text(author.get("name"), 60) or _login(raw.get("author")),
        "state": "",
        "updated_at": _text(author.get("date"), 40),
        "url": _text(raw.get("html_url"), 200),
        "summary": message[:MAX_BODY_CHARS],
    }


def _norm_release(raw: dict[str, Any]) -> dict[str, Any] | None:
    """归一化 Release，草稿状态单独标记成 draft。"""
    release_id = raw.get("id")
    if release_id is None:
        return None
    tag = _text(raw.get("tag_name"), 60)
    name = _text(raw.get("name"), 80) or tag
    state = "draft" if raw.get("draft") else "published"
    return {
        "id": str(release_id),
        "kind": "release",
        "number": None,
        "title": name,
        "tag": tag,
        "author": _login(raw.get("author")),
        "state": state,
        "updated_at": _text(raw.get("published_at"), 40) or _text(raw.get("created_at"), 40),
        "url": _text(raw.get("html_url"), 200),
        "summary": _text(raw.get("body"), MAX_BODY_CHARS),
    }


class GitHubClient:
    """按仓库聚合 Issue / PR / Commit / Release 四类动态。"""

    def __init__(self, host: Any, timeout: float = 20.0) -> None:
        """
        Args:
            host: 插件宿主视图，只需要它有 http_request 方法。
            timeout: 单次请求超时秒数，宿主侧封顶 30 秒。
        """
        self._host = host
        self._timeout = max(1.0, min(float(timeout or 20.0), 30.0))

    async def _get(
        self, path: str, token: str, query: dict[str, Any] | None = None
    ) -> list[Any]:
        """发一次 GET，校验状态码并取出 JSON 数组。"""
        headers = dict(API_HEADERS)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = await self._request(
            "GET", path, headers=headers, query=query or {}
        )
        status = int(response.get("status") or 0)
        if status in (401, 403):
            raise GitHubError(_error_text(response, "令牌无效或已触发接口限流"))
        if status == 404:
            raise GitHubError(_error_text(response, "仓库不存在或令牌无权访问"))
        if status != 200:
            raise GitHubError(_error_text(response, f"接口返回 {status}"))
        payload = response.get("json")
        if not isinstance(payload, list):
            raise GitHubError("接口返回格式不符合预期")
        return payload

    async def _get_object(
        self, path: str, token: str, query: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """发一次 GET，校验状态码并取出 JSON 对象。"""
        headers = dict(API_HEADERS)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = await self._request(
            "GET", path, headers=headers, query=query or {}
        )
        status = int(response.get("status") or 0)
        if status in (401, 403):
            raise GitHubError(_error_text(response, "令牌无效或已触发接口限流"))
        if status == 404:
            raise GitHubError(_error_text(response, "仓库不存在或令牌无权访问"))
        if status != 200:
            raise GitHubError(_error_text(response, f"接口返回 {status}"))
        payload = response.get("json")
        if not isinstance(payload, dict):
            raise GitHubError("接口返回格式不符合预期")
        return payload

    async def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        query: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> dict[str, Any]:
        """统一走宿主持有的出网通道，异常收敛成 GitHubError。"""
        try:
            return await self._host.http_request(
                method,
                f"{API_ROOT}{path}",
                headers=headers,
                query=query or {},
                json_body=json_body,
                timeout=self._timeout,
                max_chars=200000,
            )
        except Exception as error:  # 出网被拒、超时、连接失败都在这里收敛
            detail = str(error).strip() or type(error).__name__
            raise GitHubError(f"接口调用失败：{detail[:120]}") from error

    async def list_issues(self, repo: str, token: str) -> list[dict[str, Any]]:
        """Issue 列表（GitHub 把 PR 也混在 issues 端点里，这里剔掉）。"""
        raw = await self._get(
            f"/repos/{_repo_path(repo)}/issues",
            token,
            {
                "state": "all",
                "sort": "updated",
                "direction": "desc",
                "per_page": PER_PAGE,
            },
        )
        items = []
        for entry in raw:
            if not isinstance(entry, dict) or "pull_request" in entry:
                continue
            item = _norm_issue(entry)
            if item is not None:
                items.append(item)
        return items

    async def list_pulls(self, repo: str, token: str) -> list[dict[str, Any]]:
        """Pull Request 列表。"""
        raw = await self._get(
            f"/repos/{_repo_path(repo)}/pulls",
            token,
            {
                "state": "all",
                "sort": "updated",
                "direction": "desc",
                "per_page": PER_PAGE,
            },
        )
        items = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            item = _norm_pull(entry)
            if item is not None:
                items.append(item)
        return items

    async def list_commits(
        self, repo: str, token: str, branch: str = ""
    ) -> list[dict[str, Any]]:
        """
        最近提交列表。

        Args:
            repo: owner/repo。
            token: 令牌。
            branch: 分支名，留空取默认分支。
        """
        query: dict[str, Any] = {"per_page": PER_PAGE}
        if branch:
            # GitHub 的 commits 端点用 sha 指定分支或提交。
            query["sha"] = branch
        raw = await self._get(f"/repos/{_repo_path(repo)}/commits", token, query)
        items = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            item = _norm_commit(entry)
            if item is not None:
                items.append(item)
        return items

    async def list_releases(self, repo: str, token: str) -> list[dict[str, Any]]:
        """Release 列表。"""
        raw = await self._get(
            f"/repos/{_repo_path(repo)}/releases", token, {"per_page": PER_PAGE}
        )
        items = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            item = _norm_release(entry)
            if item is not None:
                items.append(item)
        return items

    async def check_repo(self, repo: str, token: str) -> str:
        """
        验证仓库可访问。

        Args:
            repo: owner/repo。
            token: 令牌，公开仓库可为空。
        Returns:
            GitHub 返回的规范仓库名（大小写以 GitHub 为准）。
        Raises:
            GitHubError: 仓库不存在、无权访问或接口异常。
        """
        stats = await self.repo_stats(repo, token)
        if not stats["full_name"]:
            raise GitHubError("接口未返回仓库名")
        return str(stats["full_name"])

    async def repo_stats(self, repo: str, token: str) -> dict[str, Any]:
        """
        取仓库概况（Star 数、默认分支等）。

        Args:
            repo: owner/repo。
            token: 令牌，公开仓库可为空。
        Returns:
            含 full_name、stars、default_branch、private 的字典。
        Raises:
            GitHubError: 仓库不存在、无权访问或接口异常。
        """
        payload = await self._get_object(f"/repos/{_repo_path(repo)}", token)
        stars = payload.get("stargazers_count")
        return {
            "full_name": _text(payload.get("full_name"), 200),
            "stars": int(stars) if isinstance(stars, (int, float)) else 0,
            "default_branch": _text(payload.get("default_branch"), 100),
            "private": bool(payload.get("private")),
        }

    async def list_items(
        self,
        repo: str,
        token: str,
        branch: str = "",
        watch: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        按监控范围拉动态，按更新时间倒序。

        Args:
            repo: owner/repo。
            token: 令牌。
            branch: 提交只看这个分支，留空取默认分支。
            watch: 要拉的类型（issue/pull/commit/release），None 表示全拉。
                star 不在条目里，由 repo_stats 单独处理。
        Returns:
            归一化后的条目列表。
        Raises:
            GitHubError: 任一类请求失败。基线不完整时推送会漏动态，宁可整轮算失败。
        """
        kinds = set(watch) if watch is not None else {"issue", "pull", "commit", "release"}
        items: list[dict[str, Any]] = []
        if "issue" in kinds:
            items.extend(await self.list_issues(repo, token))
        if "pull" in kinds:
            items.extend(await self.list_pulls(repo, token))
        if "commit" in kinds:
            items.extend(await self.list_commits(repo, token, branch))
        if "release" in kinds:
            items.extend(await self.list_releases(repo, token))
        items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return items

    async def create_issue(self, repo: str, title: str, body: str, token: str) -> str:
        """
        在仓库里建 Issue。

        Args:
            repo: owner/repo。
            title: 标题。
            body: 正文，可为空。
            token: 具有写权限的 GitHub 令牌。
        Returns:
            新建 Issue 的网页链接。
        Raises:
            GitHubError: 没有令牌、接口报错或未返回链接。
        """
        if not token:
            raise GitHubError("尚未配置 GitHub 令牌，无法提交 Issue")
        headers = dict(API_HEADERS)
        headers["Authorization"] = f"Bearer {token}"
        response = await self._request(
            "POST",
            f"/repos/{_repo_path(repo)}/issues",
            headers=headers,
            json_body={"title": title, "body": body},
        )
        status = int(response.get("status") or 0)
        if status in (401, 403):
            raise GitHubError(_error_text(response, "令牌无效或没有该仓库的写权限"))
        if status != 201:
            raise GitHubError(_error_text(response, f"接口返回 {status}"))
        payload = response.get("json")
        if not isinstance(payload, dict):
            raise GitHubError("接口返回格式不符合预期")
        url = _text(payload.get("html_url"), 200)
        if not url:
            raise GitHubError("接口未返回 Issue 链接")
        return url
