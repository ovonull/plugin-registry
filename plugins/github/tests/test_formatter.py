"""消息格式化测试：北京时间换算、单行压缩、Diana 样式拼装。"""

import unittest

from ghub.formatter import beijing_time, clip, format_item, format_star, mask_name


def _issue(**overrides):
    """一条归一化后的 Issue，可按字段覆盖。"""
    item = {
        "id": "1116",
        "kind": "issue",
        "number": 1116,
        "title": "fix(rss): 配图丢失",
        "author": "c837835323",
        "state": "open",
        "updated_at": "2026-10-18T01:17:16Z",
        "url": "https://github.com/SuInk/Diana/issues/1116",
        "summary": "",
    }
    item.update(overrides)
    return item


class BeijingTimeTest(unittest.TestCase):
    """UTC 转北京时间。"""

    def test_z_suffix(self):
        """带 Z 的 UTC 时间加 8 小时。"""
        self.assertEqual(
            beijing_time("2026-10-18T01:17:16Z"), "2026-10-18 09:17:16"
        )

    def test_cross_day(self):
        """跨天时日期也一起变。"""
        self.assertEqual(
            beijing_time("2026-10-18T20:00:00Z"), "2026-10-19 04:00:00"
        )

    def test_offset_form(self):
        """显式时区偏移也认。"""
        self.assertEqual(
            beijing_time("2026-10-18T09:17:16+08:00"), "2026-10-18 09:17:16"
        )

    def test_invalid_passthrough(self):
        """解析不了就原样截断返回。"""
        self.assertEqual(beijing_time("not-a-time"), "not-a-time")
        self.assertEqual(beijing_time(""), "未知时间")
        self.assertEqual(beijing_time(None), "未知时间")


class ClipTest(unittest.TestCase):
    """单行压缩。"""

    def test_collapse_whitespace(self):
        """换行与连续空白压成单个空格。"""
        self.assertEqual(clip("第一行\n  第二行\t第三行"), "第一行 第二行 第三行")

    def test_truncate(self):
        """超长截断并加省略号。"""
        self.assertEqual(clip("一二三四五", 4), "一二三…")

    def test_keep_within_limit(self):
        """不超长时原样返回。"""
        self.assertEqual(clip("一二三", 4), "一二三")


class FormatItemTest(unittest.TestCase):
    """推送消息拼装。"""

    def test_issue_updated(self):
        """更新中的 Issue 与 Diana 的推送样式一致。"""
        text = format_item("SuInk/Diana", _issue(), "updated")
        lines = text.split("\n")
        self.assertEqual(lines[0], "GitHub 动态: SuInk/Diana")
        self.assertEqual(lines[1], "Issue #1116（更新）")
        self.assertEqual(lines[2], "fix(rss): 配图丢失")
        self.assertEqual(lines[3], "作者：c837835323")
        self.assertEqual(lines[4], "更新于 2026-10-18 09:17:16")
        self.assertEqual(lines[5], "https://github.com/SuInk/Diana/issues/1116")

    def test_issue_new_and_closed(self):
        """新建与关闭的事件文案。"""
        self.assertIn("Issue #1116（新建）", format_item("o/r", _issue(), "new"))
        self.assertIn(
            "Issue #1116（关闭）",
            format_item("o/r", _issue(state="closed"), "closed"),
        )

    def test_pull_merged(self):
        """PR 合并的文案。"""
        item = _issue(kind="pull", state="merged", number=34)
        self.assertIn("PR #34（合并）", format_item("o/r", item, "merged"))

    def test_commit(self):
        """提交显示 7 位短哈希与「推送」。"""
        item = _issue(
            kind="commit",
            number=None,
            id="1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d",
            title="fix: 修一个 bug",
            author="c837835323",
        )
        text = format_item("o/r", item, "new")
        self.assertIn("Commit 1a2b3c4（推送）", text)
        self.assertIn("作者：c837835323", text)

    def test_release(self):
        """Release 显示标签号与「发布」。"""
        item = _issue(
            kind="release",
            number=None,
            id="99",
            title="v0.8.151",
            tag="v0.8.151",
        )
        text = format_item("o/r", item, "new")
        self.assertIn("Release v0.8.151（发布）", text)

    def test_long_title_clipped(self):
        """超长标题被压成单行并截断。"""
        item = _issue(title="很长" * 40)
        line = format_item("o/r", item, "new").split("\n")[2]
        self.assertTrue(line.endswith("…"))
        self.assertLessEqual(len(line), 70)

    def test_missing_title(self):
        """没有标题时给占位。"""
        item = _issue(title="")
        self.assertIn("（无标题）", format_item("o/r", item, "new"))


class MaskAndDisplayTest(unittest.TestCase):
    """显示名、打码与 Star 推送。"""

    def test_display_name(self):
        """显示名替换首行的仓库名。"""
        text = format_item("o/r", _issue(), "new", "我的仓库")
        self.assertEqual(text.split("\n")[0], "GitHub 动态: 我的仓库")

    def test_mask_hides_url_and_author(self):
        """打码后不输出链接，作者只留首尾。"""
        text = format_item("o/r", _issue(), "new", "", True)
        self.assertNotIn("https://github.com", text)
        self.assertIn("作者：c8***23", text)

    def test_mask_without_display(self):
        """打码且无显示名时首行用「某仓库」。"""
        text = format_item("o/r", _issue(), "new", "", True)
        self.assertEqual(text.split("\n")[0], "GitHub 动态: 某仓库")

    def test_star_message(self):
        """Star 累计增长样式。"""
        text = format_star("SuInk/Diana", 102, 3)
        lines = text.split("\n")
        self.assertEqual(lines[0], "GitHub 动态: SuInk/Diana")
        self.assertEqual(lines[1], "Star 102（+3）")
        self.assertEqual(lines[2], "累计新增 3 个 Star，当前共 102 个")
        self.assertEqual(lines[3], "https://github.com/SuInk/Diana/stargazers")

    def test_star_masked(self):
        """打码的 Star 消息不带链接。"""
        text = format_star("SuInk/Diana", 102, 3, "我的仓库", True)
        self.assertIn("GitHub 动态: 我的仓库", text)
        self.assertNotIn("https://github.com", text)

    def test_mask_name(self):
        """打码规则：短名只留一个字。"""
        self.assertEqual(mask_name("abc"), "a***")
        self.assertEqual(mask_name("abcdef"), "ab***ef")
        self.assertEqual(mask_name(""), "***")


if __name__ == "__main__":
    unittest.main()
