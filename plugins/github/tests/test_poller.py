"""后台轮询测试：假宿主 + 临时状态文件，覆盖基线、推送、失败告警与开关。

poller 依赖宿主 SDK，测试里把仓库的 backend 目录加进 sys.path 即可导入
（app.services.plugin_sdk 只依赖标准库，不需要装任何第三方包）。
"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

# plugins/github/tests -> 上四级是仓库根目录，backend 在其下。
_BACKEND = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services.plugin_sdk import PluginCallError  # noqa: E402

from ghub.client import GitHubError  # noqa: E402
from ghub.poller import MANUAL_COOLDOWN, GitHubPoller  # noqa: E402
from ghub.store import load_state  # noqa: E402

REPO = "SuInk/Diana"
KEY = "suink/diana"


def _issue_payload(number, updated):
    """issues 端点预制数据。"""
    return {
        "number": number,
        "title": f" Issue {number} ",
        "state": "open",
        "updated_at": updated,
        "html_url": f"https://github.com/SuInk/Diana/issues/{number}",
        "body": "正文",
        "user": {"login": "someone"},
    }


class FakeHost:
    """记录日志与消息、按 URL 返回预制响应的假宿主。"""

    def __init__(self, config=None, granted=None, routes=None):
        self.config = dict(config or {})
        self.granted = list(granted or ["msg.send", "http.outbound"])
        self.routes = routes or {}
        self.logs = []
        self.calls = []
        self.group_msgs = []
        self.private_msgs = []
        self.fail_next = 0

    def has(self, capability):
        """能力判断。"""
        return capability in self.granted

    def log(self, message, level="info"):
        """记录日志。"""
        self.logs.append((level, str(message)))

    async def send_group_msg(self, group_id, message, auto_escape=False):
        """记录群消息。"""
        self.group_msgs.append((str(group_id), message))

    async def send_private_msg(self, user_id, message, auto_escape=False):
        """记录私聊消息。"""
        self.private_msgs.append((str(user_id), message))

    async def http_request(self, method, url, **kwargs):
        """按 URL 片段返回预制响应。"""
        self.calls.append({"method": method, "url": url, **kwargs})
        if self.fail_next > 0:
            self.fail_next -= 1
            raise PluginCallError("出网被拒")
        for fragment, response in self.routes.items():
            if fragment in url:
                return response
        return {"status": 404, "json": {"message": "Not Found"}}


def _routes(issue_number=1, updated="2026-10-18T01:00:00Z", stars=100):
    """四类端点与仓库统计的正常响应（统计放最后，片段匹配按顺序命中）。"""
    return {
        "/issues": {"status": 200, "json": [_issue_payload(issue_number, updated)]},
        "/pulls": {"status": 200, "json": []},
        "/commits": {"status": 200, "json": []},
        "/releases": {"status": 200, "json": []},
        "/repos/SuInk/Diana": {
            "status": 200,
            "json": {
                "full_name": "SuInk/Diana",
                "stargazers_count": stars,
                "default_branch": "main",
            },
        },
    }


def _run(coro):
    """同步跑协程。"""
    return asyncio.run(coro)


class PollerBasicsTest(unittest.TestCase):
    """基线、推送、开关与能力缺失。"""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.state_path = Path(self.folder.name) / "state-123.json"

    def _poller(self, host=None, config=None):
        """造一个不启动循环的轮询器。"""
        host = host or FakeHost(routes=_routes())
        return GitHubPoller(host, self.state_path, config or {})

    def _seed_repo(self, poller):
        """先手动往状态里加一个仓库。"""
        from ghub import store

        def mutate(state):
            entry, _ = store.repo_entry(state, KEY, REPO)
            store.add_target(entry, "group", "888999")
            return entry

        return _run(poller.mutate_state(mutate))

    def test_first_run_builds_baseline(self):
        """首个周期只建基线，不推送。"""
        host = FakeHost(routes=_routes())
        poller = self._poller(host)
        self._seed_repo(poller)
        stats = _run(poller.poll_once())
        self.assertEqual(stats["pushed"], 0)
        self.assertEqual(host.group_msgs, [])
        state = load_state(self.state_path)
        self.assertTrue(state["repos"][KEY]["baseline_done"])
        self.assertIn("issue", state["repos"][KEY]["seen"])

    def test_second_run_pushes_new_item(self):
        """第二个周期有新条目才推送，格式带北京时间。"""
        host = FakeHost(routes=_routes())
        poller = self._poller(host)
        self._seed_repo(poller)
        _run(poller.poll_once())
        # 数据里出现新 Issue #2。
        host.routes = _routes(issue_number=2, updated="2026-10-18T02:00:00Z")
        stats = _run(poller.poll_once())
        self.assertEqual(stats["pushed"], 1)
        target, message = host.group_msgs[0]
        self.assertEqual(target, "888999")
        self.assertIn("GitHub 动态: SuInk/Diana", message)
        self.assertIn("Issue #2（新建）", message)
        self.assertIn("更新于 2026-10-18 10:00:00", message)

    def test_no_new_item_no_push(self):
        """没有新动态时不推送。"""
        host = FakeHost(routes=_routes())
        poller = self._poller(host)
        self._seed_repo(poller)
        _run(poller.poll_once())
        stats = _run(poller.poll_once())
        self.assertEqual(stats["pushed"], 0)
        self.assertEqual(host.group_msgs, [])

    def test_push_disabled(self):
        """关闭推送后只更新基线，不发消息。"""
        host = FakeHost(routes=_routes(), config={"push_enabled": "0"})
        poller = self._poller(host, config={"push_enabled": "0"})
        self._seed_repo(poller)
        _run(poller.poll_once())
        host.routes = _routes(issue_number=2, updated="2026-10-18T02:00:00Z")
        stats = _run(poller.poll_once())
        self.assertEqual(stats["pushed"], 0)
        self.assertEqual(host.group_msgs, [])
        state = load_state(self.state_path)
        self.assertIn("2", state["repos"][KEY]["seen"]["issue"])

    def test_missing_capability_skips(self):
        """缺 http.outbound 时跳过轮询并只提醒一次。"""
        host = FakeHost(granted=["msg.send"], routes=_routes())
        poller = self._poller(host)
        self._seed_repo(poller)
        stats = _run(poller.poll_once())
        self.assertEqual(stats["repos"], 0)
        self.assertEqual(stats["pushed"], 0)
        _run(poller.poll_once())
        warnings = [text for level, text in host.logs if level == "warning"]
        self.assertEqual(len(warnings), 1)

    def test_push_intro_prepended(self):
        """配了推送说明就在正文前加一句。"""
        host = FakeHost(routes=_routes())
        poller = self._poller(host, config={"push_intro": "来自 UCBK 的仓库动态"})
        self._seed_repo(poller)
        _run(poller.poll_once())
        host.routes = _routes(issue_number=4, updated="2026-10-18T04:00:00Z")
        _run(poller.poll_once())
        self.assertEqual(
            host.group_msgs[0][1].split("\n")[0], "来自 UCBK 的仓库动态"
        )

    def test_refresh_updates_config(self):
        """refresh 后配置与视图换血。"""
        host = FakeHost(routes=_routes())
        poller = self._poller(host)
        _run(poller.mutate_state(lambda state: None))
        host.routes = _routes(issue_number=3, updated="2026-10-18T03:00:00Z")
        poller.refresh(host, {"check_interval": "7"})
        self.assertEqual(poller._interval_seconds(), 7 * 60)


class PollerPerRepoTest(unittest.TestCase):
    """每仓库配置：Star、监控范围、通知开关、显示名打码、周期、告警开关。"""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.state_path = Path(self.folder.name) / "state-123.json"

    def _poller(self, host=None, config=None):
        """造一个不启动循环的轮询器。"""
        host = host or FakeHost(routes=_routes())
        return GitHubPoller(host, self.state_path, config or {})

    def _seed(self, poller, **fields):
        """加一个带群推送目标的仓库，并按需覆盖字段。"""
        from ghub import store

        def mutate(state):
            entry, _ = store.repo_entry(state, KEY, REPO)
            store.add_target(entry, "group", "888999")
            entry.update(fields)
            return entry

        return _run(poller.mutate_state(mutate))

    def test_star_push(self):
        """Star 增长达到阈值就推送累计增长样式。"""
        host = FakeHost(routes=_routes(stars=100))
        poller = self._poller(host)
        self._seed(poller, watch=["star"], star_threshold=1)
        _run(poller.poll_once())
        self.assertEqual(host.group_msgs, [])
        host.routes = _routes(stars=102)
        stats = _run(poller.poll_once())
        self.assertEqual(stats["pushed"], 1)
        message = host.group_msgs[0][1]
        self.assertIn("Star 102（+2）", message)
        self.assertIn("累计新增 2 个 Star", message)
        self.assertIn("https://github.com/SuInk/Diana/stargazers", message)

    def test_star_threshold_accumulates(self):
        """未达阈值的增长先攒着，够了再一次性通知。"""
        host = FakeHost(routes=_routes(stars=100))
        poller = self._poller(host)
        self._seed(poller, watch=["star"], star_threshold=3)
        _run(poller.poll_once())
        host.routes = _routes(stars=101)
        _run(poller.poll_once())
        self.assertEqual(host.group_msgs, [])
        host.routes = _routes(stars=102)
        _run(poller.poll_once())
        self.assertEqual(host.group_msgs, [])
        host.routes = _routes(stars=103)
        _run(poller.poll_once())
        self.assertEqual(len(host.group_msgs), 1)
        self.assertIn("累计新增 3 个 Star", host.group_msgs[0][1])
        # 通知后重新攒，不会重复播报已经通知过的增长。
        host.routes = _routes(stars=104)
        _run(poller.poll_once())
        self.assertEqual(len(host.group_msgs), 1)

    def test_watch_filter_only_issue(self):
        """只监控 Issue 时不去碰其它端点。"""
        host = FakeHost(routes=_routes())
        poller = self._poller(host)
        self._seed(poller, watch=["issue"])
        _run(poller.poll_once())
        urls = [call["url"] for call in host.calls]
        self.assertEqual(len(urls), 1)
        self.assertIn("/issues", urls[0])

    def test_notify_off_updates_baseline(self):
        """仓库通知关掉后仍然拉取并更新基线，只是不发消息。"""
        host = FakeHost(routes=_routes())
        poller = self._poller(host)
        self._seed(poller, notify=False)
        _run(poller.poll_once())
        host.routes = _routes(issue_number=2, updated="2026-10-18T02:00:00Z")
        stats = _run(poller.poll_once())
        self.assertEqual(stats["pushed"], 0)
        self.assertEqual(host.group_msgs, [])
        state = load_state(self.state_path)
        self.assertIn("2", state["repos"][KEY]["seen"]["issue"])

    def test_display_name_and_mask(self):
        """显示名与打码同时生效：不输出链接，作者打码。"""
        host = FakeHost(routes=_routes())
        poller = self._poller(host)
        self._seed(poller, display_name="我的仓库", mask=True)
        _run(poller.poll_once())
        host.routes = _routes(issue_number=2, updated="2026-10-18T02:00:00Z")
        _run(poller.poll_once())
        message = host.group_msgs[0][1]
        self.assertEqual(message.split("\n")[0], "GitHub 动态: 我的仓库")
        self.assertIn("作者：so***ne", message)
        self.assertNotIn("https://github.com", message)

    def test_mask_without_display(self):
        """只打码不给显示名时，首行用「某仓库」。"""
        host = FakeHost(routes=_routes())
        poller = self._poller(host)
        self._seed(poller, mask=True)
        _run(poller.poll_once())
        host.routes = _routes(issue_number=2, updated="2026-10-18T02:00:00Z")
        _run(poller.poll_once())
        self.assertEqual(host.group_msgs[0][1].split("\n")[0], "GitHub 动态: 某仓库")

    def test_repo_interval_skip(self):
        """没到每仓库检查周期就跳过。"""
        host = FakeHost(routes=_routes())
        poller = self._poller(host)
        self._seed(poller, check_interval=3600)
        _run(poller.poll_once())
        stats = _run(poller.poll_once())
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(stats["repos"], 1)

    def test_alert_disabled(self):
        """关掉失败告警后只在日志里留痕。"""
        host = FakeHost(routes=_routes())
        poller = self._poller(host, config={"alert_enabled": "0", "fail_threshold": 1})
        self._seed(poller)
        _run(poller.poll_once())
        host.fail_next = 5
        _run(poller.poll_once())
        self.assertEqual(host.group_msgs, [])
        warnings = [text for level, text in host.logs if level == "warning"]
        self.assertTrue(any("拉取失败" in text for text in warnings))


class PollerFailureTest(unittest.TestCase):
    """失败计数与告警。"""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.state_path = Path(self.folder.name) / "state-123.json"

    def _seed(self, poller):
        from ghub import store

        def mutate(state):
            entry, _ = store.repo_entry(state, KEY, REPO)
            store.add_target(entry, "group", "888999")
            return entry

        _run(poller.mutate_state(mutate))

    def test_failure_alert_once(self):
        """连续失败达到阈值只告警一次。"""
        host = FakeHost(routes=_routes())
        poller = GitHubPoller(host, self.state_path, {"fail_threshold": 3})
        self._seed(poller)
        _run(poller.poll_once())
        host.fail_next = 10
        for _ in range(5):
            _run(poller.poll_once())
        alerts = [msg for _, msg in host.group_msgs if "拉取失败" in msg]
        self.assertEqual(len(alerts), 1)
        state = load_state(self.state_path)
        self.assertEqual(state["repos"][KEY]["failures"], 5)

    def test_recovery_resets_failures(self):
        """恢复后清零，可再次告警。"""
        host = FakeHost(routes=_routes())
        poller = GitHubPoller(host, self.state_path, {"fail_threshold": 2})
        self._seed(poller)
        _run(poller.poll_once())
        host.fail_next = 5
        _run(poller.poll_once())
        _run(poller.poll_once())
        host.fail_next = 0
        _run(poller.poll_once())
        host.fail_next = 5
        _run(poller.poll_once())
        _run(poller.poll_once())
        alerts = [msg for _, msg in host.group_msgs if "拉取失败" in msg]
        self.assertEqual(len(alerts), 2)

    def test_manual_check_cooldown(self):
        """手动检查有 30 秒冷却。"""
        host = FakeHost(routes=_routes())
        poller = GitHubPoller(host, self.state_path, {})
        self.assertTrue(poller.trigger(now=1000.0))
        self.assertFalse(poller.trigger(now=1000.0 + MANUAL_COOLDOWN - 1))
        self.assertTrue(poller.trigger(now=1000.0 + MANUAL_COOLDOWN + 1))


if __name__ == "__main__":
    unittest.main()
