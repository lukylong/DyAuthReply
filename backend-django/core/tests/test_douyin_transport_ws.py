"""Phase 2 transport.ws_decorator 单测 —— WS 帧 → 唤醒 scan_inbox。"""
import asyncio
import unittest
from unittest.mock import AsyncMock

from core.douyin.runtime.transport.base import AccountTransport
from core.douyin.runtime.transport.ws_decorator import WsInboundDecorator


def _enc_varint(v: int) -> bytes:
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _enc_field(field_id: int, wire_type: int, payload) -> bytes:
    tag = (field_id << 3) | wire_type
    out = bytearray(_enc_varint(tag))
    if wire_type == 0:
        out += _enc_varint(int(payload))
    elif wire_type == 2:
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        out += _enc_varint(len(payload)) + payload
    return bytes(out)


def _build_inbound_im_frame(text: str = "hi there") -> bytes:
    """构造一个能被 frontier 识别为 inbound IM 的帧。"""
    return (
        _enc_field(1, 2, "message_received conversation text_content")
        + _enc_field(2, 2, text)
        + _enc_field(3, 0, 1_700_000_000_000)
    )


def _build_outbound_im_frame() -> bytes:
    return _enc_field(1, 2, "send_message_request conversation text_content payload")


def _build_inbound_frame_with_conversation_json() -> bytes:
    payload = (
        '{"command_type":6,'
        '"conversation_id":"0:1:80549827440:3061476426516824",'
        '"sec_uid":"MS4wLjABAAAAabcdefgHIJKLMNopqrstuvWXyz1234567890ABCDEFGH",'
        '"text":"1"}'
    )
    return _enc_field(1, 2, payload) + _enc_field(2, 0, 1_700_000_000_123)


class _StubInner(AccountTransport):
    """最小 inner transport，只有一个 scan_inbox 计数器。"""
    name = "stub"

    def __init__(self) -> None:
        self.scan_calls = 0
        self.send_calls = 0
        self.send_text_calls = 0
        self.start_calls = 0
        self.stop_calls = 0

    async def start(self, account):
        self.start_calls += 1

    async def stop(self, account):
        self.stop_calls += 1

    async def scan_inbox(self, account, **kwargs):
        self.scan_calls += 1
        return []

    async def send_reply(self, account, page, **kwargs):
        self.send_calls += 1
        return "log-id"

    async def send_text(self, account, page, **kwargs):
        self.send_text_calls += 1
        return "msg-id"


class _FakeAccount:
    id = "acc-uuid-xxxxxxxxxxxx"


class WsInboundDecoratorTests(unittest.IsolatedAsyncioTestCase):
    """直接调用内部 _handle_frame，验证 wait_for_inbound_signal 能被 WS 帧唤醒。"""

    async def test_inbound_frame_wakes_signal(self):
        inner = _StubInner()
        deco = WsInboundDecorator(inner)
        deco._account_id = "acc-uuid"
        deco._loop = asyncio.get_running_loop()

        # 在并发任务里等待 signal，主任务模拟收到一帧
        async def waiter():
            return await deco.wait_for_inbound_signal(timeout=2.0)

        wait_task = asyncio.create_task(waiter())
        await asyncio.sleep(0.01)  # 让 waiter 进入等待
        deco._handle_frame(_build_inbound_im_frame("hello world"), url="wss://frontier")
        evt = await wait_task

        self.assertIsNotNone(evt)
        self.assertEqual(evt.source, "ws")
        self.assertEqual(evt.account_id, "acc-uuid")
        self.assertIn("conversation", evt.raw["keywords"])

    async def test_outbound_frame_does_not_wake(self):
        inner = _StubInner()
        deco = WsInboundDecorator(inner)
        deco._account_id = "acc-uuid"
        deco._loop = asyncio.get_running_loop()

        deco._handle_frame(_build_outbound_im_frame(), url="wss://frontier")
        evt = await deco.wait_for_inbound_signal(timeout=0.05)
        self.assertIsNone(evt)

    async def test_inbound_frame_prefers_conversation_id_hint(self):
        inner = _StubInner()
        deco = WsInboundDecorator(inner)
        deco._account_id = "acc-uuid"
        deco._loop = asyncio.get_running_loop()

        async def waiter():
            return await deco.wait_for_inbound_signal(timeout=2.0)

        wait_task = asyncio.create_task(waiter())
        await asyncio.sleep(0.01)
        deco._handle_frame(_build_inbound_frame_with_conversation_json(), url="wss://frontier")
        evt = await wait_task

        self.assertIsNotNone(evt)
        self.assertEqual(evt.conversation_hint, "0:1:80549827440:3061476426516824")

    async def test_debounce_collapses_burst_into_one_event(self):
        inner = _StubInner()
        deco = WsInboundDecorator(inner)
        deco._account_id = "acc-uuid"
        deco._loop = asyncio.get_running_loop()
        deco._signal_min_gap_sec = 0.5

        # 连续 5 帧 → 应只产生一个事件（防抖）
        for _ in range(5):
            deco._handle_frame(_build_inbound_im_frame(), url="wss://frontier")

        evt = await deco.wait_for_inbound_signal(timeout=0.1)
        self.assertIsNotNone(evt)
        # 队列应已被 wait 清空
        evt2 = await deco.wait_for_inbound_signal(timeout=0.05)
        self.assertIsNone(evt2)

    async def test_unknown_payload_does_not_wake(self):
        inner = _StubInner()
        deco = WsInboundDecorator(inner)
        deco._account_id = "acc-uuid"
        deco._loop = asyncio.get_running_loop()

        deco._handle_frame(b"\x00" * 32, url="wss://frontier")  # 非 protobuf
        deco._handle_frame(b"hi", url="wss://frontier")  # 太短

        evt = await deco.wait_for_inbound_signal(timeout=0.05)
        self.assertIsNone(evt)

    async def test_scan_inbox_delegates_to_inner(self):
        inner = _StubInner()
        deco = WsInboundDecorator(inner)
        deco._account_id = "acc-uuid"
        deco._loop = asyncio.get_running_loop()

        await deco.scan_inbox(_FakeAccount(), include_recent_without_unread=True)
        self.assertEqual(inner.scan_calls, 1)

    async def test_send_reply_delegates_to_inner(self):
        inner = _StubInner()
        deco = WsInboundDecorator(inner)
        deco._account_id = "acc-uuid"
        deco._loop = asyncio.get_running_loop()

        log_id = await deco.send_reply(
            _FakeAccount(),
            page=object(),
            conversation_id="c",
            trigger_message_id="m",
            rule=type("R", (), {"id": "r", "name": "r"})(),
        )
        self.assertEqual(log_id, "log-id")
        self.assertEqual(inner.send_calls, 1)

    async def test_send_text_delegates_to_inner(self):
        inner = _StubInner()
        deco = WsInboundDecorator(inner)
        deco._account_id = "acc-uuid"
        deco._loop = asyncio.get_running_loop()

        msg_id = await deco.send_text(
            _FakeAccount(),
            page=object(),
            conversation_id="c",
            text="hi",
        )
        self.assertEqual(msg_id, "msg-id")
        self.assertEqual(inner.send_text_calls, 1)


class WsInboundDecoratorAttachTests(unittest.IsolatedAsyncioTestCase):
    """验证 start() 在 BrowserManager 异常时能优雅降级（不抛）。"""

    async def test_start_survives_browser_failure(self):
        from core.douyin.runtime.transport import ws_decorator as wsd

        inner = _StubInner()
        deco = wsd.WsInboundDecorator(inner)

        async def _raise(*_a, **_kw):
            raise RuntimeError("boom")

        original = wsd.BrowserManager.get_or_create_context
        wsd.BrowserManager.get_or_create_context = AsyncMock(side_effect=_raise)
        try:
            await deco.start(_FakeAccount())  # 不应抛
        finally:
            wsd.BrowserManager.get_or_create_context = original

        # 仍能正常 stop
        await deco.stop(_FakeAccount())

    async def test_start_drives_inner_start(self):
        """关键回归：装饰器的 start() 必须触发 inner.start()，否则 HttpProtocolTransport 的 SignProvider 永远不会启动"""
        from core.douyin.runtime.transport import ws_decorator as wsd

        inner = _StubInner()
        deco = wsd.WsInboundDecorator(inner)

        # 让 BrowserManager 抛错，确保 inner.start 在 BrowserManager 调用之前执行
        async def _raise(*_a, **_kw):
            raise RuntimeError("ctx unavailable")

        original = wsd.BrowserManager.get_or_create_context
        wsd.BrowserManager.get_or_create_context = AsyncMock(side_effect=_raise)
        try:
            await deco.start(_FakeAccount())
        finally:
            wsd.BrowserManager.get_or_create_context = original

        self.assertEqual(inner.start_calls, 1)

    async def test_stop_drives_inner_stop(self):
        """对称：stop() 也要把 inner 关掉，让 SignProvider page / fallback BrowserContext 释放"""
        inner = _StubInner()
        deco = WsInboundDecorator(inner)
        deco._account_id = "acc"
        deco._loop = asyncio.get_running_loop()

        await deco.stop(_FakeAccount())
        self.assertEqual(inner.stop_calls, 1)

    async def test_start_continues_when_inner_start_raises(self):
        """inner.start 抛错时，装饰器仍要把 ws 监听挂上去（降级，不能让一个失败拖垮快路径）"""
        from core.douyin.runtime.transport import ws_decorator as wsd

        inner = _StubInner()

        async def _boom(_account):
            raise RuntimeError("signer down")
        inner.start = _boom  # type: ignore[assignment]

        deco = wsd.WsInboundDecorator(inner)
        # BrowserManager 也让它走异常分支，避免真去 launch chromium
        async def _no_ctx(*_a, **_kw):
            raise RuntimeError("no ctx in test")
        original = wsd.BrowserManager.get_or_create_context
        wsd.BrowserManager.get_or_create_context = AsyncMock(side_effect=_no_ctx)
        try:
            await deco.start(_FakeAccount())  # 不应抛
        finally:
            wsd.BrowserManager.get_or_create_context = original


if __name__ == "__main__":
    unittest.main()
