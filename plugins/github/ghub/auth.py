"""
权限判断：谁是仓库管理员、谁能动全局凭证、谁能改草稿。

规则很简单：仓库管理员管自己仓库，全局管理员管所有仓库，
草稿还允许发起人自己操作。
"""

from __future__ import annotations

import re
from typing import Any

from ghub.support import text_config


def admin_ids(host: Any) -> set[str]:
    """配置里的全局管理员 QQ 集合。"""
    raw = text_config(host, "admin_qq")
    return {item.strip() for item in re.split(r"[,，;；\s]+", raw) if item.strip()}


def is_manager(state: dict[str, Any], key: str, user_id: Any, admins: set[str]) -> bool:
    """是不是这个仓库的管理员（全局管理员视作所有仓库的管理员）。"""
    text = str(user_id or "")
    if text and text in admins:
        return True
    entry = (state.get("repos") or {}).get(key)
    return bool(
        isinstance(entry, dict) and text and text in (entry.get("managers") or [])
    )


def is_any_manager(state: dict[str, Any], user_id: Any, admins: set[str]) -> bool:
    """是不是任意一个仓库的管理员（凭证这类全局资源的最小权限门槛）。"""
    text = str(user_id or "")
    if not text:
        return False
    if text in admins:
        return True
    for key in (state.get("repos") or {}):
        if is_manager(state, key, text, admins):
            return True
    return False


def draft_permission(
    state: dict[str, Any], draft: dict[str, Any], user_id: Any, admins: set[str]
) -> bool:
    """草稿的发起人、所在仓库的管理员、全局管理员都能操作。"""
    if str(draft.get("user_id") or "") == str(user_id or ""):
        return True
    return is_manager(state, str(draft.get("repo") or ""), user_id, admins)
