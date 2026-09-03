"""2026-08 DouYin_Spider PC IM 协议同步回归测试。"""
from __future__ import annotations

import base64
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.douyin.runtime.transport.http_protocol import (
    HttpProtocolTransport,
    IDENTITY_SECURITY_URL,
    identity_security_base_params,
)
from core.douyin.runtime.transport.js_sign_provider import JsSignProvider
from core.douyin.runtime.transport.sign import js_signer
from core.douyin.runtime.transport.sign_types import SignedResponse
from core.douyin.runtime.transport.wire import dy_request_pb2 as R
from core.douyin.runtime.transport.wire.im_send_pb2 import (
    IM_BUILD_NUMBER,
    IM_SDK_VERSION,
    encode_send_message_request_pb2,
)


class ProtocolEnvelopeTests(unittest.TestCase):
    def test_send_envelope_matches_current_pc_im(self):
        body, client_id, _seq = encode_send_message_request_pb2(
            conversation_id="0:1:123:456",
            conversation_short_id=987,
            ticket="conversation-ticket",
            text="你好",
            bd_ticket={"private_key": "ignored-in-envelope"},
            client_msg_id="client-1",
            identity_security_token="identity-token",
            identity_security_device_id="device-1",
            mentioned_users=[456],
            ext={"custom": "value"},
        )
        req = R.Request()
        req.ParseFromString(body)

        self.assertEqual(IM_SDK_VERSION, "0.1.8")
        self.assertEqual(IM_BUILD_NUMBER, "0d50935:feat/pc-im-groupB")
        self.assertEqual(req.version_code, "360000")
        self.assertEqual(req.token, "")
        self.assertEqual(req.ts_sign, "")
        self.assertEqual(req.sdk_cert, "")
        self.assertEqual(req.reuqest_sign, "")
        self.assertEqual(req.headers["referer"], "https://www.douyin.com/jingxuan")
        self.assertEqual(req.headers["timezone_name"], "Asia/Shanghai")
        self.assertNotIn("webid", req.headers)
        self.assertNotIn("fp", req.headers)
        self.assertEqual(
            req.headers["identity_security_token"], '{"token":"identity-token"}'
        )
        self.assertEqual(req.headers["identity_security_device_id"], "device-1")
        self.assertEqual(req.headers["identity_security_aid"], "")

        send = req.body.send_message_body
        self.assertEqual(client_id, "client-1")
        self.assertEqual(
            json.loads(send.content),
            {"aweType": 700, "type": 0, "richTextInfos": [], "text": "你好"},
        )
        self.assertEqual(send.conversation_short_id, 987)
        self.assertEqual(send.ticket, "conversation-ticket")
        self.assertEqual(list(send.mentioned_users), [456])
        self.assertEqual(
            [item.key for item in send.ext],
            ["s:mentioned_users", "s:client_message_id", "custom", "s:stime"],
        )
        self.assertRegex(send.ext[-1].value, r"^\d{13}\.\d{5}$")

    def test_bd_client_data_uses_standard_base64(self):
        with patch.object(js_signer, "get_req_sign", return_value="💥"):
            encoded = js_signer.build_bd_ticket_client_data(
                "/v1/message/send", "ticket", "ts.1.sample", "private", timestamp=123
            )
        raw = base64.b64decode(encoded, validate=True).decode("utf-8")
        self.assertEqual(
            json.loads(raw),
            {
                "ts_sign": "ts.1.sample",
                "req_content": "ticket,path,timestamp",
                "req_sign": "💥",
                "timestamp": 123,
            },
        )


class _FakeHttpResponse:
    status_code = 200
    url = "https://imapi.douyin.com/v1/message/send"
    headers = {"content-type": "application/x-protobuf"}
    content = b"OK"


class _FakeHttpClient:
    def __init__(self):
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return _FakeHttpResponse()


class JsSignProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_query_order_and_full_ticket_headers(self):
        provider = JsSignProvider()
        provider._client = _FakeHttpClient()
        provider._ready = True
        provider._cookies = {"msToken": "TOKEN"}
        provider._bd_ticket = {
            "private_key": "PRIVATE",
            "ticket": "TICKET",
            "ts_sign": "ts.1.SIGN",
        }
        with (
            patch.object(js_signer, "get_ab", return_value="AB"),
            patch.object(js_signer, "build_bd_ticket_client_data", return_value="CLIENT"),
            patch.object(js_signer, "get_ree_key", return_value="REE"),
        ):
            await provider.signed_fetch(
                "POST",
                "https://imapi.douyin.com/v1/message/send",
                body=b"\x08\x64",
                base_params="",
                post_sign_params={"verifyFp": "FP", "fp": "FP"},
            )

        _method, url, kwargs = provider._client.calls[0]
        self.assertTrue(url.endswith("?msToken=TOKEN&a_bogus=AB&verifyFp=FP&fp=FP"), url)
        headers = kwargs["headers"]
        self.assertEqual(headers["bd-ticket-guard-client-data"], "CLIENT")
        self.assertEqual(headers["bd-ticket-guard-ree-public-key"], "REE")
        self.assertEqual(headers["bd-ticket-guard-version"], "2")
        self.assertEqual(headers["bd-ticket-guard-web-version"], "1")
        self.assertEqual(headers["bd-ticket-guard-web-sign-type"], "0")


class _FakeIdentitySignProvider:
    is_ready = True

    def __init__(self):
        self.calls = []

    async def get_cookies(self):
        return {"passport_csrf_token": "CSRF"}

    async def signed_fetch(self, **kwargs):
        self.calls.append(kwargs)
        payload = {
            "message": "success",
            "data": {"identity_security_token": "IDENTITY", "device_id": "DEVICE"},
        }
        raw = json.dumps(payload).encode()
        return SignedResponse(
            status=200,
            url=IDENTITY_SECURITY_URL,
            headers={},
            text=raw.decode(),
            content=raw,
        )


class IdentitySecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_identity_token_shape_and_cache(self):
        signer = _FakeIdentitySignProvider()
        transport = HttpProtocolTransport(sign_provider=signer)
        account = SimpleNamespace(id="account-1")
        first = await transport._get_identity_security_token(account)
        second = await transport._get_identity_security_token(account)

        self.assertEqual(first, ("IDENTITY", "DEVICE"))
        self.assertEqual(second, first)
        self.assertEqual(len(signer.calls), 1)
        call = signer.calls[0]
        self.assertEqual(call["url"], IDENTITY_SECURITY_URL)
        self.assertEqual(call["headers"]["x-tt-passport-csrf-token"], "CSRF")
        self.assertRegex(call["base_params"], r"biz_trace_id=[0-9a-f]{8}")
        self.assertNotIn("msToken=", call["base_params"])
        self.assertNotIn("verifyFp=", call["base_params"])

    def test_identity_base_param_order(self):
        params = identity_security_base_params("deadbeef")
        self.assertEqual(
            params.split("&"),
            [
                "passport_jssdk_version=4.2.3",
                "passport_jssdk_type=lite",
                "is_from_ttaccountsdk=1",
                "aid=6383",
                "language=zh",
                "scene=web_im",
                "auto_retry_req=0",
                "skip_verify=false",
                "identity_token_force_get_tag=0",
                "biz_trace_id=deadbeef",
                "id_token_version=1.2.10",
            ],
        )


class _FakeSendSignProvider:
    is_ready = True

    def __init__(self):
        self.calls = []

    def get_bd_ticket(self):
        return {"private_key": "PRIVATE", "ticket": "TICKET", "ts_sign": "SIGN"}

    async def get_cookies(self):
        return {"s_v_web_id": "verify-fp"}

    async def signed_fetch(self, **kwargs):
        self.calls.append(kwargs)
        return SignedResponse(
            status=200,
            url=kwargs["url"],
            headers={},
            text="ok",
            content=b"ok",
        )


class SendMessageRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_post_send_reaches_signed_transport_after_protocol_sync(self):
        """协议同步后，发送日志不能再引用已移除的 template_body 局部变量。"""
        signer = _FakeSendSignProvider()
        transport = HttpProtocolTransport(sign_provider=signer)
        transport._get_identity_security_token = AsyncMock(
            return_value=("IDENTITY", "DEVICE")
        )
        decoded = SimpleNamespace(
            status_code=0,
            status_msg="OK",
            server_msg_id=123,
            client_msg_id="",
            biz_status_code=0,
            biz_status_text="",
            biz_raw_check_code=0,
        )

        with (
            patch(
                "core.douyin.runtime.transport.http_protocol.decode_send_message_response",
                return_value=decoded,
            ),
            patch(
                "core.douyin.runtime.send_template_cache.save_cached_send_template"
            ),
        ):
            result, client_msg_id = await transport._post_send_message(
                SimpleNamespace(id="account-test"),
                "0:1:test:peer",
                "https://card.example/c/1",
                log_tag="card_reply",
            )

        self.assertIs(result, decoded)
        self.assertTrue(client_msg_id)
        self.assertEqual(len(signer.calls), 1)
        self.assertEqual(
            signer.calls[0]["url"],
            "https://imapi.douyin.com/v1/message/send",
        )
