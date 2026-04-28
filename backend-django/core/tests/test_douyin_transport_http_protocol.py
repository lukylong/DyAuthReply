"""
Phase 3 transport.http_protocol 单测

验证：
  1. 默认所有 verb 走 fallback（没启用 _http_*_enabled）
  2. 启用 verb 但协议路径 raise _NotImplementedYet → 仍 fallback（灰度安全）
  3. 启用 verb 且协议路径成功 → 不调 fallback
  4. signer 不健康（is_ready=False）→ 直接 fallback
  5. signer 失败计数到阈值 → 之后所有 verb 都 fallback
  6. start/stop 同时驱动 SignProvider 和 fallback
"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from core.douyin.runtime.transport.base import AccountTransport
from core.douyin.runtime.transport.http_protocol import (
    HttpProtocolTransport,
    _NotImplementedYet,
)
from core.douyin.runtime.transport.sign_provider import SignerUnavailable


class _FakeAccount:
    id = "acc-uuid-http-proto"


class _FakeRule:
    id = "rule-uuid-http-proto"
    name = "rule"


class _StubFallback(AccountTransport):
    """记录每个 verb 调用次数；返回值哨兵，便于断言"""

    def __init__(self):
        self.start_calls = 0
        self.stop_calls = 0
        self.scan_calls = 0
        self.send_reply_calls = 0
        self.send_text_calls = 0

    async def start(self, account):
        self.start_calls += 1

    async def stop(self, account):
        self.stop_calls += 1

    async def scan_inbox(self, account, **kwargs):
        self.scan_calls += 1
        return ["fallback-msg"]

    async def send_reply(self, account, page, **kwargs):
        self.send_reply_calls += 1
        return "fallback-reply-id"

    async def send_text(self, account, page, **kwargs):
        self.send_text_calls += 1
        return "fallback-text-id"


class _StubSign:
    """最小 SignProvider 替身：可控 is_ready、start/stop 计数"""

    def __init__(self, *, ready=True):
        self._ready = ready
        self.start_calls = 0
        self.stop_calls = 0

    @property
    def is_ready(self):
        return self._ready

    async def start(self, account):
        self.start_calls += 1

    async def stop(self, account):
        self.stop_calls += 1

    def kill(self):
        self._ready = False


class HttpProtocolTransportFallbackTests(unittest.IsolatedAsyncioTestCase):
    """默认所有 verb fallback；保证灰度切换零回归"""

    def _make(self, **kwargs):
        fb = _StubFallback()
        sg = _StubSign(ready=True)
        t = HttpProtocolTransport(sign_provider=sg, fallback=fb, **kwargs)
        return t, fb, sg

    async def test_scan_inbox_default_fallbacks(self):
        t, fb, _ = self._make()
        result = await t.scan_inbox(_FakeAccount(), max_conversations=5)
        self.assertEqual(result, ["fallback-msg"])
        self.assertEqual(fb.scan_calls, 1)

    async def test_scan_inbox_strict_raises_when_signer_not_ready(self):
        fb = _StubFallback()
        sg = _StubSign(ready=False)
        t = HttpProtocolTransport(sign_provider=sg, fallback=fb, scan_inbox_enabled=True)
        t._http_scan_strict = True

        with self.assertRaisesRegex(RuntimeError, "scan_inbox 严格模式失败"):
            await t.scan_inbox(_FakeAccount(), max_conversations=5)
        self.assertEqual(fb.scan_calls, 0)

    async def test_send_reply_default_fallbacks(self):
        t, fb, _ = self._make()
        log_id = await t.send_reply(
            _FakeAccount(),
            object(),
            conversation_id="c",
            trigger_message_id="m",
            rule=_FakeRule(),
            peer_nickname="x",
        )
        self.assertEqual(log_id, "fallback-reply-id")
        self.assertEqual(fb.send_reply_calls, 1)

    async def test_send_text_default_fallbacks(self):
        t, fb, _ = self._make()
        msg_id = await t.send_text(
            _FakeAccount(),
            object(),
            conversation_id="c",
            text="hi",
        )
        self.assertEqual(msg_id, "fallback-text-id")
        self.assertEqual(fb.send_text_calls, 1)

    async def test_start_drives_both_sign_and_fallback(self):
        t, fb, sg = self._make()
        await t.start(_FakeAccount())
        self.assertEqual(sg.start_calls, 1)
        self.assertEqual(fb.start_calls, 1)

    async def test_stop_drives_both_sign_and_fallback(self):
        t, fb, sg = self._make()
        await t.stop(_FakeAccount())
        self.assertEqual(sg.stop_calls, 1)
        self.assertEqual(fb.stop_calls, 1)


class HttpProtocolTransportEnabledVerbTests(unittest.IsolatedAsyncioTestCase):
    """启用 verb 后协议路径可用/不可用的两种走法"""

    async def test_send_text_not_implemented_falls_back(self):
        """开了 _http_send_text_enabled，但 _impl 抛 _NotImplementedYet → fallback"""
        fb = _StubFallback()
        t = HttpProtocolTransport(sign_provider=_StubSign(), fallback=fb)
        t._http_send_text_enabled = True

        msg_id = await t.send_text(
            _FakeAccount(),
            object(),
            conversation_id="c",
            text="hi",
        )
        self.assertEqual(msg_id, "fallback-text-id")
        self.assertEqual(fb.send_text_calls, 1)

    async def test_send_text_protocol_success_skips_fallback(self):
        """协议路径成功 → 不走 fallback"""
        fb = _StubFallback()
        t = HttpProtocolTransport(sign_provider=_StubSign(), fallback=fb)
        t._http_send_text_enabled = True
        t._impl_send_text_via_http = AsyncMock(return_value="proto-msg-id")

        msg_id = await t.send_text(
            _FakeAccount(),
            object(),
            conversation_id="c",
            text="hi",
        )
        self.assertEqual(msg_id, "proto-msg-id")
        self.assertEqual(fb.send_text_calls, 0)
        t._impl_send_text_via_http.assert_awaited_once()

    async def test_send_text_signer_unavailable_falls_back_and_counts(self):
        """协议路径抛 SignerUnavailable → fallback；失败计数 +1"""
        fb = _StubFallback()
        t = HttpProtocolTransport(sign_provider=_StubSign(), fallback=fb)
        t._http_send_text_enabled = True
        t._impl_send_text_via_http = AsyncMock(side_effect=SignerUnavailable("boom"))

        msg_id = await t.send_text(
            _FakeAccount(),
            object(),
            conversation_id="c",
            text="hi",
        )
        self.assertEqual(msg_id, "fallback-text-id")
        self.assertEqual(fb.send_text_calls, 1)
        self.assertEqual(t._signer_failures, 1)

    async def test_signer_not_ready_immediately_falls_back(self):
        """signer.is_ready=False → 直接 fallback，不调 _impl"""
        fb = _StubFallback()
        sg = _StubSign(ready=False)
        t = HttpProtocolTransport(sign_provider=sg, fallback=fb)
        t._http_send_text_enabled = True
        t._impl_send_text_via_http = AsyncMock()

        await t.send_text(
            _FakeAccount(),
            object(),
            conversation_id="c",
            text="hi",
        )
        t._impl_send_text_via_http.assert_not_awaited()
        self.assertEqual(fb.send_text_calls, 1)


class HttpProtocolTransportFailureBudgetTests(unittest.IsolatedAsyncioTestCase):
    """连续 signer 失败到阈值后，所有 verb 都进降级模式"""

    async def test_failure_streak_disables_protocol_path(self):
        fb = _StubFallback()
        t = HttpProtocolTransport(
            sign_provider=_StubSign(),
            fallback=fb,
            max_signer_failures=2,
        )
        t._http_send_text_enabled = True
        t._impl_send_text_via_http = AsyncMock(side_effect=SignerUnavailable("x"))

        # 前两次：协议路径被尝试，失败 → fallback；计数累积
        await t.send_text(_FakeAccount(), object(), conversation_id="c", text="1")
        await t.send_text(_FakeAccount(), object(), conversation_id="c", text="2")
        self.assertEqual(t._signer_failures, 2)
        self.assertEqual(t._impl_send_text_via_http.await_count, 2)

        # 第三次：阈值到了 → _signer_healthy=False，连 _impl 都不应该再被调
        await t.send_text(_FakeAccount(), object(), conversation_id="c", text="3")
        self.assertEqual(t._impl_send_text_via_http.await_count, 2)  # 仍是 2，没增加
        self.assertEqual(fb.send_text_calls, 3)


class HttpProtocolTransportFactoryTests(unittest.IsolatedAsyncioTestCase):
    """build_transport 工厂"""

    def test_build_transport_browser_default(self):
        from core.douyin.runtime.transport import (
            BrowserTransport,
            build_transport,
            TRANSPORT_BACKEND_BROWSER,
        )

        t = build_transport(backend=TRANSPORT_BACKEND_BROWSER, ws_inbound=False)
        self.assertIsInstance(t, BrowserTransport)

    def test_build_transport_http_protocol(self):
        from core.douyin.runtime.transport import (
            HttpProtocolTransport,
            build_transport,
            TRANSPORT_BACKEND_HTTP_PROTOCOL,
        )

        t = build_transport(backend=TRANSPORT_BACKEND_HTTP_PROTOCOL, ws_inbound=False)
        self.assertIsInstance(t, HttpProtocolTransport)

    def test_build_transport_unknown_backend_falls_back_to_browser(self):
        from core.douyin.runtime.transport import (
            BrowserTransport,
            build_transport,
        )

        t = build_transport(backend="some-future-backend", ws_inbound=False)
        self.assertIsInstance(t, BrowserTransport)

    def test_build_transport_ws_inbound_decorates_http_protocol(self):
        from core.douyin.runtime.transport import (
            HttpProtocolTransport,
            WsInboundDecorator,
            build_transport,
            TRANSPORT_BACKEND_HTTP_PROTOCOL,
        )

        t = build_transport(backend=TRANSPORT_BACKEND_HTTP_PROTOCOL, ws_inbound=True)
        self.assertIsInstance(t, WsInboundDecorator)
        # decorator 内部的 base 应该是 HttpProtocolTransport
        self.assertIsInstance(t._inner, HttpProtocolTransport)


class HttpProtocolSendTextImplTests(unittest.IsolatedAsyncioTestCase):
    """_impl_send_text_via_http 真实路径覆盖（不 mock 内部，只 mock signed_fetch + DB）"""

    def _make_sign_provider(self, *, signed_fetch_mock):
        """构造一个最小可用的 SignProvider 替身：is_ready=True、signed_fetch 走 mock"""
        sp = _StubSign(ready=True)
        sp.signed_fetch = signed_fetch_mock
        return sp

    def _ok_response_bytes(self, *, client_msg_id: str, server_msg_id: int = 0xABCDEF) -> bytes:
        """构造一个合法的 send_message 成功响应 protobuf body"""
        from core.douyin.runtime.transport.wire.codec import (
            encode_field,
            encode_tag,
            encode_varint,
            WIRE_LEN,
        )

        inner = (
            encode_field(1, server_msg_id)
            + encode_field(3, 0)
            + encode_field(4, client_msg_id)
        )
        wrapped = encode_tag(100, WIRE_LEN) + encode_varint(len(inner)) + inner
        full = (
            encode_field(1, 100)
            + encode_field(2, 12345)
            + encode_field(3, 0)        # status_code = 0
            + encode_field(4, "OK")     # status_msg
            + encode_field(5, 0)
            + encode_tag(6, WIRE_LEN) + encode_varint(len(wrapped)) + wrapped
        )
        return full

    async def test_impl_send_text_success_writes_to_db(self):
        """成功路径：signed_fetch 返回合法响应 → 调 write_manual_out_message → 返回 msg_id"""
        from unittest.mock import patch
        from core.douyin.runtime.transport.sign_provider import SignedResponse

        # 用一个挂钩抓我们发出去的 client_msg_id，回响应里 echo 它
        sent_body_holder = {}

        async def fake_signed_fetch(method, url, *, body, headers, timeout_ms=None):
            sent_body_holder["method"] = method
            sent_body_holder["url"] = url
            sent_body_holder["body"] = body
            sent_body_holder["headers"] = headers
            # 解码我们发出的 body 拿到 client_msg_id
            from core.douyin.runtime.transport.wire.codec import iter_fields
            envelope = {}
            for fnum, _w, val in iter_fields(body):
                envelope.setdefault(fnum, []).append(val)
            inner_field_8 = envelope[8][0]  # bytes
            wrap = {}
            for fnum, _w, val in iter_fields(inner_field_8):
                wrap.setdefault(fnum, []).append(val)
            send_payload = wrap[100][0]
            sp = {}
            for fnum, _w, val in iter_fields(send_payload):
                sp.setdefault(fnum, []).append(val)
            client_msg_id = sp[5][0].decode("utf-8")
            return SignedResponse(
                status=200,
                url=url,
                headers={"content-type": "application/x-protobuf"},
                text="",
                content=self._ok_response_bytes(client_msg_id=client_msg_id),
            )

        sp = self._make_sign_provider(signed_fetch_mock=fake_signed_fetch)
        fb = _StubFallback()
        t = HttpProtocolTransport(sign_provider=sp, fallback=fb, send_text_enabled=True)

        with patch(
            "core.douyin.runtime.transport.http_protocol._resolve_conversation_transport_keys",
            new=AsyncMock(return_value=(
                "conv-db-1",
                "0:1:80549827440:3061476426516824",
            )),
        ), patch(
            "core.douyin.runtime.transport.http_protocol.write_manual_out_message",
            new_callable=AsyncMock,
            return_value="msg-uuid-from-db",
        ) as mock_write:
            msg_id = await t.send_text(
                _FakeAccount(),
                object(),
                conversation_id="0:1:80549827440:3061476426516824",
                text="你好",
            )

        self.assertEqual(msg_id, "msg-uuid-from-db")
        self.assertEqual(fb.send_text_calls, 0)  # 协议路径成功 → 不走 fallback
        mock_write.assert_awaited_once_with(
            "acc-uuid-http-proto", "conv-db-1", "你好"
        )
        # 校验请求拼装
        self.assertEqual(sent_body_holder["method"], "POST")
        self.assertEqual(sent_body_holder["url"], "https://imapi.douyin.com/v1/message/send")
        self.assertEqual(
            sent_body_holder["headers"]["content-type"], "application/x-protobuf"
        )
        self.assertIsInstance(sent_body_holder["body"], bytes)
        # body 必须是 sniff 报告里的 envelope 前缀
        self.assertEqual(
            sent_body_holder["body"][:2], b"\x08\x64",  # cmd_id=100
        )

    async def test_impl_send_text_resolves_db_conversation_to_platform_id(self):
        """上层传本地会话 ID 时，HTTP 发送必须改用平台 conversation_id。"""
        from unittest.mock import patch

        sp = self._make_sign_provider(signed_fetch_mock=AsyncMock())
        fb = _StubFallback()
        t = HttpProtocolTransport(sign_provider=sp, fallback=fb, send_text_enabled=True)
        t._post_send_message = AsyncMock(return_value=(object(), "cm-1"))

        with patch(
            "core.douyin.runtime.transport.http_protocol._resolve_conversation_transport_keys",
            new=AsyncMock(return_value=("conv-db-1", "0:1:80549827440:3061476426516824")),
        ) as mock_resolve, patch(
            "core.douyin.runtime.transport.http_protocol.write_manual_out_message",
            new_callable=AsyncMock,
            return_value="msg-db-1",
        ) as mock_write:
            msg_id = await t._impl_send_text_via_http(
                _FakeAccount(),
                conversation_id="conv-db-1",
                text="你好",
            )

        self.assertEqual(msg_id, "msg-db-1")
        mock_resolve.assert_awaited_once_with("acc-uuid-http-proto", "conv-db-1")
        args, kwargs = t._post_send_message.await_args
        self.assertEqual(args[1], "0:1:80549827440:3061476426516824")
        self.assertEqual(args[2], "你好")
        self.assertEqual(kwargs["log_tag"], "send_text")
        mock_write.assert_awaited_once_with("acc-uuid-http-proto", "conv-db-1", "你好")

    async def test_impl_send_text_http_4xx_falls_back(self):
        """HTTP 状态非 2xx → 协议路径抛 RuntimeError → 上层 catch → fallback"""
        from unittest.mock import patch
        from core.douyin.runtime.transport.sign_provider import SignedResponse

        async def fake_signed_fetch(*args, **kwargs):
            return SignedResponse(
                status=429, url="x", headers={}, text="rate limit", content=b"oops"
            )

        sp = self._make_sign_provider(signed_fetch_mock=fake_signed_fetch)
        fb = _StubFallback()
        t = HttpProtocolTransport(sign_provider=sp, fallback=fb, send_text_enabled=True)

        with patch(
            "core.douyin.runtime.transport.http_protocol._resolve_conversation_transport_keys",
            new=AsyncMock(return_value=("conv-db-1", "0:1:80549827440:3061476426516824")),
        ):
            msg_id = await t.send_text(
                _FakeAccount(), object(), conversation_id="c", text="hi"
            )
        self.assertEqual(msg_id, "fallback-text-id")
        self.assertEqual(fb.send_text_calls, 1)

    async def test_impl_send_text_protocol_status_nonzero_falls_back(self):
        """HTTP 200 但协议层 status_code != 0 → fallback"""
        from unittest.mock import patch
        from core.douyin.runtime.transport.sign_provider import SignedResponse
        from core.douyin.runtime.transport.wire.codec import encode_field

        # 构造 status_code=4001 的响应
        bad_resp = (
            encode_field(1, 100)
            + encode_field(2, 1)
            + encode_field(3, 4001)         # status_code != 0
            + encode_field(4, "rate limit")
        )

        async def fake_signed_fetch(*args, **kwargs):
            return SignedResponse(
                status=200, url="x", headers={}, text="", content=bad_resp
            )

        sp = self._make_sign_provider(signed_fetch_mock=fake_signed_fetch)
        fb = _StubFallback()
        t = HttpProtocolTransport(sign_provider=sp, fallback=fb, send_text_enabled=True)

        with patch(
            "core.douyin.runtime.transport.http_protocol._resolve_conversation_transport_keys",
            new=AsyncMock(return_value=("conv-db-1", "0:1:80549827440:3061476426516824")),
        ):
            msg_id = await t.send_text(
                _FakeAccount(), object(), conversation_id="c", text="hi"
            )
        self.assertEqual(msg_id, "fallback-text-id")
        self.assertEqual(fb.send_text_calls, 1)

    async def test_impl_send_text_decode_failure_falls_back(self):
        """响应不是合法 protobuf → 解码异常 → fallback"""
        from unittest.mock import patch
        from core.douyin.runtime.transport.sign_provider import SignedResponse

        async def fake_signed_fetch(*args, **kwargs):
            # 完全乱码，无法解 varint
            return SignedResponse(
                status=200, url="x", headers={}, text="", content=b"\x80\x80\x80\x80"
            )

        sp = self._make_sign_provider(signed_fetch_mock=fake_signed_fetch)
        fb = _StubFallback()
        t = HttpProtocolTransport(sign_provider=sp, fallback=fb, send_text_enabled=True)

        with patch(
            "core.douyin.runtime.transport.http_protocol._resolve_conversation_transport_keys",
            new=AsyncMock(return_value=("conv-db-1", "0:1:80549827440:3061476426516824")),
        ):
            msg_id = await t.send_text(
                _FakeAccount(), object(), conversation_id="c", text="hi"
            )
        self.assertEqual(msg_id, "fallback-text-id")
        self.assertEqual(fb.send_text_calls, 1)

    async def test_impl_send_text_empty_text_raises_value_error(self):
        """空 text 触发 ValueError，不应该走 fallback（这是上层 bug）"""
        from unittest.mock import patch

        sp = self._make_sign_provider(signed_fetch_mock=AsyncMock())
        fb = _StubFallback()
        t = HttpProtocolTransport(sign_provider=sp, fallback=fb, send_text_enabled=True)

        with patch(
            "core.douyin.runtime.transport.http_protocol._resolve_conversation_transport_keys",
            new=AsyncMock(return_value=("conv-db-1", "0:1:80549827440:3061476426516824")),
        ), self.assertRaises(ValueError):
            await t.send_text(_FakeAccount(), object(), conversation_id="c", text="   ")
        self.assertEqual(fb.send_text_calls, 0)


class HttpProtocolSendReplyImplTests(unittest.IsolatedAsyncioTestCase):
    """_impl_send_reply_via_http 真实路径覆盖

    策略：mock _build_segments / _record_auto_outbound_message / _write_reply_log
    + mock _post_send_message（已被 _impl_send_text_via_http 测过），
    保证测试聚焦在 reply 的"分段、落库、失败传递"逻辑。
    """

    def _make_rule(self, *, name="rule-x", template_links=None, links=None, send_mode="single"):
        class _Rule:
            id = "rule-uuid-x"

        r = _Rule()
        r.name = name
        r.send_mode = send_mode
        r.links = links or []
        # 模仿 sender._build_segments 的 rule.template / rule.links 双源
        if template_links is not None:
            class _Template:
                pass
            tpl = _Template()
            tpl.links = template_links
            r.template = tpl
        else:
            r.template = None
        return r

    async def test_impl_send_reply_two_segments_success(self):
        """两段都发成功 → 调 _post_send_message 两次 + 记 outbound 两次 + 一条 success log"""
        from unittest.mock import AsyncMock, patch

        from core.douyin.runtime.transport.wire import SendMessageResult

        sp = _StubSign(ready=True)
        # 协议层成功
        post_mock = AsyncMock(return_value=(
            SendMessageResult(
                status_code=0, status_msg="OK",
                server_msg_id=12345, client_msg_id="cm-uuid",
                raw_envelope={},
            ),
            "cm-uuid",
        ))

        fb = _StubFallback()
        t = HttpProtocolTransport(sign_provider=sp, fallback=fb, send_reply_enabled=True)
        t._post_send_message = post_mock

        with patch(
            "core.douyin.runtime.transport.http_protocol._resolve_conversation_transport_keys",
            new=AsyncMock(return_value=("conv-db-1", "0:1:80549827440:3061476426516824")),
        ) as mock_resolve, patch(
            "core.douyin.runtime.transport.http_protocol._build_segments",
            return_value=["你好啊", "https://example.com"],
        ), patch(
            "core.douyin.runtime.transport.http_protocol._record_auto_outbound_message",
            new_callable=AsyncMock,
            return_value="msg-uuid",
        ) as mock_record, patch(
            "core.douyin.runtime.transport.http_protocol._write_reply_log",
            new_callable=AsyncMock,
            return_value="reply-log-uuid-success",
        ) as mock_write_log, patch(
            "core.douyin.runtime.transport.http_protocol.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            log_id = await t.send_reply(
                _FakeAccount(),
                object(),
                conversation_id="conv-1",
                trigger_message_id="msg-trigger",
                rule=self._make_rule(),
                peer_nickname="测试用户",
            )

        self.assertEqual(log_id, "reply-log-uuid-success")
        mock_resolve.assert_awaited_once_with("acc-uuid-http-proto", "conv-1")
        self.assertEqual(post_mock.await_count, 2)
        self.assertEqual(mock_record.await_count, 2)
        # _write_reply_log 调一次，参数 result='success'
        mock_write_log.assert_awaited_once()
        kwargs = mock_write_log.await_args.kwargs
        self.assertEqual(kwargs["result"], "success")
        self.assertEqual(kwargs["text"], "你好啊")
        self.assertEqual(fb.send_reply_calls, 0)

    async def test_impl_send_reply_empty_segments_writes_skipped_log(self):
        """rule 渲染结果为空 → 直接写 skipped log，不调 _post_send_message"""
        from unittest.mock import AsyncMock, patch

        sp = _StubSign(ready=True)
        post_mock = AsyncMock()
        t = HttpProtocolTransport(
            sign_provider=sp, fallback=_StubFallback(), send_reply_enabled=True
        )
        t._post_send_message = post_mock

        with patch(
            "core.douyin.runtime.transport.http_protocol._resolve_conversation_transport_keys",
            new=AsyncMock(return_value=("conv-db-1", "0:1:80549827440:3061476426516824")),
        ), patch(
            "core.douyin.runtime.transport.http_protocol._build_segments",
            return_value=[],
        ), patch(
            "core.douyin.runtime.transport.http_protocol._write_reply_log",
            new_callable=AsyncMock,
            return_value="reply-log-uuid-skipped",
        ) as mock_write_log:
            log_id = await t.send_reply(
                _FakeAccount(),
                object(),
                conversation_id="conv-1",
                trigger_message_id="msg-trigger",
                rule=self._make_rule(),
                peer_nickname="x",
            )

        self.assertEqual(log_id, "reply-log-uuid-skipped")
        post_mock.assert_not_awaited()
        kwargs = mock_write_log.await_args.kwargs
        self.assertEqual(kwargs["result"], "skipped")
        self.assertEqual(kwargs["error_message"], "规则渲染结果为空")

    async def test_impl_send_reply_first_segment_fails_falls_back_no_log(self):
        """第一段就失败 → 不写 log（避免与 fallback 重复落 log）→ 抛异常 → 上层 fallback"""
        from unittest.mock import AsyncMock, patch

        sp = _StubSign(ready=True)
        post_mock = AsyncMock(side_effect=RuntimeError("send_reply[1] http status=429"))

        fb = _StubFallback()
        t = HttpProtocolTransport(sign_provider=sp, fallback=fb, send_reply_enabled=True)
        t._post_send_message = post_mock

        with patch(
            "core.douyin.runtime.transport.http_protocol._resolve_conversation_transport_keys",
            new=AsyncMock(return_value=("conv-db-1", "0:1:80549827440:3061476426516824")),
        ), patch(
            "core.douyin.runtime.transport.http_protocol._build_segments",
            return_value=["seg-a", "seg-b"],
        ), patch(
            "core.douyin.runtime.transport.http_protocol._write_reply_log",
            new_callable=AsyncMock,
        ) as mock_write_log, patch(
            "core.douyin.runtime.transport.http_protocol._record_auto_outbound_message",
            new_callable=AsyncMock,
        ):
            log_id = await t.send_reply(
                _FakeAccount(),
                object(),
                conversation_id="conv-1",
                trigger_message_id="msg-trigger",
                rule=self._make_rule(),
                peer_nickname="x",
            )

        # 协议路径抛异常 → send_reply 入口 catch → fallback
        self.assertEqual(log_id, "fallback-reply-id")
        self.assertEqual(fb.send_reply_calls, 1)
        # 关键：不能写 log（fallback 会自己写）
        mock_write_log.assert_not_awaited()

    async def test_impl_send_reply_links_payload_from_template(self):
        """rule.template.links 优先级高于 rule.links"""
        from unittest.mock import AsyncMock, patch

        from core.douyin.runtime.transport.wire import SendMessageResult

        sp = _StubSign(ready=True)
        post_mock = AsyncMock(return_value=(
            SendMessageResult(
                status_code=0, status_msg="OK",
                server_msg_id=1, client_msg_id="x",
                raw_envelope={},
            ),
            "x",
        ))

        t = HttpProtocolTransport(
            sign_provider=sp, fallback=_StubFallback(), send_reply_enabled=True
        )
        t._post_send_message = post_mock

        with patch(
            "core.douyin.runtime.transport.http_protocol._resolve_conversation_transport_keys",
            new=AsyncMock(return_value=("conv-db-1", "0:1:80549827440:3061476426516824")),
        ), patch(
            "core.douyin.runtime.transport.http_protocol._build_segments",
            return_value=["one"],
        ), patch(
            "core.douyin.runtime.transport.http_protocol._record_auto_outbound_message",
            new_callable=AsyncMock,
        ), patch(
            "core.douyin.runtime.transport.http_protocol._write_reply_log",
            new_callable=AsyncMock,
            return_value="ok",
        ) as mock_write_log, patch(
            "core.douyin.runtime.transport.http_protocol.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await t.send_reply(
                _FakeAccount(),
                object(),
                conversation_id="conv-1",
                trigger_message_id="m",
                rule=self._make_rule(
                    template_links=["https://from-template/"],
                    links=["https://from-rule/"],
                ),
                peer_nickname="x",
            )

        kwargs = mock_write_log.await_args.kwargs
        self.assertEqual(kwargs["links"], ["https://from-template/"])


class HttpProtocolFlagFromSettingsTests(unittest.IsolatedAsyncioTestCase):
    """verb 开关从 Django setting 读取"""

    async def test_flag_from_settings_when_explicit_none(self):
        from django.test.utils import override_settings

        with override_settings(DOUYIN_HTTP_PROTOCOL_SEND_TEXT=True):
            t = HttpProtocolTransport(
                sign_provider=_StubSign(),
                fallback=_StubFallback(),
            )
            self.assertTrue(t._http_send_text_enabled)

    async def test_explicit_arg_overrides_settings(self):
        from django.test.utils import override_settings

        with override_settings(DOUYIN_HTTP_PROTOCOL_SEND_TEXT=False):
            t = HttpProtocolTransport(
                sign_provider=_StubSign(),
                fallback=_StubFallback(),
                send_text_enabled=True,
            )
            self.assertTrue(t._http_send_text_enabled)


class HttpProtocolScanInboxImplTests(unittest.IsolatedAsyncioTestCase):
    """_impl_scan_inbox_via_http 真实路径覆盖（Phase 3.2c：get_by_user）

    策略：mock signed_fetch 返回手工编出的 get_by_user protobuf；
    mock _upsert_conversation_and_message / _recent_outbound_* 隔离 DB。

    默认隔离 storage：所有测试都 patch save/load_scan_cursor 让其无副作用，
    避免本机/容器路径差异（如 /var/lib/zq-platform 不可写）导致测试莫名 fallback。
    单测专注代码逻辑；持久化语义由 test_scan_inbox_persists_inbound_message /
    test_scan_inbox_loads_persisted_cursor_from_disk 等"显式声明"的 case 覆盖。
    """

    def setUp(self):
        from unittest.mock import patch

        # 进程级隔离磁盘副作用 —— scan_cursor 由内存预设/断言决定
        self._storage_patches = [
            patch(
                "core.douyin.runtime.storage.save_scan_cursor",
                lambda *a, **kw: None,
            ),
            patch(
                "core.douyin.runtime.storage.load_scan_cursor",
                lambda *a, **kw: 0,
            ),
            patch(
                "core.douyin.runtime.storage.save_conversation_scan_cursor",
                lambda *a, **kw: None,
            ),
            patch(
                "core.douyin.runtime.storage.load_conversation_scan_cursor",
                lambda *a, **kw: 0,
            ),
        ]
        for p in self._storage_patches:
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def _build_get_by_user_response(
        *,
        items: list[dict],
        status_code: int = 0,
        next_cursor_us: int = 0,
    ) -> bytes:
        """
        手工编一个 get_by_user 响应（envelope.f6.f200 = {f1=repeated IMMessage}）。

        items: [{
            "conv_id": str,
            "msg_type": int,
            "server_id": int,
            "create_time_us": int,
            "sender_uid": int,
            "sender_sec_uid": str,
            "text": str,
        }, ...]
        """
        import json as _json

        from core.douyin.runtime.transport.wire.codec import (
            WIRE_LEN,
            encode_field,
            encode_tag,
            encode_varint,
        )

        def _wrap_len(field_num: int, payload: bytes) -> bytes:
            return encode_tag(field_num, WIRE_LEN) + encode_varint(len(payload)) + payload

        def _build_msg(m: dict) -> bytes:
            content_json = _json.dumps({"text": m["text"]}, ensure_ascii=False)
            return (
                encode_field(1, m["conv_id"])
                + encode_field(2, m["msg_type"])
                + encode_field(3, m["server_id"])
                + encode_field(4, m["create_time_us"])
                + encode_field(7, m["sender_uid"])
                + encode_field(8, content_json)
                + encode_field(14, m["sender_sec_uid"])
            )

        # inner.f200 = repeated f1=IMMessage
        wrapper = b""
        for it in items:
            wrapper += _wrap_len(1, _build_msg(it))
        if next_cursor_us > 0:
            wrapper += encode_field(2, next_cursor_us)
            wrapper += encode_field(5, next_cursor_us)
        inner = _wrap_len(200, wrapper)
        envelope = (
            encode_field(1, 102)            # cmd_id（GET_BY_USER）
            + encode_field(2, 1)            # seq
            + encode_field(3, status_code)  # status_code
            + encode_field(4, "OK" if status_code == 0 else "ERR")
            + _wrap_len(6, inner)
        )
        return bytes(envelope)

    @staticmethod
    def _build_list_conv_response(
        *,
        self_uid: int,
        items: list[dict],
        status_code: int = 0,
    ) -> bytes:
        """
        手工编一个 list_conversations 响应。
        items: [{
            "short_id": int,
            "conv_id": str,
            "last_msg": {  # optional
                "msg_type": int,
                "server_id": int,
                "create_time_us": int,
                "sender_uid": int,
                "text": str,
            },
            "participants": [(uid, sec_uid), ...],
        }, ...]
        """
        import json as _json

        from core.douyin.runtime.transport.wire.codec import (
            WIRE_LEN,
            encode_field,
            encode_tag,
            encode_varint,
        )

        def _wrap_len(field_num: int, payload: bytes) -> bytes:
            return encode_tag(field_num, WIRE_LEN) + encode_varint(len(payload)) + payload

        def _build_msg(conv_id: str, m: dict) -> bytes:
            content_json = _json.dumps({"text": m["text"]}, ensure_ascii=False)
            return (
                encode_field(1, conv_id)
                + encode_field(2, m["msg_type"])
                + encode_field(3, m["server_id"])
                + encode_field(4, m["create_time_us"])
                + encode_field(7, m["sender_uid"])
                + encode_field(8, content_json)
            )

        def _build_participant(uid: int, sec_uid: str) -> bytes:
            return encode_field(1, uid) + encode_field(5, sec_uid)

        def _build_item(it: dict) -> bytes:
            buf = bytearray()
            buf += encode_field(1, it["short_id"])
            if it.get("last_msg"):
                buf += _wrap_len(3, _build_msg(it["conv_id"], it["last_msg"]))
            buf += encode_field(4, it["conv_id"])
            for uid, sec in it.get("participants", []):
                buf += _wrap_len(5, _build_participant(uid, sec))
            return bytes(buf)

        wrapper = b""
        for it in items:
            wrapper += _wrap_len(4, _build_item(it))
        # inner.f1000 = wrapper
        inner = _wrap_len(1000, wrapper)
        # envelope: f1=cmd_id, f2=seq, f3=status, f4=msg, f6=inner, f13=self_uid
        envelope = (
            encode_field(1, 110)
            + encode_field(2, 1)
            + encode_field(3, status_code)
            + encode_field(4, "OK" if status_code == 0 else "ERR")
            + _wrap_len(6, inner)
            + encode_field(13, self_uid)
        )
        return bytes(envelope)

    @staticmethod
    def _build_get_by_conversation_response(
        *,
        items: list[dict],
        status_code: int = 0,
    ) -> bytes:
        import json as _json

        from core.douyin.runtime.transport.wire.codec import (
            WIRE_LEN,
            encode_field,
            encode_tag,
            encode_varint,
        )

        def _wrap_len(field_num: int, payload: bytes) -> bytes:
            return encode_tag(field_num, WIRE_LEN) + encode_varint(len(payload)) + payload

        def _build_msg(m: dict) -> bytes:
            content_json = _json.dumps({"text": m["text"]}, ensure_ascii=False)
            return (
                encode_field(1, m["conv_id"])
                + encode_field(2, m["msg_type"])
                + encode_field(3, m["server_id"])
                + encode_field(4, m["create_time_us"])
                + encode_field(7, m["sender_uid"])
                + encode_field(8, content_json)
                + encode_field(14, m["sender_sec_uid"])
            )

        wrapper = b""
        for it in items:
            wrapper += _wrap_len(1, _build_msg(it))
        inner = _wrap_len(301, wrapper)
        envelope = (
            encode_field(1, 301)
            + encode_field(2, 1)
            + encode_field(3, status_code)
            + encode_field(4, "OK" if status_code == 0 else "ERR")
            + _wrap_len(6, inner)
        )
        return bytes(envelope)

    def _make_account(self, *, sec_uid: str = "ms_test_self_sec_uid"):
        class _Acc:
            id = "acc-uuid-scan-http"
        a = _Acc()
        a.sec_uid = sec_uid
        return a

    def _make_transport_with_response(self, *, response_bytes: bytes):
        from core.douyin.runtime.transport.sign_provider import SignedResponse

        called_urls: list[str] = []

        async def fake_signed_fetch(method, url, *, body, headers, timeout_ms=None):
            called_urls.append(url)
            return SignedResponse(
                status=200, url=url, headers={}, text="", content=response_bytes
            )

        sp = _StubSign(ready=True)
        sp.signed_fetch = fake_signed_fetch
        sp.await_urls = called_urls
        fb = _StubFallback()
        t = HttpProtocolTransport(
            sign_provider=sp, fallback=fb, scan_inbox_enabled=True
        )
        return t, fb, sp

    async def test_scan_inbox_persists_inbound_message(self):
        """协议路径 happy path：解出一条对方发的消息 → upsert → 返回 ScannedMessage"""
        from unittest.mock import AsyncMock, patch

        body = self._build_get_by_user_response(
            items=[{
                "conv_id": "0:1:80549827440:3061476426516824",
                "msg_type": 1,
                "server_id": 7000000001,
                "create_time_us": 1714000000_000_000,
                "sender_uid": 3061476426516824,
                "sender_sec_uid": "ms_peer_sec_uid_xxx",
                "text": "你好世界",
            }],
        )
        t, fb, _ = self._make_transport_with_response(response_bytes=body)
        account = self._make_account()
        # 预置非 0 cursor，跳过 baseline；保证主路径正常 upsert + 分发
        t._scan_cursor_us[account.id] = 1_700_000_000_000_000

        with patch(
            "core.douyin.runtime.inbox._upsert_conversation_and_message",
            new_callable=AsyncMock,
            return_value=("conv-db-uuid", "msg-db-uuid"),
        ) as mock_upsert, patch(
            "core.douyin.runtime.inbox._recent_outbound_texts",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "core.douyin.runtime.inbox._recent_outbound_replies_log",
            new_callable=AsyncMock,
            return_value=[],
        ):
            results = await t.scan_inbox(account, max_conversations=10)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "你好世界")
        self.assertEqual(results[0].peer_sec_uid, "ms_peer_sec_uid_xxx")
        self.assertEqual(results[0].message_id, "msg-db-uuid")
        self.assertEqual(results[0].conversation_id, "conv-db-uuid")
        self.assertEqual(fb.scan_calls, 0)  # 协议路径成功 → 不走 fallback
        mock_upsert.assert_awaited_once()
        # external_msg_id 用 srv_<server_id>
        kwargs = mock_upsert.await_args.kwargs
        self.assertEqual(kwargs["external_msg_id"], "srv_7000000001")
        self.assertEqual(kwargs["peer_sec_uid"], "ms_peer_sec_uid_xxx")

    async def test_scan_inbox_prefers_get_by_conversation_when_hint_present(self):
        from unittest.mock import AsyncMock, patch

        conv_id = "0:1:80549827440:3061476426516824"
        body = self._build_get_by_conversation_response(
            items=[{
                "conv_id": conv_id,
                "msg_type": 1,
                "server_id": 7000000101,
                "create_time_us": 1714000001_000_000,
                "sender_uid": 3061476426516824,
                "sender_sec_uid": "ms_peer_sec_uid_xxx",
                "text": "会话级扫描命中",
            }],
        )
        t, fb, sp = self._make_transport_with_response(response_bytes=body)
        account = self._make_account()

        with patch(
            "core.douyin.runtime.transport.http_protocol.HttpProtocolTransport._resolve_scan_conversation_id",
            new=AsyncMock(return_value=conv_id),
        ), patch(
            "core.douyin.runtime.transport.http_protocol.HttpProtocolTransport._load_latest_server_message_id_from_db",
            new=AsyncMock(return_value=7000000100),
        ), patch(
            "core.douyin.runtime.transport.http_protocol.HttpProtocolTransport._resolve_user_details",
            new=AsyncMock(return_value={}),
        ), patch(
            "core.douyin.runtime.inbox._upsert_conversation_and_message",
            new_callable=AsyncMock,
            return_value=("conv-db-uuid", "msg-db-uuid"),
        ) as mock_upsert, patch(
            "core.douyin.runtime.inbox._recent_outbound_texts",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "core.douyin.runtime.inbox._recent_outbound_replies_log",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "core.douyin.runtime.storage.load_conversation_scan_cursor",
            return_value=0,
        ), patch(
            "core.douyin.runtime.storage.save_conversation_scan_cursor",
        ) as mock_save_conv_cursor, patch(
            "core.douyin.runtime.storage.save_scan_cursor",
        ) as mock_save_scan_cursor:
            results = await t.scan_inbox(
                account,
                max_conversations=10,
                conversation_hint=conv_id,
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "会话级扫描命中")
        self.assertEqual(results[0].peer_sec_uid, "ms_peer_sec_uid_xxx")
        self.assertEqual(fb.scan_calls, 0)
        mock_upsert.assert_awaited_once()
        self.assertIn(
            "https://imapi.douyin.com/v1/message/get_by_conversation",
            sp.await_urls,
        )
        mock_save_conv_cursor.assert_called()
        save_args = mock_save_conv_cursor.call_args.args
        self.assertEqual(save_args[0], account.id)
        self.assertEqual(save_args[1], conv_id)
        self.assertEqual(save_args[2], 7000000101)
        mock_save_scan_cursor.assert_not_called()

    async def test_scan_inbox_skips_self_sent_message(self):
        """sender_sec_uid == account.sec_uid → 自己发的，跳过"""
        from unittest.mock import AsyncMock, patch

        body = self._build_get_by_user_response(
            items=[{
                "conv_id": "conv-self",
                "msg_type": 1,
                "server_id": 7000000002,
                "create_time_us": 1714000000_000_000,
                "sender_uid": 80549827440,
                "sender_sec_uid": "ms_test_self_sec_uid",  # 等于 account.sec_uid
                "text": "我自己发的",
            }],
        )
        t, _, _ = self._make_transport_with_response(response_bytes=body)
        account = self._make_account()

        with patch(
            "core.douyin.runtime.inbox._upsert_conversation_and_message",
            new_callable=AsyncMock,
        ) as mock_upsert, patch(
            "core.douyin.runtime.inbox._recent_outbound_texts",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "core.douyin.runtime.inbox._recent_outbound_replies_log",
            new_callable=AsyncMock,
            return_value=[],
        ):
            results = await t.scan_inbox(account, max_conversations=10)

        self.assertEqual(results, [])
        mock_upsert.assert_not_awaited()

    async def test_scan_inbox_skips_non_text_message(self):
        """msg_type != 1 → 系统消息（已读回执等），跳过"""
        from unittest.mock import AsyncMock, patch

        body = self._build_get_by_user_response(
            items=[{
                "conv_id": "conv-sys",
                "msg_type": 50001,  # 非 text
                "server_id": 7000000003,
                "create_time_us": 1714000000_000_000,
                "sender_uid": 3061476426516824,
                "sender_sec_uid": "ms_peer_sec_uid_xxx",
                "text": "should-be-ignored",
            }],
        )
        t, _, _ = self._make_transport_with_response(response_bytes=body)
        account = self._make_account()

        with patch(
            "core.douyin.runtime.inbox._upsert_conversation_and_message",
            new_callable=AsyncMock,
        ) as mock_upsert, patch(
            "core.douyin.runtime.inbox._recent_outbound_texts",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "core.douyin.runtime.inbox._recent_outbound_replies_log",
            new_callable=AsyncMock,
            return_value=[],
        ):
            results = await t.scan_inbox(account, max_conversations=10)
        self.assertEqual(results, [])
        mock_upsert.assert_not_awaited()

    async def test_scan_inbox_echo_dedup_skips_recent_outbound(self):
        """对方回声消息（其实是我们刚发的，被服务端 echo）→ 命中 _recent_outbound_texts → 跳过"""
        from unittest.mock import AsyncMock, patch

        body = self._build_get_by_user_response(
            items=[{
                "conv_id": "conv-echo",
                "msg_type": 1,
                "server_id": 7000000004,
                "create_time_us": 1714000000_000_000,
                "sender_uid": 3061476426516824,
                "sender_sec_uid": "ms_peer_sec_uid_xxx",
                "text": "我刚发出去的内容",
            }],
        )
        t, _, _ = self._make_transport_with_response(response_bytes=body)
        account = self._make_account()

        # _norm_for_compare 会去空白；这里直接给原文就够
        with patch(
            "core.douyin.runtime.inbox._upsert_conversation_and_message",
            new_callable=AsyncMock,
        ) as mock_upsert, patch(
            "core.douyin.runtime.inbox._recent_outbound_texts",
            new_callable=AsyncMock,
            return_value=["我刚发出去的内容"],
        ), patch(
            "core.douyin.runtime.inbox._recent_outbound_replies_log",
            new_callable=AsyncMock,
            return_value=[],
        ):
            results = await t.scan_inbox(account, max_conversations=10)
        self.assertEqual(results, [])
        mock_upsert.assert_not_awaited()

    async def test_scan_inbox_identity_warning_when_no_self_message(self):
        """get_by_user 协议下没法做 hard identity check（没有 participants 结构）：
        改为软推断 + warn-only —— 当响应里 ≥5 条 message 但没一条 sender_sec
        命中 account.sec_uid 时，打 warning（不抛）。
        """
        from unittest.mock import AsyncMock, patch

        # 5 条 message，没一条 sender_sec 等于 account.sec_uid="ms_test_self_sec_uid"
        items = []
        for i in range(5):
            items.append({
                "conv_id": f"conv-{i}",
                "msg_type": 1,
                "server_id": 9_000_000_000 + i,
                "create_time_us": 1714000000_000_000 + i * 1000,
                "sender_uid": 3061476426516824 + i,
                "sender_sec_uid": f"ms_peer_{i}",
                "text": f"消息 {i}",
            })
        body = self._build_get_by_user_response(items=items)
        t, _, _ = self._make_transport_with_response(response_bytes=body)
        account = self._make_account(sec_uid="ms_test_self_sec_uid")

        with patch.object(
            __import__(
                "core.douyin.runtime.transport.http_protocol", fromlist=["logger"]
            ).logger,
            "warning",
        ) as log_warn, patch(
            "core.douyin.runtime.inbox._upsert_conversation_and_message",
            new_callable=AsyncMock,
            return_value=("conv-db", "msg-db"),
        ), patch(
            "core.douyin.runtime.inbox._recent_outbound_texts",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "core.douyin.runtime.inbox._recent_outbound_replies_log",
            new_callable=AsyncMock,
            return_value=[],
        ):
            # _impl_scan_inbox_via_http 不抛异常
            await t._impl_scan_inbox_via_http(
                account,
                max_conversations=10,
                include_recent_without_unread=False,
            )
        # 至少有一条 warning 提到"换号"
        warn_msgs = [
            (call.args[0] if call.args else "")
            for call in log_warn.call_args_list
        ]
        self.assertTrue(
            any("换号" in str(m) or "未在" in str(m) for m in warn_msgs),
            f"未看到 identity warning，actual={warn_msgs}",
        )

    async def test_scan_inbox_falls_back_when_signer_not_ready(self):
        """signer 不健康 → 跳过协议路径直接 fallback；不访问 signed_fetch"""
        sp = _StubSign(ready=False)
        sp.signed_fetch = AsyncMock()
        fb = _StubFallback()
        t = HttpProtocolTransport(
            sign_provider=sp, fallback=fb, scan_inbox_enabled=True
        )
        account = self._make_account()

        result = await t.scan_inbox(account, max_conversations=10)
        self.assertEqual(result, ["fallback-msg"])
        self.assertEqual(fb.scan_calls, 1)
        sp.signed_fetch.assert_not_called()

    async def test_dry_run_does_not_persist_and_returns_empty(self):
        """dry_run=True：解析正常但不调 _upsert，不调 save_scan_cursor，返回空列表"""
        from unittest.mock import AsyncMock, patch

        body = self._build_get_by_user_response(
            items=[{
                "conv_id": "conv-dry",
                "msg_type": 1,
                "server_id": 9000000001,
                "create_time_us": 1714000000_000_000,
                "sender_uid": 3061476426516824,
                "sender_sec_uid": "ms_peer_sec_uid_xxx",
                "text": "影子模式只读",
            }],
        )
        t, _, _ = self._make_transport_with_response(response_bytes=body)
        account = self._make_account()

        with patch(
            "core.douyin.runtime.inbox._upsert_conversation_and_message",
            new_callable=AsyncMock,
        ) as mock_upsert, patch(
            "core.douyin.runtime.inbox._recent_outbound_texts",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "core.douyin.runtime.inbox._recent_outbound_replies_log",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "core.douyin.runtime.storage.load_scan_cursor",
            return_value=0,  # 与磁盘隔离
        ), patch(
            "core.douyin.runtime.storage.save_scan_cursor",
        ) as mock_save:
            results = await t._impl_scan_inbox_via_http(
                account,
                max_conversations=10,
                include_recent_without_unread=False,
                dry_run=True,
            )

        self.assertEqual(results, [])
        mock_upsert.assert_not_awaited()
        candidates = getattr(t, "_last_dry_run_candidates", None)
        self.assertIsNotNone(candidates)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["external_msg_id"], "srv_9000000001")
        self.assertEqual(candidates[0]["text"], "影子模式只读")
        # dry_run 关键约束：不写盘 cursor —— 切到主路径时还要走 baseline 保护
        mock_save.assert_not_called()

    async def test_scan_inbox_advances_cursor_us_on_success(self):
        """非 dry_run 路径下，cursor_us 优先推进到 wrapper 提供的 next cursor"""
        from unittest.mock import AsyncMock, patch

        body = self._build_get_by_user_response(
            items=[
                {
                    "conv_id": "conv-A",
                    "msg_type": 1,
                    "server_id": 1000_000_001,
                    "create_time_us": 1_714_000_000_000_000,
                    "sender_uid": 30000001,
                    "sender_sec_uid": "ms_peer_a",
                    "text": "msg-a",
                },
                {
                    "conv_id": "conv-B",
                    "msg_type": 1,
                    "server_id": 1000_000_002,
                    "create_time_us": 1_714_000_001_500_000,
                    "sender_uid": 30000002,
                    "sender_sec_uid": "ms_peer_b",
                    "text": "msg-b",
                },
            ],
            next_cursor_us=1_777_359_065_393_749,
        )
        t, _, _ = self._make_transport_with_response(response_bytes=body)
        account = self._make_account()
        # 预置非 0 cursor 跳过 baseline，专注校验"推进 cursor"逻辑
        t._scan_cursor_us[account.id] = 1_700_000_000_000_000

        with patch(
            "core.douyin.runtime.inbox._upsert_conversation_and_message",
            new_callable=AsyncMock,
            return_value=("conv-db", "msg-db"),
        ), patch(
            "core.douyin.runtime.inbox._recent_outbound_texts",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "core.douyin.runtime.inbox._recent_outbound_replies_log",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "core.douyin.runtime.storage.save_scan_cursor",
        ) as mock_save_cursor, patch(
            "core.douyin.runtime.storage.load_scan_cursor",
            return_value=0,
        ):
            await t.scan_inbox(account, max_conversations=10)

        self.assertEqual(
            t._scan_cursor_us.get("acc-uuid-scan-http"),
            1_777_359_065_393_749,
        )
        # 推进的 cursor 应该同步落盘
        mock_save_cursor.assert_called_once_with(
            "acc-uuid-scan-http", 1_777_359_065_393_749
        )

    async def test_scan_inbox_first_round_baseline_does_not_dispatch(self):
        """关键灰度保护：cursor 完全没有（内存 + 磁盘都 0）→ 进 baseline 模式：
        只解析 + 推 cursor + 落盘，**不 upsert、不返回 ScannedMessage**。
        避免 SCAN_INBOX=true 切换瞬间，把"最近 50 条历史 message"当成新消息分发。
        """
        from unittest.mock import AsyncMock, patch

        body = self._build_get_by_user_response(
            items=[{
                "conv_id": "conv-baseline",
                "msg_type": 1,
                "server_id": 999_111_222,
                "create_time_us": 1_714_000_500_000_000,
                "sender_uid": 30000099,
                "sender_sec_uid": "ms_peer_baseline",
                "text": "这条 baseline 阶段不该被分发",
            }],
        )
        t, _, _ = self._make_transport_with_response(response_bytes=body)
        account = self._make_account()
        # 关键前置：清空内存 + 磁盘 cursor → 触发 baseline
        t._scan_cursor_us.pop(account.id, None)

        with patch(
            "core.douyin.runtime.inbox._upsert_conversation_and_message",
            new_callable=AsyncMock,
        ) as mock_upsert, patch(
            "core.douyin.runtime.inbox._recent_outbound_texts",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "core.douyin.runtime.inbox._recent_outbound_replies_log",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "core.douyin.runtime.storage.load_scan_cursor",
            return_value=0,
        ), patch(
            "core.douyin.runtime.storage.save_scan_cursor",
        ) as mock_save_cursor:
            results = await t.scan_inbox(account, max_conversations=10)

        # baseline 模式：返回空列表
        self.assertEqual(results, [])
        # 不 upsert
        mock_upsert.assert_not_awaited()
        # cursor 仍然要推进 + 落盘（关键：下一轮起才能正常分发）
        self.assertEqual(
            t._scan_cursor_us.get(account.id), 1_714_000_500_000_000
        )
        mock_save_cursor.assert_called_once_with(
            account.id, 1_714_000_500_000_000
        )

    async def test_scan_inbox_loads_persisted_cursor_from_disk(self):
        """worker 重启后内存 cursor=0 → 从磁盘读 → 不进 baseline → 正常分发"""
        from unittest.mock import AsyncMock, patch

        body = self._build_get_by_user_response(
            items=[{
                "conv_id": "conv-resume",
                "msg_type": 1,
                "server_id": 555_111_222,
                "create_time_us": 1_714_000_900_000_000,
                "sender_uid": 30000077,
                "sender_sec_uid": "ms_peer_resume",
                "text": "重启后第一条新消息",
            }],
        )
        t, _, _ = self._make_transport_with_response(response_bytes=body)
        account = self._make_account()
        # 内存空，但磁盘里有上次落盘的 cursor
        t._scan_cursor_us.pop(account.id, None)

        with patch(
            "core.douyin.runtime.inbox._upsert_conversation_and_message",
            new_callable=AsyncMock,
            return_value=("conv-db", "msg-db"),
        ) as mock_upsert, patch(
            "core.douyin.runtime.inbox._recent_outbound_texts",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "core.douyin.runtime.inbox._recent_outbound_replies_log",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "core.douyin.runtime.storage.load_scan_cursor",
            return_value=1_700_000_000_000_000,  # 磁盘里有
        ), patch(
            "core.douyin.runtime.storage.save_scan_cursor",
        ):
            results = await t.scan_inbox(account, max_conversations=10)

        # 不进 baseline → 正常分发
        self.assertEqual(len(results), 1)
        mock_upsert.assert_awaited_once()

    async def test_scan_inbox_passes_persisted_cursor_to_encoder(self):
        """已有 cursor 应原样传给 encoder（用 spy 抓 sniff body 解出 f200 inner）"""
        from unittest.mock import AsyncMock, patch

        body = self._build_get_by_user_response(items=[])
        t, _, sp = self._make_transport_with_response(response_bytes=body)
        account = self._make_account()
        # 预置 cursor
        t._scan_cursor_us[account.id] = 1_700_000_000_000_000

        captured = {}

        async def spy_signed_fetch(method, url, *, body, headers, timeout_ms=None):
            captured["body"] = body
            from core.douyin.runtime.transport.sign_provider import SignedResponse
            return SignedResponse(
                status=200, url=url, headers={}, text="",
                content=self._build_get_by_user_response(items=[]),
            )

        sp.signed_fetch = spy_signed_fetch

        with patch(
            "core.douyin.runtime.inbox._upsert_conversation_and_message",
            new_callable=AsyncMock,
        ), patch(
            "core.douyin.runtime.inbox._recent_outbound_texts",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "core.douyin.runtime.inbox._recent_outbound_replies_log",
            new_callable=AsyncMock,
            return_value=[],
        ):
            await t.scan_inbox(account, max_conversations=10)

        # 解请求 envelope.f8.f200.{f1=cursor, f2=limit}
        from core.douyin.runtime.transport.wire.codec import iter_fields

        envelope = {}
        for fnum, _w, val in iter_fields(captured["body"]):
            envelope.setdefault(fnum, []).append(val)
        inner = envelope[8][0]
        wrap = {}
        for fnum, _w, val in iter_fields(inner):
            wrap.setdefault(fnum, []).append(val)
        payload = wrap[200][0]
        sp_kv = {}
        for fnum, _w, val in iter_fields(payload):
            sp_kv.setdefault(fnum, []).append(val)
        self.assertEqual(sp_kv[1][0], 1_700_000_000_000_000)
        self.assertEqual(sp_kv[2][0], 50)


class HttpProtocolUserDetailTests(unittest.IsolatedAsyncioTestCase):
    """P0：user_detail 批量补昵称/头像，串到 scan 主路径"""

    def setUp(self):
        # 与 HttpProtocolScanInboxImplTests 同样隔离 storage 磁盘 IO
        from unittest.mock import patch
        for p in (
            patch("core.douyin.runtime.storage.save_scan_cursor", lambda *a, **kw: None),
            patch("core.douyin.runtime.storage.load_scan_cursor", lambda *a, **kw: 0),
        ):
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def _extract_users():
        from core.douyin.runtime.transport.http_protocol import HttpProtocolTransport
        return HttpProtocolTransport._extract_user_detail_users

    def test_extract_shape_a_data_users(self):
        """{"data": {"users": [...]}}"""
        out = self._extract_users()({
            "status_code": 0,
            "data": {"users": [
                {"user_id": 1001, "nickname": "甲"},
                {"user_id": 1002, "nickname": "乙"},
            ]},
        })
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["nickname"], "甲")

    def test_extract_shape_b_data_user_infos(self):
        """{"data": {"user_infos": [...]}}"""
        out = self._extract_users()({
            "data": {"user_infos": [
                {"uid": 2001, "name": "丙"},
            ]},
        })
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["name"], "丙")

    def test_extract_shape_c_data_uid_dict(self):
        """{"data": {"<uid>": {...}}}"""
        out = self._extract_users()({
            "data": {
                "3001": {"nickname": "丁"},
                "3002": {"nickname": "戊"},
            },
        })
        # 应该把 uid 反填到 entry 里
        uids = sorted(int(e.get("user_id") or 0) for e in out)
        self.assertEqual(uids, [3001, 3002])

    def test_extract_returns_empty_for_garbage_payload(self):
        self.assertEqual(self._extract_users()({}), [])
        self.assertEqual(self._extract_users()({"data": "not-a-dict"}), [])
        self.assertEqual(self._extract_users()({"data": {}}), [])

    def _make_account(self, *, sec_uid: str = "ms_test_self_sec_uid"):
        class _Acc:
            id = "acc-uuid-user-detail"
        a = _Acc()
        a.sec_uid = sec_uid
        return a

    def _make_transport(self, *, signed_fetch_impl):
        sp = _StubSign(ready=True)
        sp.signed_fetch = signed_fetch_impl
        fb = _StubFallback()
        return HttpProtocolTransport(sign_provider=sp, fallback=fb, scan_inbox_enabled=True)

    async def test_resolve_user_details_parses_shape_a_response(self):
        """signed_fetch 返回 shape A → _resolve_user_details 解出 dict"""
        from core.douyin.runtime.transport.sign_provider import SignedResponse

        json_body = (
            '{"status_code":0,"data":{"users":['
            '{"user_id":4001,"nickname":"小明","avatar_url":"https://x/a.jpg",'
            '"sec_uid":"MS4_peer_a","short_id":"123"},'
            '{"user_id":4002,"nickname":"小红","sec_uid":"MS4_peer_b"}'
            ']}}'
        )

        async def fake_signed_fetch(method, url, *, body, headers, timeout_ms=None):
            self.assertEqual(method, "POST")
            self.assertIn("user_detail", url)
            # 必须带 JSON content-type
            self.assertEqual(headers.get("content-type"), "application/json")
            # body 必须是字符串 JSON（不是 protobuf 字节）
            self.assertIsInstance(body, str)
            self.assertIn('"user_ids"', body)
            return SignedResponse(
                status=200, url=url, headers={}, text=json_body,
                content=json_body.encode("utf-8"),
            )

        t = self._make_transport(signed_fetch_impl=fake_signed_fetch)
        out = await t._resolve_user_details(self._make_account(), [4001, 4002, 0, -1])
        self.assertEqual(out[4001]["nickname"], "小明")
        self.assertEqual(out[4001]["avatar"], "https://x/a.jpg")
        self.assertEqual(out[4001]["sec_uid"], "MS4_peer_a")
        self.assertEqual(out[4002]["nickname"], "小红")
        self.assertNotIn(0, out)
        self.assertNotIn(-1, out)

    async def test_resolve_user_details_returns_empty_on_signer_unavailable(self):
        """signed_fetch 抛 SignerUnavailable → 返回 {} 不抛，不影响 scan 主路径"""

        async def boom(method, url, *, body, headers, timeout_ms=None):
            raise SignerUnavailable("page closed")

        t = self._make_transport(signed_fetch_impl=boom)
        out = await t._resolve_user_details(self._make_account(), [5001])
        self.assertEqual(out, {})

    async def test_resolve_user_details_returns_empty_on_http_error(self):
        from core.douyin.runtime.transport.sign_provider import SignedResponse

        async def http_500(method, url, *, body, headers, timeout_ms=None):
            return SignedResponse(
                status=500, url=url, headers={}, text="upstream timeout", content=b""
            )

        t = self._make_transport(signed_fetch_impl=http_500)
        out = await t._resolve_user_details(self._make_account(), [5001, 5002])
        self.assertEqual(out, {})

    async def test_scan_inbox_passes_user_detail_nickname_to_upsert(self):
        """scan 主路径：user_detail 拿到的 nickname/avatar 传给 _upsert"""
        from unittest.mock import AsyncMock, patch

        from core.douyin.runtime.transport.sign_provider import SignedResponse

        # get_by_user 一条入向 message
        body_pb = HttpProtocolScanInboxImplTests._build_get_by_user_response(
            items=[{
                "conv_id": "conv-x",
                "msg_type": 1,
                "server_id": 7700_000_001,
                "create_time_us": 1_714_002_000_000_000,
                "sender_uid": 60001,
                "sender_sec_uid": "MS4_peer_x",
                "text": "你好啊",
            }],
        )

        # signed_fetch 双角色：scan_inbox endpoint → protobuf；user_detail → JSON
        user_detail_json = (
            '{"status_code":0,"data":{"users":['
            '{"user_id":60001,"nickname":"对端昵称","avatar_url":"https://cdn/p.jpg",'
            '"sec_uid":"MS4_peer_x"}'
            ']}}'
        )

        async def router(method, url, *, body, headers, timeout_ms=None):
            if "user_detail" in url:
                return SignedResponse(
                    status=200, url=url, headers={}, text=user_detail_json,
                    content=user_detail_json.encode("utf-8"),
                )
            return SignedResponse(
                status=200, url=url, headers={}, text="", content=body_pb,
            )

        t = self._make_transport(signed_fetch_impl=router)
        account = self._make_account()
        # 跳过 baseline，专注校验昵称是否传到 upsert
        t._scan_cursor_us[account.id] = 1_700_000_000_000_000

        with patch(
            "core.douyin.runtime.inbox._upsert_conversation_and_message",
            new_callable=AsyncMock,
            return_value=("conv-db-uuid", "msg-db-uuid"),
        ) as mock_upsert, patch(
            "core.douyin.runtime.inbox._silent_mark_read_in_db",
            new_callable=AsyncMock,
            return_value=1,
        ), patch(
            "core.douyin.runtime.inbox._recent_outbound_texts",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "core.douyin.runtime.inbox._recent_outbound_replies_log",
            new_callable=AsyncMock,
            return_value=[],
        ):
            results = await t.scan_inbox(account, max_conversations=10)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].peer_nickname, "对端昵称")
        # 关键断言：upsert 收到了 user_detail 解出来的字段
        kwargs = mock_upsert.await_args.kwargs
        self.assertEqual(kwargs["peer_nickname"], "对端昵称")
        self.assertEqual(kwargs["peer_avatar"], "https://cdn/p.jpg")

    async def test_scan_inbox_user_detail_failure_does_not_block_dispatch(self):
        """user_detail 接口失败 → upsert 仍然被调用，nickname/avatar=None（不污染 DB 已有值）"""
        from unittest.mock import AsyncMock, patch

        from core.douyin.runtime.transport.sign_provider import SignedResponse

        body_pb = HttpProtocolScanInboxImplTests._build_get_by_user_response(
            items=[{
                "conv_id": "conv-y",
                "msg_type": 1,
                "server_id": 7700_000_002,
                "create_time_us": 1_714_002_001_000_000,
                "sender_uid": 60002,
                "sender_sec_uid": "MS4_peer_y",
                "text": "在吗",
            }],
        )

        async def router(method, url, *, body, headers, timeout_ms=None):
            if "user_detail" in url:
                # JSON 解析失败 → _resolve_user_details 返回 {}
                return SignedResponse(
                    status=200, url=url, headers={}, text="not-a-json",
                    content=b"not-a-json",
                )
            return SignedResponse(
                status=200, url=url, headers={}, text="", content=body_pb,
            )

        t = self._make_transport(signed_fetch_impl=router)
        account = self._make_account()
        t._scan_cursor_us[account.id] = 1_700_000_000_000_000

        with patch(
            "core.douyin.runtime.inbox._upsert_conversation_and_message",
            new_callable=AsyncMock,
            return_value=("conv-db", "msg-db"),
        ) as mock_upsert, patch(
            "core.douyin.runtime.inbox._silent_mark_read_in_db",
            new_callable=AsyncMock,
            return_value=1,
        ), patch(
            "core.douyin.runtime.inbox._recent_outbound_texts",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "core.douyin.runtime.inbox._recent_outbound_replies_log",
            new_callable=AsyncMock,
            return_value=[],
        ):
            results = await t.scan_inbox(account, max_conversations=10)

        self.assertEqual(len(results), 1)
        kwargs = mock_upsert.await_args.kwargs
        # 关键：失败时 peer_nickname=None，让 _upsert 走 "non-empty 才覆盖" 保护
        self.assertIsNone(kwargs["peer_nickname"])
        self.assertIsNone(kwargs["peer_avatar"])


class HttpProtocolSilentMarkReadTests(unittest.IsolatedAsyncioTestCase):
    """P0：HTTP 路径 upsert 成功 → DB 内静默清 unread（不打抖音 mark_read 接口）"""

    def setUp(self):
        from unittest.mock import patch
        for p in (
            patch("core.douyin.runtime.storage.save_scan_cursor", lambda *a, **kw: None),
            patch("core.douyin.runtime.storage.load_scan_cursor", lambda *a, **kw: 0),
        ):
            p.start()
            self.addCleanup(p.stop)

    def _make_account(self):
        class _Acc:
            id = "acc-uuid-mark-read"
        a = _Acc()
        a.sec_uid = "ms_test_self_sec_uid"
        return a

    def _make_transport_with_response(self, *, response_bytes: bytes):
        from core.douyin.runtime.transport.sign_provider import SignedResponse

        async def fake_signed_fetch(method, url, *, body, headers, timeout_ms=None):
            # 不发 user_detail（让它失败 → 回空 dict，简化测试）
            if "user_detail" in url:
                return SignedResponse(
                    status=200, url=url, headers={}, text="{}", content=b"{}",
                )
            return SignedResponse(
                status=200, url=url, headers={}, text="", content=response_bytes,
            )

        sp = _StubSign(ready=True)
        sp.signed_fetch = fake_signed_fetch
        fb = _StubFallback()
        return HttpProtocolTransport(
            sign_provider=sp, fallback=fb, scan_inbox_enabled=True
        )

    async def test_silent_mark_read_called_per_touched_conversation(self):
        """两条入向 message（来自不同 conv）→ silent mark_read 各调一次"""
        from unittest.mock import AsyncMock, patch

        body = HttpProtocolScanInboxImplTests._build_get_by_user_response(
            items=[
                {
                    "conv_id": "conv-A",
                    "msg_type": 1,
                    "server_id": 8800_000_001,
                    "create_time_us": 1_714_003_000_000_000,
                    "sender_uid": 70001,
                    "sender_sec_uid": "MS4_peer_a",
                    "text": "msg-1",
                },
                {
                    "conv_id": "conv-B",
                    "msg_type": 1,
                    "server_id": 8800_000_002,
                    "create_time_us": 1_714_003_001_000_000,
                    "sender_uid": 70002,
                    "sender_sec_uid": "MS4_peer_b",
                    "text": "msg-2",
                },
            ],
        )
        t = self._make_transport_with_response(response_bytes=body)
        account = self._make_account()
        t._scan_cursor_us[account.id] = 1_700_000_000_000_000

        # 两次 upsert 返回不同 conv_id
        upsert_returns = iter([("conv-db-a", "msg-db-a"), ("conv-db-b", "msg-db-b")])

        async def fake_upsert(**kwargs):
            return next(upsert_returns)

        with patch(
            "core.douyin.runtime.inbox._upsert_conversation_and_message",
            new=fake_upsert,
        ), patch(
            "core.douyin.runtime.inbox._silent_mark_read_in_db",
            new_callable=AsyncMock,
            return_value=1,
        ) as mock_mark, patch(
            "core.douyin.runtime.inbox._recent_outbound_texts",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "core.douyin.runtime.inbox._recent_outbound_replies_log",
            new_callable=AsyncMock,
            return_value=[],
        ):
            await t.scan_inbox(account, max_conversations=10)

        # 两个不同的 conv_id 都应被清 unread
        called_with = sorted(c.args[0] for c in mock_mark.await_args_list)
        self.assertEqual(called_with, ["conv-db-a", "conv-db-b"])

    async def test_silent_mark_read_not_called_in_baseline(self):
        """baseline 模式（cursor=0）不分发 → 不清 unread（DB 状态保持原样）"""
        from unittest.mock import AsyncMock, patch

        body = HttpProtocolScanInboxImplTests._build_get_by_user_response(
            items=[{
                "conv_id": "conv-baseline",
                "msg_type": 1,
                "server_id": 8800_000_999,
                "create_time_us": 1_714_003_900_000_000,
                "sender_uid": 70099,
                "sender_sec_uid": "MS4_peer_baseline",
                "text": "baseline 不动 unread",
            }],
        )
        t = self._make_transport_with_response(response_bytes=body)
        account = self._make_account()
        # 关键：cursor=0 触发 baseline
        t._scan_cursor_us.pop(account.id, None)

        with patch(
            "core.douyin.runtime.inbox._upsert_conversation_and_message",
            new_callable=AsyncMock,
        ), patch(
            "core.douyin.runtime.inbox._silent_mark_read_in_db",
            new_callable=AsyncMock,
            return_value=1,
        ) as mock_mark, patch(
            "core.douyin.runtime.inbox._recent_outbound_texts",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "core.douyin.runtime.inbox._recent_outbound_replies_log",
            new_callable=AsyncMock,
            return_value=[],
        ):
            await t.scan_inbox(account, max_conversations=10)

        mock_mark.assert_not_awaited()

    async def test_silent_mark_read_not_called_in_dry_run(self):
        """dry_run（dual_run 影子模式）不落库 → 不动 unread"""
        from unittest.mock import AsyncMock, patch

        body = HttpProtocolScanInboxImplTests._build_get_by_user_response(
            items=[{
                "conv_id": "conv-dry",
                "msg_type": 1,
                "server_id": 8800_000_777,
                "create_time_us": 1_714_003_700_000_000,
                "sender_uid": 70077,
                "sender_sec_uid": "MS4_peer_dry",
                "text": "dry-run 不动 unread",
            }],
        )
        t = self._make_transport_with_response(response_bytes=body)
        account = self._make_account()

        with patch(
            "core.douyin.runtime.inbox._upsert_conversation_and_message",
            new_callable=AsyncMock,
        ), patch(
            "core.douyin.runtime.inbox._silent_mark_read_in_db",
            new_callable=AsyncMock,
            return_value=1,
        ) as mock_mark, patch(
            "core.douyin.runtime.inbox._recent_outbound_texts",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "core.douyin.runtime.inbox._recent_outbound_replies_log",
            new_callable=AsyncMock,
            return_value=[],
        ):
            await t._impl_scan_inbox_via_http(
                account,
                max_conversations=10,
                include_recent_without_unread=False,
                dry_run=True,
            )

        mock_mark.assert_not_awaited()


class HttpProtocolDualRunTests(unittest.IsolatedAsyncioTestCase):
    """dual-run 影子模式：fallback 主路径 + HTTP 旁路 dry_run 对账"""

    async def test_dual_run_disabled_does_not_call_http(self):
        """SCAN_INBOX_DUAL_RUN=false → 只跑 fallback，HTTP 完全不动"""
        from unittest.mock import AsyncMock

        from django.test.utils import override_settings

        sp = _StubSign(ready=True)
        sp.signed_fetch = AsyncMock()
        fb = _StubFallback()
        # override settings 确保不受 .env 默认值影响
        with override_settings(
            DOUYIN_HTTP_PROTOCOL_SCAN_INBOX=False,
            DOUYIN_HTTP_PROTOCOL_SCAN_INBOX_DUAL_RUN=False,
        ):
            t = HttpProtocolTransport(sign_provider=sp, fallback=fb)
        self.assertFalse(t._http_scan_inbox_enabled)
        self.assertFalse(t._http_scan_inbox_dual_run)

        class _Acc:
            id = "acc"
            sec_uid = "x"
        await t.scan_inbox(_Acc(), max_conversations=5)
        self.assertEqual(fb.scan_calls, 1)
        sp.signed_fetch.assert_not_called()

    async def test_dual_run_enabled_runs_http_dry_run_and_compares(self):
        """SCAN_INBOX_DUAL_RUN=true：fallback 返回值不变；HTTP dry_run 异步触发；对账日志输出"""
        from unittest.mock import AsyncMock, patch

        sp = _StubSign(ready=True)
        fb = _StubFallback()
        t = HttpProtocolTransport(sign_provider=sp, fallback=fb)
        # 显式开 dual-run（避免依赖 settings）
        t._http_scan_inbox_dual_run = True

        # 让 _impl_scan_inbox_via_http 在 dry_run=True 时填充候选，用 mock 实现
        async def fake_impl(account, *, max_conversations, include_recent_without_unread, dry_run=False):
            assert dry_run is True
            t._last_dry_run_candidates = [{
                "external_msg_id": "srv_1",
                "peer_sec_uid": "peer_a",
                "text": "重叠消息",  # 与 fallback 重叠
                "server_message_id": 1,
                "received_at": "2026-04-28T00:00:00+00:00",
            }, {
                "external_msg_id": "srv_2",
                "peer_sec_uid": "peer_b",
                "text": "只有 HTTP 看到",  # only_http
                "server_message_id": 2,
                "received_at": "2026-04-28T00:00:01+00:00",
            }]
            return []

        t._impl_scan_inbox_via_http = fake_impl

        # 让 fallback 返回一条 ScannedMessage：peer_a 的"重叠消息" + 一条 only_browser
        from core.douyin.runtime.inbox import ScannedMessage

        async def fake_fb_scan(account, **kwargs):
            fb.scan_calls += 1
            return [
                ScannedMessage(
                    message_id="m1",
                    conversation_id="c1",
                    peer_sec_uid="peer_a",
                    peer_nickname=None,
                    text="重叠消息",
                    received_at="2026-04-28T00:00:00+00:00",
                ),
                ScannedMessage(
                    message_id="m9",
                    conversation_id="c9",
                    peer_sec_uid="peer_c",
                    peer_nickname=None,
                    text="只有浏览器看到",
                    received_at="2026-04-28T00:00:02+00:00",
                ),
            ]
        fb.scan_inbox = fake_fb_scan

        class _Acc:
            id = "acc-dual"
            sec_uid = "ms_x"

        # 抓一下 logger.info 的内容，验证 dual_run 摘要被打印
        with patch.object(
            __import__("core.douyin.runtime.transport.http_protocol", fromlist=["logger"]).logger,
            "info",
        ) as log_info:
            result = await t.scan_inbox(_Acc(), max_conversations=10)
            # ensure_future 调度的 _dual_run_compare 需要 yield 出去再执行
            import asyncio as _aio
            await _aio.sleep(0)
            await _aio.sleep(0)

        # 主路径行为：worker 看到的就是 fallback 的结果
        self.assertEqual(len(result), 2)
        self.assertEqual(fb.scan_calls, 1)

        # 对账日志校验：搜含 "dual_run" 的 info 行
        dual_run_msgs = [
            (call.args[0] if call.args else "")
            for call in log_info.call_args_list
            if call.args and "dual_run" in str(call.args[0])
        ]
        self.assertTrue(
            any("both=1" in m and "only_http=1" in m and "only_browser=1" in m
                for m in dual_run_msgs),
            f"未看到预期对账摘要，actual={dual_run_msgs}",
        )

    async def test_dual_run_swallows_http_errors(self):
        """HTTP dry_run 抛异常 → dual-run 静默吞掉，主路径正常返回"""
        from unittest.mock import patch

        sp = _StubSign(ready=True)
        fb = _StubFallback()
        t = HttpProtocolTransport(sign_provider=sp, fallback=fb)
        t._http_scan_inbox_dual_run = True

        async def boom(account, **kwargs):
            raise RuntimeError("network down")
        t._impl_scan_inbox_via_http = boom

        class _Acc:
            id = "acc-dual-err"
            sec_uid = "ms_x"

        with patch.object(
            __import__("core.douyin.runtime.transport.http_protocol", fromlist=["logger"]).logger,
            "warning",
        ) as log_warn:
            result = await t.scan_inbox(_Acc(), max_conversations=5)
            import asyncio as _aio
            await _aio.sleep(0)
            await _aio.sleep(0)

        # 主路径仍然返回 fallback 的值
        self.assertEqual(result, ["fallback-msg"])
        # 至少有一条 warning 提到 dual_run
        warn_msgs = [
            (call.args[0] if call.args else "")
            for call in log_warn.call_args_list
        ]
        self.assertTrue(
            any("dual_run" in str(m) for m in warn_msgs),
            f"未看到 dual_run warning，actual={warn_msgs}",
        )


if __name__ == "__main__":
    unittest.main()
