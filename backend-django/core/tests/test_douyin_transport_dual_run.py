#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DualRunDecorator 单测

覆盖：
  1. 影子编码不影响主路径返回值
  2. 影子编码失败 → 主路径仍正常
  3. send_text / send_reply 都有影子编码
  4. 影子编码会调 encode_send_message_request 并 log hex
  5. scan_inbox / start / stop 透传
  6. build_transport(dual_run=True) 工厂正确装饰
  7. send_reply 在 segments 渲染失败时不抛
"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class _FakeAccount:
    id = "acc-uuid-dual-run"


class _FakeRule:
    id = "rule-uuid-dr"
    name = "dr-rule"
    send_mode = "single"
    links = []
    template = None


class _StubInner:
    """假的 inner transport：记录所有调用 + 返回固定值。"""

    name = "stub_inner"

    def __init__(self):
        self.start_calls = 0
        self.stop_calls = 0
        self.send_text_calls: list[dict] = []
        self.send_reply_calls: list[dict] = []
        self.scan_inbox_calls = 0

    async def start(self, account):
        self.start_calls += 1

    async def stop(self, account):
        self.stop_calls += 1

    async def send_text(self, account, page, *, conversation_id, text):
        self.send_text_calls.append(
            {"account": account, "conversation_id": conversation_id, "text": text}
        )
        return "inner-msg-id"

    async def send_reply(
        self,
        account,
        page,
        *,
        conversation_id,
        trigger_message_id,
        rule,
        peer_nickname="",
    ):
        self.send_reply_calls.append(
            {
                "account": account,
                "conversation_id": conversation_id,
                "rule": rule,
                "peer_nickname": peer_nickname,
            }
        )
        return "inner-reply-log-id"

    async def scan_inbox(self, account, *, max_conversations=15, include_recent_without_unread=False):
        self.scan_inbox_calls += 1
        return []

    async def inbound_events(self):
        if False:
            yield  # pragma: no cover

    async def wait_for_inbound_signal(self, *, timeout):
        return None


class DualRunDecoratorBasicsTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_text_calls_inner_and_shadow_encodes_once(self):
        from core.douyin.runtime.transport.dual_run import DualRunDecorator

        inner = _StubInner()
        dr = DualRunDecorator(inner)

        with patch(
            "core.douyin.runtime.transport.dual_run.encode_send_message_request",
            return_value=(b"\x08\xad\x02\x10\x01\x18\x00", "cm-1", 999),
        ) as enc_mock:
            ret = await dr.send_text(
                _FakeAccount(), object(), conversation_id="conv-1", text="hello"
            )

        self.assertEqual(ret, "inner-msg-id")
        self.assertEqual(len(inner.send_text_calls), 1)
        self.assertEqual(inner.send_text_calls[0]["text"], "hello")
        # 影子编码恰好被调用一次
        enc_mock.assert_called_once()
        kwargs = enc_mock.call_args.kwargs
        self.assertEqual(kwargs["conversation_id"], "conv-1")
        self.assertEqual(kwargs["text"], "hello")

    async def test_send_text_skips_shadow_for_empty_text(self):
        """主路径会拒绝空文本；影子也不应编码（没意义、徒增日志）"""
        from core.douyin.runtime.transport.dual_run import DualRunDecorator

        inner = _StubInner()
        dr = DualRunDecorator(inner)

        with patch(
            "core.douyin.runtime.transport.dual_run.encode_send_message_request",
        ) as enc_mock:
            await dr.send_text(
                _FakeAccount(), object(), conversation_id="conv-1", text="   "
            )

        enc_mock.assert_not_called()
        # inner 仍被调用 —— DualRunDecorator 不替主路径做任何业务校验
        self.assertEqual(len(inner.send_text_calls), 1)

    async def test_shadow_encode_failure_does_not_break_main(self):
        """影子编码异常 → 仅 error log，inner 仍走完返回 inner 值"""
        from core.douyin.runtime.transport.dual_run import DualRunDecorator

        inner = _StubInner()
        dr = DualRunDecorator(inner)

        with patch(
            "core.douyin.runtime.transport.dual_run.encode_send_message_request",
            side_effect=RuntimeError("boom"),
        ):
            ret = await dr.send_text(
                _FakeAccount(), object(), conversation_id="conv-1", text="x"
            )

        self.assertEqual(ret, "inner-msg-id")
        self.assertEqual(len(inner.send_text_calls), 1)

    async def test_send_reply_shadow_encodes_each_segment(self):
        from core.douyin.runtime.transport.dual_run import DualRunDecorator

        inner = _StubInner()
        dr = DualRunDecorator(inner)

        with patch(
            "core.douyin.runtime.sender._build_segments",
            return_value=["seg-a", "seg-b", "seg-c"],
        ), patch(
            "core.douyin.runtime.transport.dual_run.encode_send_message_request",
            return_value=(b"\x00\x01", "cm-x", 1),
        ) as enc_mock:
            ret = await dr.send_reply(
                _FakeAccount(),
                object(),
                conversation_id="conv-2",
                trigger_message_id="msg-trigger",
                rule=_FakeRule(),
                peer_nickname="某人",
            )

        self.assertEqual(ret, "inner-reply-log-id")
        # 每段都编码一次
        self.assertEqual(enc_mock.call_count, 3)
        # 主路径调一次
        self.assertEqual(len(inner.send_reply_calls), 1)

    async def test_send_reply_segment_render_failure_does_not_break(self):
        """_build_segments 抛异常 → dual_run 吞掉 + inner 仍正常返回"""
        from core.douyin.runtime.transport.dual_run import DualRunDecorator

        inner = _StubInner()
        dr = DualRunDecorator(inner)

        with patch(
            "core.douyin.runtime.sender._build_segments",
            side_effect=RuntimeError("render failed"),
        ), patch(
            "core.douyin.runtime.transport.dual_run.encode_send_message_request",
        ) as enc_mock:
            ret = await dr.send_reply(
                _FakeAccount(),
                object(),
                conversation_id="conv-2",
                trigger_message_id="m",
                rule=_FakeRule(),
            )

        self.assertEqual(ret, "inner-reply-log-id")
        enc_mock.assert_not_called()
        self.assertEqual(len(inner.send_reply_calls), 1)

    async def test_scan_inbox_passthrough(self):
        from core.douyin.runtime.transport.dual_run import DualRunDecorator

        inner = _StubInner()
        dr = DualRunDecorator(inner)
        await dr.scan_inbox(_FakeAccount(), max_conversations=10)
        self.assertEqual(inner.scan_inbox_calls, 1)

    async def test_start_and_stop_passthrough(self):
        from core.douyin.runtime.transport.dual_run import DualRunDecorator

        inner = _StubInner()
        dr = DualRunDecorator(inner)
        await dr.start(_FakeAccount())
        await dr.stop(_FakeAccount())
        self.assertEqual(inner.start_calls, 1)
        self.assertEqual(inner.stop_calls, 1)

    async def test_real_encode_logs_byte_for_byte_envelope_prefix(self):
        """端到端：真实 encode + 主路径，验证 log 中的 hex 与协议地图一致"""
        from core.douyin.runtime.transport.dual_run import DualRunDecorator

        inner = _StubInner()
        dr = DualRunDecorator(inner)

        with self.assertLogs(
            "core.douyin.runtime.transport.dual_run", level="INFO"
        ) as cm:
            ret = await dr.send_text(
                _FakeAccount(),
                object(),
                conversation_id="0:1:80549827440:3061476426516824",
                text="ping",
            )

        self.assertEqual(ret, "inner-msg-id")
        # 主路径走完
        self.assertEqual(len(inner.send_text_calls), 1)
        # 至少一条 dual_run encoded log
        encoded_lines = [r for r in cm.output if "encoded" in r and "send_text" in r]
        self.assertTrue(encoded_lines, f"未找到 dual_run encoded 日志：{cm.output}")
        # envelope 起头：field 1 (cmd_id) tag = 0x08 + varint(100) = 0x08 0x64
        # 这是 sniff 报告里 send_message 真实出站流量的字节头，必须字面一致
        line = encoded_lines[0]
        self.assertIn("hex=", line)
        hex_idx = line.index("hex=")
        hex_str = line[hex_idx + len("hex="):].split()[0]
        self.assertTrue(
            hex_str.startswith("0864"),
            f"envelope 头应以 0864 开始（cmd_id=100=send_message），实际：{hex_str[:16]}",
        )


class BuildTransportDualRunFactoryTests(unittest.IsolatedAsyncioTestCase):
    """工厂 dual_run=True 时正确装饰"""

    def test_build_transport_with_dual_run_browser(self):
        from core.douyin.runtime.transport import (
            BrowserTransport,
            DualRunDecorator,
            build_transport,
        )

        t = build_transport(backend="browser", ws_inbound=False, dual_run=True)
        self.assertIsInstance(t, DualRunDecorator)
        # inner 应该是 BrowserTransport
        self.assertIsInstance(t._inner, BrowserTransport)

    def test_build_transport_with_dual_run_http_protocol(self):
        from core.douyin.runtime.transport import (
            DualRunDecorator,
            HttpProtocolTransport,
            build_transport,
        )

        t = build_transport(backend="http_protocol", ws_inbound=False, dual_run=True)
        self.assertIsInstance(t, DualRunDecorator)
        self.assertIsInstance(t._inner, HttpProtocolTransport)

    def test_build_transport_with_dual_run_and_ws_inbound(self):
        """装饰顺序：WsInboundDecorator → DualRunDecorator → BrowserTransport"""
        from core.douyin.runtime.transport import (
            BrowserTransport,
            DualRunDecorator,
            WsInboundDecorator,
            build_transport,
        )

        t = build_transport(backend="browser", ws_inbound=True, dual_run=True)
        self.assertIsInstance(t, WsInboundDecorator)
        self.assertIsInstance(t._inner, DualRunDecorator)  # type: ignore[attr-defined]
        self.assertIsInstance(t._inner._inner, BrowserTransport)  # type: ignore[attr-defined]

    def test_build_transport_default_no_dual_run(self):
        """默认不开 dual_run"""
        from core.douyin.runtime.transport import (
            BrowserTransport,
            DualRunDecorator,
            build_transport,
        )

        t = build_transport(backend="browser")
        self.assertNotIsInstance(t, DualRunDecorator)
        self.assertIsInstance(t, BrowserTransport)
