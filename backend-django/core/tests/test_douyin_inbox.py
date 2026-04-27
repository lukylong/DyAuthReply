import unittest
from unittest.mock import AsyncMock

from core.douyin.runtime.inbox import (
    _is_aggregate_conversation,
    _parse_conversation_lines,
    _scroll_conversation_list,
)


class DouyinInboxTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_conversation_lines_strips_trailing_time(self):
        nickname, preview = _parse_conversation_lines("陌生人A\n你好，在吗\n16:10")

        self.assertEqual(nickname, "陌生人A")
        self.assertEqual(preview, "你好，在吗")

    def test_aggregate_conversation_only_skipped_on_default_tabs(self):
        self.assertTrue(_is_aggregate_conversation("陌生人消息", None))
        self.assertTrue(_is_aggregate_conversation("朋友私信", "全部"))
        self.assertFalse(_is_aggregate_conversation("陌生人消息", "陌生人"))
        self.assertFalse(_is_aggregate_conversation("测试用户", None))

    async def test_scroll_conversation_list_uses_page_evaluate(self):
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value=True)

        moved = await _scroll_conversation_list(page)

        self.assertTrue(moved)
        page.evaluate.assert_awaited()
