"""Phase 2 transport.browser 单测 —— 验证 BrowserTransport 是 inbox/sender 的薄封装。"""
import unittest
from unittest.mock import AsyncMock, patch

from core.douyin.runtime.transport.browser import BrowserTransport


class _FakeAccount:
    id = "acc-uuid-xxxxxxxxxxxx"


class _FakeRule:
    id = "rule-uuid-xxxxxxxxxxxx"
    name = "rule"


class BrowserTransportDelegateTests(unittest.IsolatedAsyncioTestCase):
    """委派 inbox.scan_inbox / sender.send_reply。"""

    async def test_scan_inbox_passes_through_kwargs(self):
        t = BrowserTransport()
        with patch(
            "core.douyin.runtime.transport.browser.scan_inbox",
            new=AsyncMock(return_value=["m1", "m2"]),
        ) as mocked:
            result = await t.scan_inbox(
                _FakeAccount(),
                max_conversations=7,
                include_recent_without_unread=True,
            )
            self.assertEqual(result, ["m1", "m2"])
            mocked.assert_awaited_once()
            _args, kwargs = mocked.await_args
            self.assertEqual(kwargs["max_conversations"], 7)
            self.assertTrue(kwargs["include_recent_without_unread"])

    async def test_send_reply_passes_through_args(self):
        t = BrowserTransport()
        with patch(
            "core.douyin.runtime.transport.browser.send_reply",
            new=AsyncMock(return_value="reply-log-id"),
        ) as mocked:
            page = object()
            log_id = await t.send_reply(
                _FakeAccount(),
                page,
                conversation_id="conv-id",
                trigger_message_id="msg-id",
                rule=_FakeRule(),
                peer_nickname="some-peer",
            )
            self.assertEqual(log_id, "reply-log-id")
            mocked.assert_awaited_once()
            args, kwargs = mocked.await_args
            self.assertIs(args[1], page)
            self.assertEqual(kwargs["conversation_id"], "conv-id")
            self.assertEqual(kwargs["peer_nickname"], "some-peer")

    async def test_send_text_success_writes_manual_out(self):
        """成功路径：confirm 通过 → 调 write_manual_out_message 并返回 msg id。"""
        t = BrowserTransport()
        page = object()
        with patch(
            "core.douyin.runtime.transport.browser._send_one",
            new=AsyncMock(),
        ) as send_one_mock, patch(
            "core.douyin.runtime.transport.browser.confirm_text_present_in_recent_messages",
            new=AsyncMock(return_value=True),
        ) as confirm_mock, patch(
            "core.douyin.runtime.transport.browser.write_manual_out_message",
            new=AsyncMock(return_value="msg-uuid"),
        ) as write_mock:
            msg_id = await t.send_text(
                _FakeAccount(),
                page,
                conversation_id="conv-1",
                text="  hello world  ",
            )
            self.assertEqual(msg_id, "msg-uuid")
            send_one_mock.assert_awaited_once()
            self.assertEqual(send_one_mock.await_args.args[1], "hello world")
            confirm_mock.assert_awaited_once()
            write_mock.assert_awaited_once_with("acc-uuid-xxxxxxxxxxxx", "conv-1", "hello world")

    async def test_send_text_raises_when_not_confirmed(self):
        """confirm 未通过 → 抛 RuntimeError，不写 manual_out。"""
        t = BrowserTransport()
        with patch(
            "core.douyin.runtime.transport.browser._send_one",
            new=AsyncMock(),
        ), patch(
            "core.douyin.runtime.transport.browser.confirm_text_present_in_recent_messages",
            new=AsyncMock(return_value=False),
        ), patch(
            "core.douyin.runtime.transport.browser.write_manual_out_message",
            new=AsyncMock(),
        ) as write_mock:
            with self.assertRaises(RuntimeError):
                await t.send_text(
                    _FakeAccount(),
                    object(),
                    conversation_id="conv-1",
                    text="hi",
                )
            write_mock.assert_not_awaited()

    async def test_send_text_rejects_empty_text(self):
        t = BrowserTransport()
        with self.assertRaises(ValueError):
            await t.send_text(
                _FakeAccount(),
                object(),
                conversation_id="conv-1",
                text="   ",
            )

    async def test_inbound_events_default_is_empty_stream(self):
        t = BrowserTransport()
        events = []
        gen = t.inbound_events()
        # 默认实现是空 generator，async for 应立即退出
        try:
            async for evt in gen:
                events.append(evt)
        except StopAsyncIteration:
            pass
        self.assertEqual(events, [])

    async def test_wait_for_inbound_signal_default_is_sleep(self):
        """BrowserTransport 默认 wait_for_inbound_signal 退化为 asyncio.sleep。"""
        import time
        t = BrowserTransport()
        t0 = time.monotonic()
        evt = await t.wait_for_inbound_signal(timeout=0.05)
        elapsed = time.monotonic() - t0
        self.assertIsNone(evt)
        self.assertGreaterEqual(elapsed, 0.04)


if __name__ == "__main__":
    unittest.main()
