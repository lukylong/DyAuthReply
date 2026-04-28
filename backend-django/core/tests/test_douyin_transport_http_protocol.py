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
            "acc-uuid-http-proto", "0:1:80549827440:3061476426516824", "你好"
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

    async def test_impl_send_text_http_4xx_falls_back(self):
        """HTTP 状态非 2xx → 协议路径抛 RuntimeError → 上层 catch → fallback"""
        from core.douyin.runtime.transport.sign_provider import SignedResponse

        async def fake_signed_fetch(*args, **kwargs):
            return SignedResponse(
                status=429, url="x", headers={}, text="rate limit", content=b"oops"
            )

        sp = self._make_sign_provider(signed_fetch_mock=fake_signed_fetch)
        fb = _StubFallback()
        t = HttpProtocolTransport(sign_provider=sp, fallback=fb, send_text_enabled=True)

        msg_id = await t.send_text(
            _FakeAccount(), object(), conversation_id="c", text="hi"
        )
        self.assertEqual(msg_id, "fallback-text-id")
        self.assertEqual(fb.send_text_calls, 1)

    async def test_impl_send_text_protocol_status_nonzero_falls_back(self):
        """HTTP 200 但协议层 status_code != 0 → fallback"""
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

        msg_id = await t.send_text(
            _FakeAccount(), object(), conversation_id="c", text="hi"
        )
        self.assertEqual(msg_id, "fallback-text-id")
        self.assertEqual(fb.send_text_calls, 1)

    async def test_impl_send_text_decode_failure_falls_back(self):
        """响应不是合法 protobuf → 解码异常 → fallback"""
        from core.douyin.runtime.transport.sign_provider import SignedResponse

        async def fake_signed_fetch(*args, **kwargs):
            # 完全乱码，无法解 varint
            return SignedResponse(
                status=200, url="x", headers={}, text="", content=b"\x80\x80\x80\x80"
            )

        sp = self._make_sign_provider(signed_fetch_mock=fake_signed_fetch)
        fb = _StubFallback()
        t = HttpProtocolTransport(sign_provider=sp, fallback=fb, send_text_enabled=True)

        msg_id = await t.send_text(
            _FakeAccount(), object(), conversation_id="c", text="hi"
        )
        self.assertEqual(msg_id, "fallback-text-id")
        self.assertEqual(fb.send_text_calls, 1)

    async def test_impl_send_text_empty_text_raises_value_error(self):
        """空 text 触发 ValueError，不应该走 fallback（这是上层 bug）"""
        sp = self._make_sign_provider(signed_fetch_mock=AsyncMock())
        fb = _StubFallback()
        t = HttpProtocolTransport(sign_provider=sp, fallback=fb, send_text_enabled=True)

        with self.assertRaises(ValueError):
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


if __name__ == "__main__":
    unittest.main()
