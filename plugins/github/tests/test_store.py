"""状态存储测试：读写、仓库条目、推送目标、已见条目对比、失败计数。"""

import json
import tempfile
import unittest
from pathlib import Path

from ghub import store


def _issue(number, updated, state="open", kind="issue", title="标题"):
    """构造一条客户端风格的条目。"""
    return {
        "id": str(number),
        "kind": kind,
        "number": None if kind in {"commit", "release"} else number,
        "title": title,
        "author": "someone",
        "state": state,
        "updated_at": updated,
        "url": f"https://github.com/o/r/{kind}/{number}",
        "summary": "",
    }


class StateFileTest(unittest.TestCase):
    """状态文件路径与读写。"""

    def test_bot_qq_sanitized(self):
        """机器人 QQ 里的非法字符不会带进文件名。"""
        path = store.state_file("cn.ucbk.github", "123/456")
        self.assertEqual(path.name, "state-123456.json")

    def test_missing_file_gives_default(self):
        """文件不存在时返回空状态。"""
        with tempfile.TemporaryDirectory() as folder:
            state = store.load_state(Path(folder) / "none.json")
        self.assertEqual(
            state,
            {"version": store.STATE_VERSION, "repos": {}, "drafts": {}, "credentials": {}},
        )

    def test_corrupt_file_gives_default(self):
        """文件损坏时返回空状态，不抛异常。"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "broken.json"
            path.write_text("{ 这不是 JSON", encoding="utf-8")
            state = store.load_state(path)
        self.assertEqual(state["repos"], {})

    def test_roundtrip(self):
        """存进去能原样读回来。"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            state = store.default_state()
            entry, created = store.repo_entry(state, "suink/diana", "SuInk/Diana")
            entry["token"] = "ghp_test"
            store.save_state(path, state)
            loaded = store.load_state(path)
        self.assertTrue(created)
        self.assertEqual(loaded["repos"]["suink/diana"]["repo"], "SuInk/Diana")
        self.assertEqual(loaded["repos"]["suink/diana"]["token"], "ghp_test")


class RepoEntryTest(unittest.TestCase):
    """仓库条目与目标/管理员。"""

    def setUp(self):
        self.state = store.default_state()

    def test_entry_created_once(self):
        """第二次取同一仓库不重复创建。"""
        _, first = store.repo_entry(self.state, "suink/diana", "SuInk/Diana")
        _, second = store.repo_entry(self.state, "suink/diana")
        self.assertTrue(first)
        self.assertFalse(second)

    def test_display_kept(self):
        """展示名保留用户输入的大小写。"""
        entry, _ = store.repo_entry(self.state, "suink/diana", "SuInk/Diana")
        self.assertEqual(entry["repo"], "SuInk/Diana")

    def test_targets_add_remove(self):
        """推送目标可增可删，重复添加只算一次。"""
        entry, _ = store.repo_entry(self.state, "suink/diana", "SuInk/Diana")
        self.assertTrue(store.add_target(entry, "group", "123"))
        self.assertFalse(store.add_target(entry, "group", "123"))
        self.assertTrue(store.add_target(entry, "private", "456"))
        self.assertEqual(len(entry["targets"]), 2)
        self.assertTrue(store.remove_target(entry, "group", "123"))
        self.assertFalse(store.remove_target(entry, "group", "123"))
        self.assertEqual(len(entry["targets"]), 1)

    def test_target_rejects_bad_input(self):
        """非法目标类型与号码被拒。"""
        entry, _ = store.repo_entry(self.state, "suink/diana", "SuInk/Diana")
        self.assertFalse(store.add_target(entry, "channel", "123"))
        self.assertFalse(store.add_target(entry, "group", "abc"))

    def test_managers(self):
        """管理员可增可删。"""
        entry, _ = store.repo_entry(self.state, "suink/diana", "SuInk/Diana")
        self.assertTrue(store.add_manager(entry, "10001"))
        self.assertFalse(store.add_manager(entry, "10001"))
        self.assertFalse(store.add_manager(entry, "abc"))
        self.assertTrue(store.remove_manager(entry, "10001"))
        self.assertFalse(store.remove_manager(entry, "10001"))


class EntryDefaultsTest(unittest.TestCase):
    """每仓库配置字段的补齐与解析。"""

    def test_defaults_filled(self):
        """老状态缺字段时补齐默认值。"""
        entry = {"repo": "SuInk/Diana"}
        store.ensure_entry_defaults(entry)
        self.assertEqual(entry["watch"], list(store.WATCH_KINDS))
        self.assertEqual(entry["branch"], "")
        self.assertTrue(entry["notify"])
        self.assertTrue(entry["issue_enabled"])
        self.assertFalse(entry["star_baseline_done"])

    def test_watch_filtered(self):
        """非法的监控类型被过滤，重复项去重。"""
        entry = {"watch": ["issue", "乱写", "issue", "star"]}
        self.assertEqual(store.watch_of(entry), ["issue", "star"])

    def test_display_of(self):
        """显示名优先，其次仓库名。"""
        self.assertEqual(store.display_of({"display_name": "别名"}), "别名")
        self.assertEqual(store.display_of({"repo": "SuInk/Diana"}), "SuInk/Diana")
        self.assertEqual(store.display_of({}, "suink/diana"), "suink/diana")

    def test_credential_of(self):
        """凭证名优先于仓库自带令牌。"""
        state = {"credentials": {"公共": "ghp_a"}}
        self.assertEqual(
            store.credential_of({"credential": "公共", "token": "ghp_b"}, state), "ghp_a"
        )
        self.assertEqual(
            store.credential_of({"credential": "不存在", "token": "ghp_b"}, state), "ghp_b"
        )
        self.assertEqual(store.credential_of({}, state), "")


class ApplyItemsKindsTest(unittest.TestCase):
    """按类型重建已见记录，未拉取的类型要保留。"""

    def test_other_kinds_preserved(self):
        """只更新拉取过的类型，别类的历史记录不丢。"""
        state = store.default_state()
        entry, _ = store.repo_entry(state, "suink/diana", "SuInk/Diana")
        store.apply_items(
            entry,
            [_issue(1, "2026-10-18T01:00:00Z"), _issue(9, "2026-10-17T01:00:00Z", kind="release")],
            10,
        )
        self.assertIn("release", entry["seen"])
        store.apply_items(entry, [_issue(2, "2026-10-19T01:00:00Z")], 10, ["issue"])
        self.assertIn("release", entry["seen"])
        self.assertIn("2", entry["seen"]["issue"])
        self.assertNotIn("1", entry["seen"]["issue"])


class DiffItemsTest(unittest.TestCase):
    """已见条目对比。"""

    def setUp(self):
        self.state = store.default_state()
        self.entry, _ = store.repo_entry(self.state, "suink/diana", "SuInk/Diana")

    def test_first_apply_then_no_events(self):
        """刚应用的条目再比一次没有事件。"""
        items = [_issue(1, "2026-10-18T01:00:00Z"), _issue(2, "2026-10-17T01:00:00Z")]
        store.apply_items(self.entry, items, 10)
        self.assertEqual(store.diff_items(self.entry, items, 10), [])

    def test_new_item_event(self):
        """新条目算新建事件。"""
        store.apply_items(self.entry, [_issue(1, "2026-10-17T01:00:00Z")], 10)
        items = [_issue(2, "2026-10-18T01:00:00Z"), _issue(1, "2026-10-17T01:00:00Z")]
        events = store.diff_items(self.entry, items, 10)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], store.EVENT_NEW)
        self.assertEqual(events[0][1]["id"], "2")

    def test_updated_item_event(self):
        """更新时间变化算更新事件。"""
        store.apply_items(self.entry, [_issue(1, "2026-10-17T01:00:00Z")], 10)
        items = [_issue(1, "2026-10-18T01:00:00Z")]
        events = store.diff_items(self.entry, items, 10)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], store.EVENT_UPDATED)

    def test_state_change_events(self):
        """开闭状态迁移算关闭/重新打开。"""
        store.apply_items(self.entry, [_issue(1, "2026-10-17T01:00:00Z")], 10)
        closed = store.diff_items(
            self.entry, [_issue(1, "2026-10-18T01:00:00Z", state="closed")], 10
        )
        self.assertEqual(closed[0][0], store.EVENT_CLOSED)
        store.apply_items(self.entry, [_issue(1, "2026-10-18T01:00:00Z", state="closed")], 10)
        reopened = store.diff_items(
            self.entry, [_issue(1, "2026-10-19T01:00:00Z", state="open")], 10
        )
        self.assertEqual(reopened[0][0], store.EVENT_REOPENED)

    def test_merged_event(self):
        """PR 合并状态单独标记。"""
        store.apply_items(
            self.entry, [_issue(7, "2026-10-17T01:00:00Z", kind="pull")], 10
        )
        events = store.diff_items(
            self.entry,
            [_issue(7, "2026-10-18T01:00:00Z", state="merged", kind="pull")],
            10,
        )
        self.assertEqual(events[0][0], store.EVENT_MERGED)

    def test_items_beyond_limit_ignored(self):
        """超出保留条数的旧条目不产生事件。"""
        store.apply_items(
            self.entry,
            [_issue(1, "2026-10-17T01:00:00Z"), _issue(2, "2026-10-16T01:00:00Z")],
            10,
        )
        # 第 3 条本来就不在已见记录里，limit=2 时不应被当成新闻。
        items = [
            _issue(3, "2026-10-18T01:00:00Z"),
            _issue(1, "2026-10-17T01:00:00Z"),
            _issue(2, "2026-10-16T01:00:00Z"),
        ]
        events = store.diff_items(self.entry, items, 2)
        self.assertEqual([event[1]["id"] for event in events], ["3"])
        store.apply_items(self.entry, items, 2)
        # 应用之后已见记录只有前 2 条，再比一次仍然没有历史条目冒出来。
        self.assertEqual(store.diff_items(self.entry, items, 2), [])


class FailureCountTest(unittest.TestCase):
    """失败计数与告警去重。"""

    def setUp(self):
        self.state = store.default_state()
        self.entry, _ = store.repo_entry(self.state, "suink/diana", "SuInk/Diana")

    def test_alert_once_at_threshold(self):
        """达到阈值告警一次，之后不再告警。"""
        alerts = [
            store.record_failure(self.entry, 1.0, 3) for _ in range(5)
        ]
        self.assertEqual(alerts, [False, False, True, False, False])

    def test_success_resets(self):
        """成功后清零，允许再次告警。"""
        for _ in range(3):
            store.record_failure(self.entry, 1.0, 3)
        store.record_success(self.entry, 2.0)
        self.assertEqual(self.entry["failures"], 0)
        self.assertTrue(store.record_failure(self.entry, 3.0, 3) is False)
        self.assertTrue(store.record_failure(self.entry, 4.0, 3) is False)
        self.assertTrue(store.record_failure(self.entry, 5.0, 3))

    def test_zero_threshold_never_alerts(self):
        """阈值 0 表示不告警。"""
        results = [store.record_failure(self.entry, float(i), 0) for i in range(10)]
        self.assertEqual(results, [False] * 10)


if __name__ == "__main__":
    unittest.main()
