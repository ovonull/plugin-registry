"""指令解析测试：前缀、别名、仓库名、Issue 参数、QQ 列表。"""

import unittest

from ghub.commands import (
    parse_bool,
    parse_branch,
    parse_command,
    parse_credential,
    parse_display_name,
    parse_qq_list,
    parse_repo,
    parse_seconds,
    parse_settings,
    parse_threshold,
    parse_watch,
    repo_key,
    split_issue_args,
)


class ParseCommandTest(unittest.TestCase):
    """parse_command 的前缀与别名匹配。"""

    def test_prefix_with_space(self):
        """前缀后有空格也能识别。"""
        command = parse_command("gh 帮助")
        self.assertIsNotNone(command)
        self.assertEqual(command.name, "help")

    def test_prefix_without_space(self):
        """前缀后紧跟中文指令也能识别。"""
        command = parse_command("gh帮助")
        self.assertIsNotNone(command)
        self.assertEqual(command.name, "help")

    def test_prefix_case_insensitive(self):
        """英文前缀与英文指令都大小写不敏感。"""
        command = parse_command("GH list")
        self.assertIsNotNone(command)
        self.assertEqual(command.name, "list")

    def test_bare_prefix_is_help(self):
        """只发前缀按求助处理。"""
        command = parse_command("gh")
        self.assertIsNotNone(command)
        self.assertEqual(command.name, "help")

    def test_question_mark_is_help(self):
        """问号别名也算帮助。"""
        command = parse_command("gh ？")
        self.assertIsNotNone(command)
        self.assertEqual(command.name, "help")

    def test_other_message_returns_none(self):
        """普通消息不解析成指令。"""
        self.assertIsNone(parse_command("今天天气怎么样"))
        self.assertIsNone(parse_command("github.com 上的动态"))
        self.assertIsNone(parse_command("ghtest"))

    def test_listing_is_not_list(self):
        """listing 不能被当成 list。"""
        self.assertIsNone(parse_command("gh listing"))
        command = parse_command("gh list")
        self.assertEqual(command.name, "list")

    def test_long_alias_first(self):
        """长别名优先，仓库列表不会命中列表。"""
        command = parse_command("gh 仓库列表")
        self.assertEqual(command.name, "list")
        command = parse_command("gh 监控列表")
        self.assertEqual(command.name, "list")

    def test_add_repo_args(self):
        """添加仓库带出仓库名参数。"""
        command = parse_command("gh 添加仓库 SuInk/Diana")
        self.assertEqual(command.name, "add")
        self.assertEqual(command.args, ("SuInk/Diana",))

    def test_issue_rest_kept(self):
        """issue 指令保留竖线原文。"""
        command = parse_command("gh issue SuInk/Diana 标题 | 正文")
        self.assertEqual(command.name, "issue")
        self.assertEqual(command.rest, "SuInk/Diana 标题 | 正文")

    def test_issue_chinese_alias(self):
        """发起issue 也是 issue 指令。"""
        command = parse_command("gh 发起issue o/r 标题")
        self.assertEqual(command.name, "issue")

    def test_custom_prefix(self):
        """自定义前缀生效。"""
        self.assertIsNone(parse_command("gh 帮助", prefix="git"))
        command = parse_command("git 帮助", prefix="git")
        self.assertEqual(command.name, "help")


class ParseRepoTest(unittest.TestCase):
    """parse_repo 的归一化与校验。"""

    def test_plain(self):
        """普通 owner/repo 原样通过。"""
        self.assertEqual(parse_repo("SuInk/Diana"), "SuInk/Diana")

    def test_url(self):
        """整条 GitHub 网址只取 owner/repo。"""
        self.assertEqual(
            parse_repo("https://github.com/SuInk/Diana"), "SuInk/Diana"
        )
        self.assertEqual(
            parse_repo("https://github.com/SuInk/Diana/issues/1116"),
            "SuInk/Diana",
        )

    def test_git_suffix_and_trailing_punctuation(self):
        """.git 后缀与句尾标点都被剥掉。"""
        self.assertEqual(parse_repo("SuInk/Diana.git"), "SuInk/Diana")
        self.assertEqual(parse_repo("SuInk/Diana。"), "SuInk/Diana")

    def test_invalid(self):
        """非法输入返回 None。"""
        self.assertIsNone(parse_repo("Diana"))
        self.assertIsNone(parse_repo("SuInk/Diana/extra"))
        self.assertIsNone(parse_repo("Su Ink/Diana"))
        self.assertIsNone(parse_repo(""))
        self.assertIsNone(parse_repo("-bad/name"))

    def test_repo_key_lowercase(self):
        """状态键统一小写。"""
        self.assertEqual(repo_key(" SuInk/Diana "), "suink/diana")


class SplitIssueArgsTest(unittest.TestCase):
    """split_issue_args 的标题正文拆分。"""

    def test_with_body(self):
        """有竖线时拆成标题与正文。"""
        title, body = split_issue_args("标题 | 第一行\n第二行")
        self.assertEqual(title, "标题")
        self.assertEqual(body, "第一行\n第二行")

    def test_full_width_separator(self):
        """全角竖线也认。"""
        title, body = split_issue_args("标题｜正文")
        self.assertEqual(title, "标题")
        self.assertEqual(body, "正文")

    def test_without_body(self):
        """没有竖线时正文为空。"""
        self.assertEqual(split_issue_args("只有标题"), ("只有标题", ""))

    def test_empty_title(self):
        """标题为空返回 None。"""
        self.assertIsNone(split_issue_args(" | 正文"))
        self.assertIsNone(split_issue_args(""))


class ParseQQListTest(unittest.TestCase):
    """parse_qq_list 的拆分与过滤。"""

    def test_mixed_separators(self):
        """中英文逗号、分号、空格都能拆。"""
        self.assertEqual(
            parse_qq_list("123，456;789 012"), ["123", "456", "789", "012"]
        )

    def test_non_digit_filtered(self):
        """非纯数字项被丢掉。"""
        self.assertEqual(parse_qq_list("123, abc, 45x"), ["123"])


class ParseSettingsTest(unittest.TestCase):
    """gh 设置 的键值解析。"""

    def test_space_separated(self):
        """空格分隔多个设置。"""
        settings, unknown = parse_settings("分支=main 打码=on")
        self.assertEqual(settings, {"branch": "main", "mask": "on"})
        self.assertEqual(unknown, [])

    def test_watch_list_with_commas(self):
        """监控列表里的逗号属于值，不能被拆成两条设置。"""
        settings, unknown = parse_settings("监控=issue,pr,commit")
        self.assertEqual(settings, {"watch": "issue,pr,commit"})
        self.assertEqual(unknown, [])

    def test_comma_between_settings(self):
        """逗号后面紧跟「键=」时当分隔符。"""
        settings, _ = parse_settings("分支=main,打码=off")
        self.assertEqual(settings, {"branch": "main", "mask": "off"})

    def test_full_width_colon(self):
        """全角冒号也认。"""
        settings, _ = parse_settings("分支：dev")
        self.assertEqual(settings, {"branch": "dev"})

    def test_unknown_key(self):
        """不认识的键单独返回。"""
        settings, unknown = parse_settings("颜色=red 分支=main")
        self.assertEqual(settings, {"branch": "main"})
        self.assertEqual(unknown, ["颜色"])

    def test_missing_equals(self):
        """没有等号的词进 unknown。"""
        _, unknown = parse_settings("随便说说")
        self.assertEqual(unknown, ["随便说说"])


class SettingValueTest(unittest.TestCase):
    """设置值的语义校验。"""

    def test_bool(self):
        """开关词。"""
        self.assertIs(parse_bool("on"), True)
        self.assertIs(parse_bool("关"), False)
        self.assertIsNone(parse_bool("也许"))

    def test_watch(self):
        """监控类型与全选/全不选。"""
        self.assertEqual(parse_watch("pr,issue"), ["issue", "pull"])
        self.assertEqual(parse_watch("全部"), ["issue", "pull", "commit", "release", "star"])
        self.assertEqual(parse_watch("无"), [])
        self.assertIsNone(parse_watch("issue,乱写"))

    def test_seconds(self):
        """周期只接受 0 或 30 秒以上。"""
        self.assertEqual(parse_seconds("0"), 0)
        self.assertEqual(parse_seconds("3600"), 3600)
        self.assertIsNone(parse_seconds("10"))
        self.assertIsNone(parse_seconds("abc"))
        self.assertIsNone(parse_seconds(str(366 * 24 * 3600)))

    def test_threshold(self):
        """Star 阈值 1 起。"""
        self.assertEqual(parse_threshold("5"), 5)
        self.assertIsNone(parse_threshold("0"))

    def test_branch(self):
        """分支名合法性与默认值。"""
        self.assertEqual(parse_branch("main"), "main")
        self.assertEqual(parse_branch("feature/x"), "feature/x")
        self.assertEqual(parse_branch("默认"), "")
        self.assertIsNone(parse_branch("bad branch"))
        self.assertIsNone(parse_branch("a..b"))

    def test_credential_and_display(self):
        """凭证名与显示名。"""
        self.assertEqual(parse_credential("公共"), "公共")
        self.assertEqual(parse_credential("全局"), "")
        self.assertIsNone(parse_credential("带 空格"))
        self.assertEqual(parse_display_name(" 我的  仓库 "), "我的 仓库")
        self.assertIsNone(parse_display_name("长" * 41))


if __name__ == "__main__":
    unittest.main()
