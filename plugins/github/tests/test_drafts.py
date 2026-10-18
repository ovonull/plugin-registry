"""Issue 草稿状态机测试：创建、确认、撤销、过期、清理、可见范围。"""

import unittest

from ghub.drafts import (
    EXPIRE_SECONDS,
    KEEP_SECONDS,
    MAX_DRAFTS,
    STATUS_PENDING,
    cancel_draft,
    confirm_draft,
    create_draft,
    delete_draft,
    edit_draft,
    get_draft,
    list_drafts,
    refresh_drafts,
    restore_draft,
)

NOW = 1_800_000_000.0


def _new_state():
    """一份空状态。"""
    return {"version": 1, "repos": {}, "drafts": {}}


class DraftLifecycleTest(unittest.TestCase):
    """草稿的创建与状态迁移。"""

    def test_create_and_get(self):
        """创建后能按码取到，大小写不敏感。"""
        state = _new_state()
        code = create_draft(state, "suink/diana", "标题", "正文", "10001", now=NOW)
        draft = get_draft(state, code.lower())
        self.assertIsNotNone(draft)
        self.assertEqual(draft["status"], STATUS_PENDING)
        self.assertEqual(draft["title"], "标题")
        self.assertEqual(draft["user_id"], "10001")

    def test_codes_do_not_collide(self):
        """连续创建不出现重码。"""
        state = _new_state()
        codes = {
            create_draft(state, "suink/diana", f"标题{i}", "", "10001", now=NOW)
            for i in range(50)
        }
        self.assertEqual(len(codes), 50)

    def test_confirm_once(self):
        """确认后变已提交，不能重复确认。"""
        state = _new_state()
        code = create_draft(state, "suink/diana", "标题", "", "10001", now=NOW)
        draft = confirm_draft(state, code, now=NOW)
        self.assertEqual(draft["status"], "submitted")
        self.assertIsNone(confirm_draft(state, code, now=NOW))

    def test_cancel_once(self):
        """撤销后不能重复撤销。"""
        state = _new_state()
        code = create_draft(state, "suink/diana", "标题", "", "10001", now=NOW)
        draft = cancel_draft(state, code, now=NOW)
        self.assertEqual(draft["status"], "cancelled")
        self.assertIsNone(cancel_draft(state, code, now=NOW))

    def test_missing_code(self):
        """不存在的码返回 None。"""
        state = _new_state()
        self.assertIsNone(get_draft(state, "ZZZZ"))
        self.assertIsNone(confirm_draft(state, "ZZZZ"))
        self.assertIsNone(cancel_draft(state, "ZZZZ"))


class DraftRefreshTest(unittest.TestCase):
    """过期与清理。"""

    def test_pending_expires(self):
        """超过 7 天未确认的草稿变已过期。"""
        state = _new_state()
        code = create_draft(state, "suink/diana", "标题", "", "10001", now=NOW)
        refresh_drafts(state, now=NOW + EXPIRE_SECONDS + 1)
        self.assertEqual(get_draft(state, code)["status"], "expired")

    def test_pending_kept_before_expiry(self):
        """未到期的草稿保持待确认。"""
        state = _new_state()
        code = create_draft(state, "suink/diana", "标题", "", "10001", now=NOW)
        refresh_drafts(state, now=NOW + EXPIRE_SECONDS - 60)
        self.assertEqual(get_draft(state, code)["status"], STATUS_PENDING)

    def test_history_cleaned(self):
        """已提交记录超过 30 天被清掉。"""
        state = _new_state()
        code = create_draft(state, "suink/diana", "标题", "", "10001", now=NOW)
        confirm_draft(state, code, now=NOW)
        refresh_drafts(state, now=NOW + KEEP_SECONDS + 1)
        self.assertIsNone(get_draft(state, code))

    def test_expired_history_cleaned(self):
        """已过期记录同样在 30 天后清掉。"""
        state = _new_state()
        code = create_draft(state, "suink/diana", "标题", "", "10001", now=NOW)
        refresh_drafts(state, now=NOW + EXPIRE_SECONDS + 1)
        refresh_drafts(state, now=NOW + EXPIRE_SECONDS + KEEP_SECONDS + 2)
        self.assertIsNone(get_draft(state, code))


class DraftListTest(unittest.TestCase):
    """可见范围与排序。"""

    def test_filter_by_user(self):
        """给了用户号只看本人的。"""
        state = _new_state()
        create_draft(state, "suink/diana", "我的", "", "10001", now=NOW)
        create_draft(state, "suink/diana", "别人的", "", "20002", now=NOW + 1)
        mine = list_drafts(state, user_id="10001")
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["title"], "我的")

    def test_pending_first(self):
        """待确认的排在前面。"""
        state = _new_state()
        old = create_draft(state, "suink/diana", "旧的", "", "10001", now=NOW)
        create_draft(state, "suink/diana", "新的", "", "10001", now=NOW + 10)
        cancel_draft(state, old, now=NOW + 20)
        titles = [draft["title"] for draft in list_drafts(state)]
        self.assertEqual(titles, ["新的", "旧的"])

    def test_trim_removes_finished_first(self):
        """超量时先淘汰最旧的非待处理记录。"""
        state = _new_state()
        for index in range(MAX_DRAFTS):
            create_draft(state, "suink/diana", f"草稿{index}", "", "10001", now=NOW)
            confirm_draft(
                state, list(state["drafts"])[-1], now=NOW + index
            )
        # 全部提交后再加一条，触发淘汰。
        create_draft(state, "suink/diana", "最后一条", "", "10001", now=NOW + 999)
        self.assertEqual(len(state["drafts"]), MAX_DRAFTS)
        titles = [draft["title"] for draft in list_drafts(state)]
        self.assertIn("最后一条", titles)
        self.assertNotIn("草稿0", titles)


class DraftEditTest(unittest.TestCase):
    """修改、还原、删除。"""

    def test_edit_pending(self):
        """待确认草稿可以改内容。"""
        state = _new_state()
        code = create_draft(state, "suink/diana", "旧标题", "旧正文", "10001", now=NOW)
        draft = edit_draft(state, code, "新标题", "新正文", now=NOW + 5)
        self.assertEqual(draft["title"], "新标题")
        self.assertEqual(draft["body"], "新正文")
        self.assertEqual(draft["updated_at"], NOW + 5)

    def test_edit_non_pending(self):
        """已提交或已撤销的不能改。"""
        state = _new_state()
        code = create_draft(state, "suink/diana", "标题", "", "10001", now=NOW)
        confirm_draft(state, code, now=NOW)
        self.assertIsNone(edit_draft(state, code, "新标题", "", now=NOW))

    def test_restore_cancelled(self):
        """已撤销的草稿还原成新码，内容保留。"""
        state = _new_state()
        code = create_draft(state, "suink/diana", "标题", "正文", "10001", now=NOW)
        cancel_draft(state, code, now=NOW)
        result = restore_draft(state, code, now=NOW + 10)
        self.assertIsNotNone(result)
        new_code, draft = result
        self.assertNotEqual(new_code, code)
        self.assertEqual(draft["status"], STATUS_PENDING)
        self.assertEqual(draft["title"], "标题")
        self.assertEqual(draft["body"], "正文")
        self.assertIsNone(get_draft(state, code))

    def test_restore_expired(self):
        """已过期的草稿也能还原。"""
        state = _new_state()
        code = create_draft(state, "suink/diana", "标题", "", "10001", now=NOW)
        refresh_drafts(state, now=NOW + EXPIRE_SECONDS + 1)
        result = restore_draft(state, code, now=NOW + EXPIRE_SECONDS + 2)
        self.assertIsNotNone(result)
        self.assertEqual(result[1]["status"], STATUS_PENDING)

    def test_restore_submitted_refused(self):
        """已提交的草稿不能还原（Issue 已在 GitHub 上）。"""
        state = _new_state()
        code = create_draft(state, "suink/diana", "标题", "", "10001", now=NOW)
        confirm_draft(state, code, now=NOW)
        self.assertIsNone(restore_draft(state, code, now=NOW))

    def test_delete(self):
        """删除记录，重复删除返回 False。"""
        state = _new_state()
        code = create_draft(state, "suink/diana", "标题", "", "10001", now=NOW)
        self.assertTrue(delete_draft(state, code))
        self.assertFalse(delete_draft(state, code))
        self.assertIsNone(get_draft(state, code))


if __name__ == "__main__":
    unittest.main()
