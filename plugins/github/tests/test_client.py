"""GitHub 客户端测试：用假宿主返回预制响应，覆盖归一化与错误路径。"""

import asyncio
import unittest

from ghub.client import GitHubClient, GitHubError


class FakeHost:
    """按 URL 片段返回预制响应的假宿主，并记录调用。"""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []

    async def http_request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        for fragment, response in self.routes.items():
            if fragment in url:
                return response
        return {"status": 404, "json": {"message": "Not Found"}}


# 客户端方法是协程，用 asyncio.run 包一层同步调用，避免引入 pytest-asyncio。
def client_list_items(client, repo, token):
    """同步调用 list_items。"""
    return asyncio.run(client.list_items(repo, token))


def client_check_repo(client, repo, token):
    """同步调用 check_repo。"""
    return asyncio.run(client.check_repo(repo, token))


def client_create_issue(client, repo, title, body, token):
    """同步调用 create_issue。"""
    return asyncio.run(client.create_issue(repo, title, body, token))


def _issue_payload(number, updated, state="open", title="标题"):
    """GitHub issues 端点的原始结构。"""
    return {
        "number": number,
        "title": title,
        "state": state,
        "updated_at": updated,
        "html_url": f"https://github.com/o/r/issues/{number}",
        "body": "正文",
        "user": {"login": "someone"},
    }


def _pull_payload(number, updated, merged=False, state="open"):
    """GitHub pulls 端点的原始结构。"""
    return {
        "number": number,
        "title": "PR 标题",
        "state": state,
        "merged": merged,
        "updated_at": updated,
        "html_url": f"https://github.com/o/r/pull/{number}",
        "body": "PR 正文",
        "user": {"login": "someone"},
    }


def _commit_payload(sha, date):
    """GitHub commits 端点的原始结构。"""
    return {
        "sha": sha,
        "html_url": f"https://github.com/o/r/commit/{sha}",
        "commit": {
            "message": "fix: 第一行\n\n详细描述",
            "author": {"name": "someone", "date": date},
        },
        "author": {"login": "someone"},
    }


def _release_payload(release_id, published, tag="v1.0.0"):
    """GitHub releases 端点的原始结构。"""
    return {
        "id": release_id,
        "tag_name": tag,
        "name": tag,
        "draft": False,
        "published_at": published,
        "html_url": f"https://github.com/o/r/releases/tag/{tag}",
        "body": "发行说明",
        "author": {"login": "someone"},
    }


class ListItemsTest(unittest.TestCase):
    """四类动态的聚合与归一化。"""

    def _client(self):
        host = FakeHost(
            {
                "/issues": {"status": 200, "json": [_issue_payload(2, "2026-10-18T01:00:00Z")]},
                "/pulls": {"status": 200, "json": [_pull_payload(7, "2026-10-17T01:00:00Z")]},
                "/commits": {"status": 200, "json": [_commit_payload("abc123", "2026-10-16T01:00:00Z")]},
                "/releases": {"status": 200, "json": [_release_payload(99, "2026-10-15T01:00:00Z")]},
            }
        )
        return GitHubClient(host), host

    def test_aggregate_sorted(self):
        """四类一起拉，按更新时间倒序。"""
        client, host = self._client()
        items = client_list_items(client, "o/r", "")
        self.assertEqual(
            [item["kind"] for item in items], ["issue", "pull", "commit", "release"]
        )
        self.assertEqual(len(host.calls), 4)

    def test_pull_filtered_from_issues(self):
        """issues 端点里混着的 PR 被剔掉。"""
        host = FakeHost(
            {
                "/issues": {
                    "status": 200,
                    "json": [
                        _issue_payload(2, "2026-10-18T01:00:00Z"),
                        {**_issue_payload(3, "2026-10-18T02:00:00Z"), "pull_request": {}},
                    ],
                },
                "/pulls": {"status": 200, "json": []},
                "/commits": {"status": 200, "json": []},
                "/releases": {"status": 200, "json": []},
            }
        )
        client = GitHubClient(host)
        items = client_list_items(client, "o/r", "")
        self.assertEqual([item["id"] for item in items], ["2"])

    def test_normalization(self):
        """字段映射正确。"""
        client, _ = self._client()
        items = client_list_items(client, "o/r", "")
        issue = items[0]
        self.assertEqual(issue["id"], "2")
        self.assertEqual(issue["author"], "someone")
        self.assertEqual(issue["summary"], "正文")
        commit = items[2]
        self.assertEqual(commit["id"], "abc123")
        self.assertEqual(commit["title"], "fix: 第一行")
        release = items[3]
        self.assertEqual(release["tag"], "v1.0.0")

    def test_token_header(self):
        """有令牌时带上 Authorization 头。"""
        client, host = self._client()
        client_list_items(client, "o/r", "ghp_secret")
        authorization = [
            call["headers"].get("Authorization") for call in host.calls
        ]
        self.assertEqual(authorization, ["Bearer ghp_secret"] * 4)


class CheckRepoTest(unittest.TestCase):
    """仓库存在性验证。"""

    def test_full_name_returned(self):
        """返回 GitHub 的规范仓库名。"""
        host = FakeHost(
            {"/repos/": {"status": 200, "json": {"full_name": "SuInk/Diana"}}}
        )
        client = GitHubClient(host)
        name = client_check_repo(client, "suink/diana", "")
        self.assertEqual(name, "SuInk/Diana")

    def test_not_found(self):
        """404 时报仓库不存在。"""
        host = FakeHost()
        client = GitHubClient(host)
        with self.assertRaises(GitHubError) as ctx:
            client_check_repo(client, "no/such", "")
        self.assertIn("Not Found", str(ctx.exception))


class CreateIssueTest(unittest.TestCase):
    """提交 Issue。"""

    def test_created(self):
        """201 时返回链接，并带上 JSON 请求体。"""
        host = FakeHost(
            {
                "/issues": {
                    "status": 201,
                    "json": {"html_url": "https://github.com/o/r/issues/9"},
                }
            }
        )
        client = GitHubClient(host)
        url = client_create_issue(client, "o/r", "标题", "正文", "ghp_secret")
        self.assertEqual(url, "https://github.com/o/r/issues/9")
        call = host.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["json_body"], {"title": "标题", "body": "正文"})

    def test_without_token(self):
        """没有令牌直接报错，不发请求。"""
        host = FakeHost()
        client = GitHubClient(host)
        with self.assertRaises(GitHubError):
            client_create_issue(client, "o/r", "标题", "正文", "")
        self.assertEqual(host.calls, [])

    def test_forbidden(self):
        """403 时报令牌问题。"""
        host = FakeHost(
            {"/issues": {"status": 403, "json": {"message": "Bad credentials"}}}
        )
        client = GitHubClient(host)
        with self.assertRaises(GitHubError) as ctx:
            client_create_issue(client, "o/r", "标题", "正文", "ghp_bad")
        self.assertIn("Bad credentials", str(ctx.exception))


class ErrorPathTest(unittest.TestCase):
    """异常收敛。"""

    def test_network_error_wrapped(self):
        """出网异常收敛成 GitHubError。"""
        host = FakeHost()

        async def boom(*args, **kwargs):
            raise OSError("连接被重置")

        host.http_request = boom
        client = GitHubClient(host)
        with self.assertRaises(GitHubError) as ctx:
            client_list_items(client, "o/r", "")
        self.assertIn("连接被重置", str(ctx.exception))

    def test_rate_limited(self):
        """403 报限流文案。"""
        host = FakeHost(
            {
                "/issues": {
                    "status": 403,
                    "json": {"message": "API rate limit exceeded"},
                }
            }
        )
        client = GitHubClient(host)
        with self.assertRaises(GitHubError) as ctx:
            client_list_items(client, "o/r", "")
        self.assertIn("rate limit", str(ctx.exception))


# 客户端方法是协程，测试里用 asyncio.run 包一层，避免引入 pytest-asyncio。
import asyncio


def client_list_items(client, repo, token):
    """同步调用 list_items。"""
    return asyncio.run(client.list_items(repo, token))


def client_check_repo(client, repo, token):
    """同步调用 check_repo。"""
    return asyncio.run(client.check_repo(repo, token))


def client_create_issue(client, repo, title, body, token):
    """同步调用 create_issue。"""
    return asyncio.run(client.create_issue(repo, title, body, token))


if __name__ == "__main__":
    unittest.main()
