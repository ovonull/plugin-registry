"""
Issue 草稿状态机。

「gh issue owner/repo 标题 | 正文」只创建草稿，「gh 确认 码」才真正提交到
GitHub，避免手滑把没写完的内容发出去。草稿 7 天不确认就过期，过期/已提交/
已撤销的记录再留 30 天供追溯，全程只动插件本地状态文件，不碰 GitHub。
"""

from __future__ import annotations

import secrets
import time
from typing import Any

# 去掉 0/O/1/I 等易混淆字符，4 位码足够日常并发使用。
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 4
CODE_ATTEMPTS = 20

# 待确认草稿的保质期，以及历史记录的留存期。
EXPIRE_SECONDS = 7 * 24 * 3600
KEEP_SECONDS = 30 * 24 * 3600

# 状态文件里草稿总量上限，超过时优先清理最旧的非待处理记录。
MAX_DRAFTS = 60

STATUS_PENDING = "pending"
STATUS_SUBMITTED = "submitted"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"

STATUS_TEXT = {
    STATUS_PENDING: "待确认",
    STATUS_SUBMITTED: "已提交",
    STATUS_CANCELLED: "已撤销",
    STATUS_EXPIRED: "已过期",
}


def generate_code(state: dict[str, Any]) -> str:
    """
    生成一个不冲突的草稿码。

    Args:
        state: 状态字典（草稿码不能与已有的撞）。
    Returns:
        4 位（极端情况下 6 位）大写字母数字码。
    """
    drafts = state.setdefault("drafts", {})
    for _ in range(CODE_ATTEMPTS):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if code not in drafts:
            return code
    # 碰撞太多（草稿极多时），直接加长到 6 位。
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


def create_draft(
    state: dict[str, Any],
    repo: str,
    title: str,
    body: str,
    user_id: Any,
    now: float | None = None,
) -> str:
    """
    建一条待确认草稿。

    Args:
        state: 状态字典。
        repo: 目标仓库（小写 owner/repo）。
        title: Issue 标题。
        body: Issue 正文，可为空。
        user_id: 发起人 QQ。
        now: 当前时间戳，测试时可注入。
    Returns:
        草稿码。
    """
    moment = float(now if now is not None else time.time())
    code = generate_code(state)
    state.setdefault("drafts", {})[code] = {
        "code": code,
        "repo": str(repo or ""),
        "title": str(title or ""),
        "body": str(body or ""),
        "user_id": str(user_id or ""),
        "status": STATUS_PENDING,
        "created_at": moment,
        "updated_at": moment,
    }
    _trim(state)
    return code


def get_draft(state: dict[str, Any], code: Any) -> dict[str, Any] | None:
    """
    按码取草稿。

    Args:
        state: 状态字典。
        code: 草稿码，大小写不敏感。
    Returns:
        草稿字典；不存在返回 None。
    """
    text = str(code if code is not None else "").strip().upper()
    if not text:
        return None
    draft = (state.get("drafts") or {}).get(text)
    return draft if isinstance(draft, dict) else None


def confirm_draft(
    state: dict[str, Any], code: Any, now: float | None = None
) -> dict[str, Any] | None:
    """
    把待确认草稿标记为已提交。

    Args:
        state: 状态字典。
        code: 草稿码。
        now: 当前时间戳，测试时可注入。
    Returns:
        草稿字典；草稿不存在或不是待确认状态时返回 None。
    """
    draft = get_draft(state, code)
    if draft is None or draft.get("status") != STATUS_PENDING:
        return None
    draft["status"] = STATUS_SUBMITTED
    draft["updated_at"] = float(now if now is not None else time.time())
    return draft


def cancel_draft(
    state: dict[str, Any], code: Any, now: float | None = None
) -> dict[str, Any] | None:
    """
    把待确认草稿标记为已撤销。

    Args:
        state: 状态字典。
        code: 草稿码。
        now: 当前时间戳，测试时可注入。
    Returns:
        草稿字典；草稿不存在或不是待确认状态时返回 None。
    """
    draft = get_draft(state, code)
    if draft is None or draft.get("status") != STATUS_PENDING:
        return None
    draft["status"] = STATUS_CANCELLED
    draft["updated_at"] = float(now if now is not None else time.time())
    return draft


def edit_draft(
    state: dict[str, Any],
    code: Any,
    title: str,
    body: str,
    now: float | None = None,
) -> dict[str, Any] | None:
    """
    修改待确认草稿的标题与正文。

    Args:
        state: 状态字典。
        code: 草稿码。
        title: 新标题。
        body: 新正文。
        now: 当前时间戳，测试时可注入。
    Returns:
        草稿字典；草稿不存在或不是待确认状态时返回 None。
    """
    draft = get_draft(state, code)
    if draft is None or draft.get("status") != STATUS_PENDING:
        return None
    draft["title"] = str(title or "")
    draft["body"] = str(body or "")
    draft["updated_at"] = float(now if now is not None else time.time())
    return draft


def restore_draft(
    state: dict[str, Any], code: Any, now: float | None = None
) -> tuple[str, dict[str, Any]] | None:
    """
    把已过期或已撤销的草稿还原成新的待确认草稿。

    还原会换一个新确认码：旧码可能已经被截图、被转发，留着不安全。
    已提交的草稿不能还原（Issue 已经在 GitHub 上了）。

    Args:
        state: 状态字典。
        code: 旧草稿码。
        now: 当前时间戳，测试时可注入。
    Returns:
        (新草稿码, 新草稿)；状态不允许还原时返回 None。
    """
    draft = get_draft(state, code)
    if draft is None:
        return None
    if draft.get("status") not in {STATUS_EXPIRED, STATUS_CANCELLED}:
        return None
    moment = float(now if now is not None else time.time())
    new_code = create_draft(
        state,
        str(draft.get("repo") or ""),
        str(draft.get("title") or ""),
        str(draft.get("body") or ""),
        draft.get("user_id"),
        now=moment,
    )
    (state.get("drafts") or {}).pop(str(draft.get("code")), None)
    return new_code, state["drafts"][new_code]


def delete_draft(state: dict[str, Any], code: Any) -> bool:
    """
    删除一条草稿记录。

    只删本地记录，已经写进 GitHub 的 Issue 不受影响。

    Args:
        state: 状态字典。
        code: 草稿码。
    Returns:
        True 表示删掉了，False 表示本来就不存在。
    """
    draft = get_draft(state, code)
    if draft is None:
        return False
    (state.get("drafts") or {}).pop(str(draft.get("code")), None)
    return True


def refresh_drafts(state: dict[str, Any], now: float | None = None) -> None:
    """
    过期待确认草稿，并清理留存过期的历史记录。

    Args:
        state: 状态字典。
        now: 当前时间戳，测试时可注入。
    """
    moment = float(now if now is not None else time.time())
    drafts = state.get("drafts") or {}
    for draft in drafts.values():
        if not isinstance(draft, dict):
            continue
        if draft.get("status") != STATUS_PENDING:
            continue
        created = float(draft.get("created_at") or 0)
        if moment - created > EXPIRE_SECONDS:
            draft["status"] = STATUS_EXPIRED
            draft["updated_at"] = moment
    stale = [
        code
        for code, draft in drafts.items()
        if isinstance(draft, dict)
        and draft.get("status") in {STATUS_SUBMITTED, STATUS_CANCELLED, STATUS_EXPIRED}
        and moment - float(draft.get("updated_at") or 0) > KEEP_SECONDS
    ]
    for code in stale:
        drafts.pop(code, None)


def list_drafts(
    state: dict[str, Any], user_id: Any = None
) -> list[dict[str, Any]]:
    """
    列草稿，待确认的排在前面。

    Args:
        state: 状态字典。
        user_id: 给了就只看本人的，否则看全部。
    Returns:
        草稿字典列表。
    """
    drafts = [
        draft
        for draft in (state.get("drafts") or {}).values()
        if isinstance(draft, dict)
    ]
    if user_id is not None:
        drafts = [
            draft for draft in drafts if str(draft.get("user_id")) == str(user_id)
        ]
    order = {
        STATUS_PENDING: 0,
        STATUS_SUBMITTED: 1,
        STATUS_CANCELLED: 2,
        STATUS_EXPIRED: 3,
    }
    drafts.sort(
        key=lambda draft: (
            order.get(str(draft.get("status")), 9),
            -float(draft.get("created_at") or 0),
        )
    )
    return drafts


def _trim(state: dict[str, Any]) -> None:
    """草稿超量时先淘汰最旧的非待处理记录，仍超量再淘汰最旧的待确认记录。"""
    drafts = state.get("drafts") or {}
    if len(drafts) <= MAX_DRAFTS:
        return
    finished = [
        draft
        for draft in drafts.values()
        if isinstance(draft, dict) and draft.get("status") != STATUS_PENDING
    ]
    finished.sort(key=lambda draft: float(draft.get("updated_at") or 0))
    for draft in finished:
        if len(drafts) <= MAX_DRAFTS:
            return
        drafts.pop(draft.get("code"), None)
    pending = [
        draft
        for draft in drafts.values()
        if isinstance(draft, dict) and draft.get("status") == STATUS_PENDING
    ]
    pending.sort(key=lambda draft: float(draft.get("created_at") or 0))
    for draft in pending:
        if len(drafts) <= MAX_DRAFTS:
            return
        drafts.pop(draft.get("code"), None)
