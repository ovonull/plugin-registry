"""
群聊指令解析：把「gh 添加仓库 owner/repo」这类文本变成结构化指令。

约定：
- 前缀可配置（默认 gh），前缀后加不加空格都认，如「gh帮助」「gh 帮助」；
- 英文别名要求后面是结尾或非字母数字，避免「listing」被当成「list」；
- owner/repo 校验字符集，杜绝把奇怪字符带进 URL；
- 本模块只做语法层判断，权限与副作用都在 plugin.py 里处理。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

DEFAULT_PREFIX = "gh"

# owner 与仓库名都按 GitHub 规则校验。
_REPO_PART = r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}"
REPO_PATTERN = re.compile(rf"^{_REPO_PART}/{_REPO_PART}$")

# 指令别名 -> 规范名。
COMMAND_ALIASES: dict[str, str] = {
    "帮助": "help",
    "help": "help",
    "?": "help",
    "？": "help",
    "用法": "help",
    "仓库列表": "list",
    "列表": "list",
    "监控列表": "list",
    "list": "list",
    "添加仓库": "add",
    "添加监控": "add",
    "监控": "add",
    "订阅": "add",
    "add": "add",
    "删除仓库": "remove",
    "移除仓库": "remove",
    "取消监控": "remove",
    "取消订阅": "remove",
    "remove": "remove",
    "添加管理": "add_manager",
    "删除管理": "remove_manager",
    "移除管理": "remove_manager",
    "设置令牌": "set_token",
    "配置令牌": "set_token",
    "token": "set_token",
    "配置": "show",
    "详情": "show",
    "设置": "set",
    "凭证列表": "creds",
    "凭证": "creds",
    "添加凭证": "add_cred",
    "新增凭证": "add_cred",
    "删除凭证": "remove_cred",
    "移除凭证": "remove_cred",
    "检查": "check",
    "刷新": "check",
    "立即检查": "check",
    "check": "check",
    "issue": "issue",
    "发起issue": "issue",
    "反馈": "issue",
    "确认": "confirm",
    "提交": "confirm",
    "confirm": "confirm",
    "撤销": "cancel",
    "取消": "cancel",
    "cancel": "cancel",
    "修改": "edit",
    "改草稿": "edit",
    "还原": "restore",
    "恢复": "restore",
    "删除草稿": "delete_draft",
    "删草稿": "delete_draft",
    "草稿列表": "drafts",
    "我的草稿": "drafts",
    "drafts": "drafts",
}

# 匹配时长的别名优先，保证「仓库列表」先于「列表」命中。
_SORTED_ALIASES = sorted(COMMAND_ALIASES, key=len, reverse=True)


@dataclass(frozen=True)
class Command:
    """一条解析后的指令。rest 保留别名之后的原始文本，供带正文的指令使用。"""

    name: str
    args: tuple[str, ...]
    rest: str = ""


def _match_alias(rest: str) -> tuple[str, str] | None:
    """
    在 rest 开头找指令别名。

    Args:
        rest: 去掉前缀后的文本。
    Returns:
        (规范指令名, 剩余文本)；找不到返回 None。
    """
    for alias in _SORTED_ALIASES:
        if len(rest) < len(alias):
            continue
        head = rest[: len(alias)]
        if head.lower() != alias.lower():
            continue
        body = rest[len(alias):]
        # ASCII 别名要求后面是结尾或非字母数字，避免「listing」被当成「list」。
        if alias.isascii() and body and (body[0].isalnum() or body[0] == "_"):
            continue
        return COMMAND_ALIASES[alias], body.strip()
    return None


def parse_command(text: str, prefix: str = DEFAULT_PREFIX) -> Command | None:
    """
    把消息文本解析成指令。

    Args:
        text: 消息纯文本（@ 等消息段已在调用方剥掉）。
        prefix: 指令前缀。
    Returns:
        指令对象；不是本插件的消息返回 None。
    """
    clean = str(text if text is not None else "").strip()
    tag = str(prefix if prefix is not None else "").strip() or DEFAULT_PREFIX
    if not clean or not tag:
        return None
    if not clean.lower().startswith(tag.lower()):
        return None
    rest = clean[len(tag):].strip()
    if not rest:
        # 只发了前缀，按求助处理。
        return Command("help", (), "")
    matched = _match_alias(rest)
    if matched is None:
        return None
    name, body = matched
    return Command(name, tuple(body.split()), body)


def parse_repo(raw: Any) -> str | None:
    """
    从用户输入里取 owner/repo。

    支持完整 GitHub 网址，也容忍句尾标点；非法输入返回 None。

    Args:
        raw: 用户输入，例如 SuInk/Diana 或 https://github.com/SuInk/Diana。
    Returns:
        合法的 owner/repo；不合法返回 None。
    """
    text = str(raw if raw is not None else "").strip()
    text = text.strip("。．，,、；;：:！!？?~～\"'“”（）()[]【】{}")
    if not text:
        return None
    # 贴整条网址时只取路径里的 owner/repo。
    matched = re.search(
        r"github\.com/([^/\s]+)/([^/\s?#]+)", text, re.IGNORECASE
    )
    if matched:
        text = f"{matched.group(1)}/{matched.group(2)}"
    text = text.rstrip("/")
    if text.lower().endswith(".git"):
        text = text[:-4]
    if not REPO_PATTERN.match(text):
        return None
    return text


def repo_key(repo: str) -> str:
    """
    状态文件里的仓库键。

    GitHub 对 owner/repo 大小写不敏感，统一小写做键，展示另存原文。

    Args:
        repo: owner/repo。
    Returns:
        小写仓库键。
    """
    return str(repo if repo is not None else "").strip().lower()


# ---- 每仓库设置：gh 设置 owner/repo 键=值 ... ----

# 设置项别名 -> 规范键。
SETTING_ALIASES: dict[str, str] = {
    "分支": "branch",
    "branch": "branch",
    "显示名": "display_name",
    "display": "display_name",
    "push_name": "display_name",
    "打码": "mask",
    "mask": "mask",
    "周期": "check_interval",
    "检查周期": "check_interval",
    "interval": "check_interval",
    "凭证": "credential",
    "credential": "credential",
    "监控": "watch",
    "监控内容": "watch",
    "watch": "watch",
    "通知": "notify",
    "notify": "notify",
    "issuemanage": "issue_enabled",
    "issue管理": "issue_enabled",
    "问题管理": "issue_enabled",
    "issue_enabled": "issue_enabled",
    "星标阈值": "star_threshold",
    "星标": "star_threshold",
    "star": "star_threshold",
}

# 开关类取值。
TRUE_WORDS = {"1", "on", "true", "yes", "开", "开启", "打开", "是"}
FALSE_WORDS = {"0", "off", "false", "no", "关", "关闭", "取消", "否"}

# 监控类型别名 -> 规范类型，顺序即展示顺序。
WATCH_ALIASES: dict[str, str] = {
    "issue": "issue",
    "问题": "issue",
    "工单": "issue",
    "pr": "pull",
    "pull": "pull",
    "合并请求": "pull",
    "commit": "commit",
    "提交": "commit",
    "release": "release",
    "发布": "release",
    "版本": "release",
    "star": "star",
    "星标": "star",
}
WATCH_ORDER = ("issue", "pull", "commit", "release", "star")
ALL_WORDS = {"all", "全部", "所有"}

# 分支名只允许 Git 常见字符，避免把奇怪内容拼进接口参数。
BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
# 凭证名允许中文，方便群友用「公共」「私有库」这种叫法。
CREDENTIAL_PATTERN = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5-]{1,20}$")

# 空值 / 默认词：用于「恢复跟随全局」「恢复默认分支」。
BLANK_WORDS = {"", "default", "默认", "全局", "跟随全局", "-"}

# 多条设置之间的分隔：空白，或「逗号后面紧跟一个键=」的情形（保留监控列表里的逗号）。
_SETTING_SPLIT = re.compile(r"\s+|,(?=[^,=\s]{1,20}[=：])")


def parse_settings(rest: str) -> tuple[dict[str, str], list[str]]:
    """
    把「分支=main 打码=on 监控=issue,pr」拆成 {规范键: 原始值}。

    值不做语义校验，交给 parse_bool / parse_watch 等函数，便于逐项报错。

    Args:
        rest: 去掉仓库名之后的文本。
    Returns:
        (设置字典, 无法识别的项列表)。
    """
    settings: dict[str, str] = {}
    unknown: list[str] = []
    text = str(rest if rest is not None else "").strip()
    if not text:
        return settings, unknown
    for token in _SETTING_SPLIT.split(text):
        item = token.strip("，,;；")
        if not item:
            continue
        key, sep, value = item.partition("=")
        if not sep:
            key, sep, value = item.partition("：")
        if not sep:
            unknown.append(item)
            continue
        canonical = SETTING_ALIASES.get(key.strip().lower())
        if canonical is None:
            unknown.append(key.strip())
            continue
        settings[canonical] = value.strip()
    return settings, unknown


def parse_bool(value: Any) -> bool | None:
    """
    解析开关取值。

    Args:
        value: on/off/开/关/1/0 等。
    Returns:
        True/False；无法识别返回 None。
    """
    text = str(value if value is not None else "").strip().lower()
    if text in TRUE_WORDS:
        return True
    if text in FALSE_WORDS:
        return False
    return None


def parse_watch(value: Any) -> list[str] | None:
    """
    解析监控内容。

    Args:
        value: issue,pr,commit,release,star 或其别名；all/全部 表示全选；
            空或 none/无 表示全不选。
    Returns:
        规范顺序的类型列表；含非法项返回 None。
    """
    text = str(value if value is not None else "").strip()
    if text.lower() in ALL_WORDS:
        return list(WATCH_ORDER)
    if text.lower() in {"none", "none", "无", "空"}:
        return []
    kinds: list[str] = []
    for part in re.split(r"[,，/、\s]+", text):
        item = part.strip().lower()
        if not item:
            continue
        kind = WATCH_ALIASES.get(item)
        if kind is None:
            return None
        if kind not in kinds:
            kinds.append(kind)
    kinds.sort(key=WATCH_ORDER.index)
    return kinds


def parse_seconds(value: Any) -> int | None:
    """
    解析检查周期（秒）。

    Args:
        value: 秒数；0 表示跟随全局间隔；30 到 365 天之间有效。
    Returns:
        秒数；非法返回 None。
    """
    try:
        number = int(float(str(value if value is not None else "").strip()))
    except (TypeError, ValueError):
        return None
    if number == 0:
        return 0
    if number < 30 or number > 365 * 24 * 3600:
        return None
    return number


def parse_threshold(value: Any) -> int | None:
    """
    解析 Star 增长通知阈值。

    Args:
        value: 1 到 100000。
    Returns:
        阈值；非法返回 None。
    """
    try:
        number = int(float(str(value if value is not None else "").strip()))
    except (TypeError, ValueError):
        return None
    if number < 1 or number > 100000:
        return None
    return number


def parse_branch(value: Any) -> str | None:
    """
    解析分支名。

    Args:
        value: 分支名；空或 default/默认 表示跟随默认分支。
    Returns:
        分支名（空串表示默认分支）；非法返回 None。
    """
    text = str(value if value is not None else "").strip()
    if text.lower() in BLANK_WORDS:
        return ""
    if not BRANCH_PATTERN.match(text) or ".." in text:
        return None
    return text


def parse_credential(value: Any) -> str | None:
    """
    解析凭证名。

    Args:
        value: 凭证名；空或 全局/default 表示用全局令牌。
    Returns:
        凭证名（空串表示全局）；非法返回 None。
    """
    text = str(value if value is not None else "").strip()
    if text.lower() in BLANK_WORDS:
        return ""
    if not CREDENTIAL_PATTERN.match(text):
        return None
    return text


def parse_display_name(value: Any) -> str | None:
    """
    解析推送显示名。

    Args:
        value: 显示名；空表示用仓库原名。
    Returns:
        显示名（空串表示默认）；超长返回 None。
    """
    text = " ".join(str(value if value is not None else "").split())
    if text.lower() in BLANK_WORDS:
        return ""
    if len(text) > 40:
        return None
    return text


def split_issue_args(rest: str) -> tuple[str, str] | None:
    """
    把「标题 | 正文」拆开。

    Args:
        rest: 去掉仓库名之后的文本。
    Returns:
        (标题, 正文)；标题为空返回 None。没有竖线时正文为空串。
    """
    text = str(rest if rest is not None else "").strip()
    if not text:
        return None
    for sep in ("|", "｜"):
        if sep in text:
            title, _, body = text.partition(sep)
            title = title.strip()
            if not title:
                return None
            return title[:80], body.strip()[:2000]
    return text[:80], ""


def parse_qq_list(raw: Any) -> list[str]:
    """
    把一串 QQ 号按逗号/分号/空格拆开，只留纯数字。

    Args:
        raw: 用户输入，例如「123456, 789012」。
    Returns:
        QQ 号字符串列表。
    """
    text = (
        str(raw if raw is not None else "")
        .replace("，", ",")
        .replace("；", ",")
        .replace(";", ",")
    )
    return [item for item in re.split(r"[,\s]+", text) if item.isdigit()]
