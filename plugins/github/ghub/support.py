"""
插件内的通用小工具：文本切分、权限外的会话识别、发消息与打码。

这些函数只做字符串与事件字段处理，不碰状态文件，谁都能调。
"""

from __future__ import annotations

import re
from typing import Any

# 指令前缀默认值。
DEFAULT_PREFIX = "gh"

# 调 GitHub 接口的默认超时（宿主侧上限 30 秒）。
DEFAULT_TIMEOUT = 20.0

# 草稿码：4 位大写字母数字，用户连括号一起粘过来时只取其中的码。
CODE_PATTERN = re.compile(r"[A-Za-z0-9]{4,8}")


def text_config(host: Any, key: str, default: str = "") -> str:
    """读取文本型配置，缺失时返回默认值。"""
    value = host.config.get(key)
    text = str(value).strip() if value is not None else ""
    return text or default


def split_head(rest: str) -> tuple[str, str]:
    """
    把行首第一个词和其余部分拆开，逗号/分号也算边界。

    「SuInk/Diana,123456」这种写法也能正确拆成仓库名和 QQ 号。

    Args:
        rest: 去掉指令别名后的原始文本。
    Returns:
        (行首词, 其余文本)。
    """
    text = str(rest if rest is not None else "").strip()
    matched = re.match(r"([^\s,，;；]+)[\s,，;；]*(.*)", text, re.DOTALL)
    if not matched:
        return "", ""
    return matched.group(1), matched.group(2).strip()


def mask_token(token: str) -> str:
    """令牌只露头尾，中间打码，避免截图泄露。"""
    text = str(token or "").strip()
    if len(text) <= 8:
        return "****"
    return f"{text[:4]}****{text[-4:]}"


def code_arg(raw: Any) -> str:
    """
    从用户输入里提取草稿码。

    用户经常把「KY6T（旧编号 QLTF 作废）」整段复制回来，这里只取第一段
    字母数字，避免因为多了括号就找不到草稿。

    Args:
        raw: 用户输入的码片段。
    Returns:
        规范化后的码（大写）；提取不到返回空串。
    """
    matched = CODE_PATTERN.search(str(raw if raw is not None else ""))
    return matched.group(0).upper() if matched else ""


def current_target(event: dict[str, Any]) -> tuple[str, str]:
    """当前会话的推送目标（group/private, 号码）。"""
    if event.get("message_type") == "group":
        return "group", str(event.get("group_id") or "")
    return "private", str(event.get("user_id") or "")


def chat_name(event: dict[str, Any]) -> str:
    """回复里对本会话的称呼。"""
    return "本群" if event.get("message_type") == "group" else "本私聊"


def repo_summary(state: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """所有仓库的（展示名, 条目）列表。"""
    return [
        (str(entry.get("repo") or key), entry)
        for key, entry in (state.get("repos") or {}).items()
        if isinstance(entry, dict)
    ]


async def reply(host: Any, event: dict[str, Any], message: str) -> None:
    """按会话类型把消息发回去。"""
    if event.get("message_type") == "group":
        await host.send_group_msg(event.get("group_id"), message)
    else:
        await host.send_private_msg(event.get("user_id"), message)
