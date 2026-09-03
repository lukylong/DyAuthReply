"""2026-08 DouYin_Spider PC IM 协议同步回归测试。"""
from __future__ import annotations

import base64
import hashlib
import json
import unittest
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from core.douyin.runtime.transport.browser_fingerprint import browser_fingerprint
from core.douyin.runtime.transport.douyin_web_profile import (
    _signed_web_get,
    _web_platform_params,
    ensure_web_cookie_fields,
)
from core.douyin.runtime.transport.http_protocol import (
    HttpProtocolTransport,
    IDENTITY_SECURITY_URL,
    _format_send_business_failure,
    identity_security_base_params,
)
from core.douyin.runtime.transport.js_sign_provider import JsSignProvider
from core.douyin.runtime.transport.sign import js_signer
from core.douyin.runtime.transport.sign import secsdk_web_sign
from core.douyin.runtime.transport.sign.bd_ticket import derive_ecdh_key
from core.douyin.runtime.transport.sign.dtrait import build_session_dtrait
from core.douyin.runtime.transport.sign_types import (
    LoginExpiredError,
    SignedResponse,
    SignerUnavailable,
)
from core.douyin.runtime.transport.wire import dy_request_pb2 as R
from core.douyin.runtime.transport.wire.codec import encode_field, iter_fields
from core.douyin.runtime.transport.wire.im_send_pb2 import (
    decode_get_conversation_info_response_pb2,
    encode_get_conversation_info_request_pb2,
    IM_BUILD_NUMBER,
    IM_SDK_VERSION,
    encode_send_message_request_pb2,
)


class ProtocolEnvelopeTests(unittest.TestCase):
    def test_conversation_info_round_trip_fields(self):
        body, _seq = encode_get_conversation_info_request_pb2(
            conversation_id="0:1:123:456",
            conversation_short_id=987,
        )
        request = R.Request()
        request.ParseFromString(body)
        data = request.body.get_conversation_info_list_v2_body.data
        self.assertEqual(request.cmd, 610)
        self.assertEqual(data.conversation_id, "0:1:123:456")
        self.assertEqual(data.conversation_short_id, 987)
        self.assertEqual(data.conversation_type, 1)

        response = _conversation_info_response(
            conversation_id="0:1:123:456",
            conversation_short_id=987,
            ticket="conversation-ticket",
        )
        decoded = decode_get_conversation_info_response_pb2(response)
        self.assertEqual(decoded.status_code, 0)
        self.assertEqual(decoded.conversation_id, "0:1:123:456")
        self.assertEqual(decoded.conversation_short_id, 987)
        self.assertEqual(decoded.ticket, "conversation-ticket")

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

        self.assertEqual(IM_SDK_VERSION, "0.2.0")
        self.assertEqual(IM_BUILD_NUMBER, "0f64c47:feat/pc-im")
        self.assertEqual(req.version_code, "")
        self.assertEqual(req.token, "")
        self.assertEqual(req.ts_sign, "")
        self.assertEqual(req.sdk_cert, "")
        self.assertEqual(req.reuqest_sign, "")
        self.assertEqual(req.device_platform, "douyin_creator")
        self.assertEqual(req.auth_type, 1)
        self.assertEqual(req.biz, "douyin_creator")
        self.assertEqual(req.access, "web_sdk")
        self.assertEqual(req.headers["app_name"], "douyin_creator")
        self.assertEqual(req.headers["is-retry"], "0")
        self.assertNotIn("webid", req.headers)
        self.assertNotIn("fp", req.headers)
        self.assertNotIn("user_agent", req.headers)
        self.assertEqual(
            req.headers["identity_security_token"], '{"token":"identity-token"}'
        )
        self.assertEqual(req.headers["identity_security_device_id"], "device-1")
        self.assertEqual(req.headers["identity_security_aid"], "2906")

        send = req.body.send_message_body
        self.assertEqual(client_id, "client-1")
        self.assertEqual(
            json.loads(send.content),
            {"text": "你好", "aweType": 774},
        )
        self.assertEqual(send.conversation_short_id, 987)
        self.assertEqual(send.ticket, "conversation-ticket")
        self.assertEqual(list(send.mentioned_users), [456])
        self.assertEqual(
            [item.key for item in send.ext],
            ["s:mentioned_users", "s:client_message_id", "custom", "s:stime"],
        )
        self.assertRegex(send.ext[-1].value, r"^\d{13}\.\d{1,5}$")

        # Chromium still emits selected proto3 default fields on the wire. A
        # normal protobuf re-serialize would silently drop these tags.
        top_fields = list(iter_fields(body))
        self.assertEqual([number for number, _wire, _value in top_fields[:9]],
                         [1, 2, 3, 4, 5, 6, 7, 8, 9])
        self.assertEqual(top_fields[3][2], b"")  # token
        self.assertEqual(top_fields[5][2], 0)    # inbox_type
        self.assertEqual(top_fields[8][2], b"")  # device_id

    def test_send_envelope_keeps_browser_fingerprint_at_http_layer(self):
        ua = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/152.0.0.0 Safari/537.36"
        )
        body, _client_id, _seq = encode_send_message_request_pb2(
            conversation_id="0:1:123:456",
            text="你好",
            bd_ticket={},
            user_agent=ua,
        )
        req = R.Request()
        req.ParseFromString(body)
        self.assertNotIn("user_agent", req.headers)
        self.assertNotIn("browser_platform", req.headers)
        self.assertNotIn("browser_version", req.headers)

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

    def test_bd_client_data_uses_hmac_and_trust_cookie(self):
        encoded = js_signer.build_bd_ticket_client_data(
            "/v1/message/send",
            "ticket",
            "ts.1.sample",
            "unused-with-hmac",
            timestamp=123,
            ecdh_key=b"K" * 32,
            t_trust=1,
        )
        payload = json.loads(base64.b64decode(encoded, validate=True))
        self.assertEqual(payload["t_trust"], 1)
        self.assertEqual(
            payload["req_sign"],
            "mwOFOYocayxPUewun/75Hq9iM7cwGpnZNJJzSsH0GJc=",
        )


class BrowserProtocolTests(unittest.IsolatedAsyncioTestCase):
    MAC_UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/152.0.0.0 Safari/537.36"
    )

    def test_browser_fields_follow_imported_user_agent(self):
        fingerprint = browser_fingerprint(self.MAC_UA)
        params = _web_platform_params(user_agent=self.MAC_UA)
        self.assertEqual(fingerprint["browser_version"], "152.0.0.0")
        self.assertEqual(params["browser_platform"], "MacIntel")
        self.assertEqual(params["browser_version"], "152.0.0.0")
        self.assertEqual(params["engine_version"], "152.0.0.0")
        self.assertEqual(params["round_trip_time"], "0")
        self.assertEqual(params["support_h265"], "1")
        self.assertEqual(params["support_dash"], "1")

    def test_cookie_names_are_restored_for_wire_serialization(self):
        cookies = ensure_web_cookie_fields(
            {"uifid": "UF", "uifid_temp": "TMP", "mstoken": "MS", "s_v_web_id": "FP"}
        )
        self.assertEqual(cookies["UIFID"], "UF")
        self.assertEqual(cookies["UIFID_temp"], "TMP")
        self.assertEqual(cookies["msToken"], "MS")
        self.assertNotIn("uifid", cookies)
        self.assertNotIn("mstoken", cookies)

    async def test_profile_request_sends_uifid_in_query_header_and_cookie(self):
        fake_client = _FakeWebClient()
        with (
            patch("httpx.AsyncClient", return_value=fake_client),
            patch.object(js_signer, "get_ab", return_value="AB") as signer,
        ):
            await _signed_web_get(
                "https://www.douyin.com/aweme/v1/web/user/profile/other/",
                {"aid": "6383"},
                cookies={"uifid": "UF", "mstoken": "MS", "s_v_web_id": "FP"},
                user_agent=self.MAC_UA,
                referer="https://www.douyin.com/user/test",
                verify_fp_after_sign=True,
            )

        url, headers = fake_client.calls[0]
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["uifid"], ["UF"])
        self.assertEqual(query["msToken"], ["MS"])
        self.assertEqual(query["verifyFp"], ["FP"])
        self.assertEqual(headers["uifid"], "UF")
        self.assertIn("UIFID=UF", headers["cookie"])
        self.assertNotIn("uifid=UF", headers["cookie"])
        self.assertEqual(headers["sec-ch-ua-platform"], '"macOS"')
        signed_query = signer.call_args.args[0]
        self.assertIn("uifid=UF", signed_query)
        self.assertNotIn("verifyFp=", signed_query)

    def test_secsdk_web_sign_matches_reference_vector(self):
        url = secsdk_web_sign.sign_url(
            "https://www.douyin.com/aweme/v1/web/aweme/post/"
            "?aid=6383&uifid=UF%2B1&a_bogus=AB&verifyFp=FP&fp=FP",
            timestamp=1_700_000_000,
            uifid="UF+1",
        )
        self.assertEqual(
            url,
            "https://www.douyin.com/aweme/v1/web/aweme/post/"
            "?aid=6383&uifid=UF%2B1&a_bogus=AB&verifyFp=FP&fp=FP"
            "&timestamp=1700000000"
            "&x-secsdk-web-signature=77996339d7adaef2cea23a9dcf2f2c0c",
        )

    def test_ecdh_derivation_matches_both_p256_peers(self):
        client = ec.generate_private_key(ec.SECP256R1())
        server = ec.generate_private_key(ec.SECP256R1())
        private_hex = f"{client.private_numbers().private_value:064x}"
        server_point = server.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
        server_cert = "pub." + base64.b64encode(server_point).decode("ascii")
        self.assertEqual(len(derive_ecdh_key(private_hex, server_cert)), 32)

    def test_dtrait_matches_reference_vector(self):
        class DeterministicBytes:
            def __init__(self):
                self.offset = 0

            def __call__(self, size):
                value = bytes(
                    (self.offset + index) % 251 + 1 for index in range(size)
                )
                self.offset += size
                return value

        header, material = build_session_dtrait(
            "/passport/safe/get_identity_security_token/",
            "fixture-device-blob",
            timestamp=1_700_000_000,
            randbytes=DeterministicBytes(),
            return_material=True,
        )
        self.assertEqual(
            hashlib.sha256(header.encode("ascii")).hexdigest(),
            "b56dfbbb32e35aad46891a7751d994b37a840cbff560ce780ed515375d4a4d4b",
        )
        self.assertEqual(header.split("_", 1)[0], "d0")
        self.assertEqual(material[0], "0102030405060708090a0b0c0d0e0f10")
        self.assertEqual(len(material[1]), 256)


class _FakeWebResponse:
    status_code = 200
    text = "{}"


class _FakeWebClient:
    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, *, headers):
        self.calls.append((url, headers))
        return _FakeWebResponse()


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
        self.assertEqual(url, "https://imapi.douyin.com/v1/message/send")
        headers = kwargs["headers"]
        self.assertEqual(headers["user-agent"], provider._user_agent)
        self.assertEqual(headers["bd-ticket-guard-client-data"], "CLIENT")
        self.assertEqual(headers["bd-ticket-guard-ree-public-key"], "REE")
        self.assertEqual(headers["bd-ticket-guard-version"], "2")
        self.assertEqual(headers["bd-ticket-guard-web-version"], "1")
        self.assertEqual(headers["bd-ticket-guard-web-sign-type"], "0")

    async def test_imapi_uses_exact_host_scoped_cookie_header(self):
        provider = JsSignProvider()
        provider._client = _FakeHttpClient()
        provider._ready = True
        provider._cookies = {"msToken": "FLAT", "sessionid": "flat-session"}
        provider._cookie_headers = {
            "imapi.douyin.com": "sessionid=im-session; msToken=IM; same=first; same=last"
        }
        provider._bd_ticket = {
            "private_key": "PRIVATE",
            "ticket": "TICKET",
            "ts_sign": "ts.1.SIGN",
        }
        with (
            patch.object(js_signer, "build_bd_ticket_client_data", return_value="CLIENT"),
            patch.object(js_signer, "get_ree_key", return_value="REE"),
        ):
            await provider.signed_fetch(
                "POST",
                "https://imapi.douyin.com/v1/message/send",
                body=b"\x08\x64",
                base_params="",
            )

        headers = provider._client.calls[0][2]["headers"]
        self.assertEqual(
            headers["cookie"],
            "sessionid=im-session; msToken=IM; same=first; same=last",
        )

    async def test_identity_injects_path_bound_dtrait_and_creator_cookie(self):
        provider = JsSignProvider()
        provider._client = _FakeHttpClient()
        provider._ready = True
        provider._cookies = {"msToken": "FLAT"}
        provider._cookie_headers = {
            "creator.douyin.com": "sessionid=creator-session; msToken=CREATOR"
        }
        provider._dtrait = {"blob": "browser-blob", "header": "", "path": ""}
        material = ("0" * 32, b"K" * 256)
        with (
            patch.object(js_signer, "get_ab", return_value="AB"),
            patch(
                "core.douyin.runtime.transport.js_sign_provider.build_session_dtrait",
                return_value=("d0_DYNAMIC", material),
            ) as build_dtrait,
        ):
            await provider.signed_fetch(
                "GET",
                IDENTITY_SECURITY_URL,
                base_params="aid=2906",
            )

        _method, url, kwargs = provider._client.calls[0]
        self.assertIn("msToken=CREATOR", url)
        self.assertEqual(kwargs["headers"]["cookie"],
                         "sessionid=creator-session; msToken=CREATOR")
        self.assertEqual(kwargs["headers"]["x-tt-session-dtrait"], "d0_DYNAMIC")
        self.assertEqual(
            build_dtrait.call_args.args,
            ("/passport/safe/get_identity_security_token/", "browser-blob"),
        )
        self.assertEqual(provider._dtrait_material, material)

    async def test_current_ticket_session_uses_hmac_and_t_trust(self):
        provider = JsSignProvider()
        provider._client = _FakeHttpClient()
        provider._ready = True
        provider._cookies = {
            "msToken": "TOKEN",
            "bd_ticket_guard_ts_sign_id": "ts.1",
            "_bd_ticket_crypt_cookie": "TRUST",
        }
        provider._bd_ticket = {
            "private_key": "PRIVATE",
            "ticket": "TICKET",
            "ts_sign": "ts.1.SIGN",
            "client_cert": "pub.CLIENT",
        }
        provider._resolve_ecdh_key = AsyncMock(return_value=b"K" * 32)
        with (
            patch.object(js_signer, "get_ab", return_value="AB"),
            patch.object(
                js_signer, "build_bd_ticket_client_data", return_value="CLIENT"
            ) as build_client_data,
            patch.object(js_signer, "get_ree_key", return_value="REE"),
        ):
            await provider.signed_fetch(
                "POST",
                "https://imapi.douyin.com/v1/message/send",
                body=b"\x08\x64",
                base_params="",
            )

        headers = provider._client.calls[0][2]["headers"]
        self.assertEqual(headers["bd-ticket-guard-web-sign-type"], "1")
        self.assertEqual(build_client_data.call_args.kwargs["ecdh_key"], b"K" * 32)
        self.assertEqual(build_client_data.call_args.kwargs["t_trust"], 1)

    async def test_mismatched_ticket_session_is_rejected_before_send(self):
        provider = JsSignProvider()
        provider._client = _FakeHttpClient()
        provider._ready = True
        provider._cookies = {
            "msToken": "TOKEN",
            "bd_ticket_guard_ts_sign_id": "ts.current",
        }
        provider._bd_ticket = {
            "private_key": "PRIVATE",
            "ticket": "TICKET",
            "ts_sign": "ts.stale.SIGN",
            "client_cert": "pub.CLIENT",
        }
        with patch.object(js_signer, "get_ab", return_value="AB"):
            with self.assertRaisesRegex(SignerUnavailable, "不属于同一次登录"):
                await provider.signed_fetch(
                    "POST",
                    "https://imapi.douyin.com/v1/message/send",
                    body=b"\x08\x64",
                    base_params="",
                )
        self.assertEqual(provider._client.calls, [])


class _FakeIdentitySignProvider:
    is_ready = True

    def __init__(self):
        self.calls = []

    async def get_cookies(self, *, domain_contains=None):
        self.domain_contains = domain_contains
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
        self.assertEqual(signer.domain_contains, "creator.douyin.com")
        self.assertRegex(call["base_params"], r"biz_trace_id=[0-9a-f]{8}")
        self.assertNotIn("msToken=", call["base_params"])
        self.assertNotIn("verifyFp=", call["base_params"])

    def test_identity_base_param_order(self):
        params = identity_security_base_params("deadbeef")
        self.assertEqual(
            params.split("&"),
            [
                "passport_jssdk_version=5.1.4",
                "passport_jssdk_type=lite",
                "is_from_ttaccountsdk=1",
                "aid=2906",
                "language=zh",
                "account_app_language=zh-CN",
                "scene=im_send_msg",
                "auto_retry_req=0",
                "skip_verify=false",
                "identity_token_force_get_tag=0",
                "biz_trace_id=deadbeef",
                "id_token_version=2.1.5",
            ],
        )


class _FakeSendSignProvider:
    is_ready = True
    _user_agent = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/152.0.0.0 Safari/537.36"
    )

    def __init__(self):
        self.calls = []

    def get_bd_ticket(self):
        return {"private_key": "PRIVATE", "ticket": "TICKET", "ts_sign": "SIGN"}

    async def get_cookies(self, *, domain_contains=None):
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
    def test_8610_raw_check_two_is_platform_risk_not_cookie_expiry(self):
        result = SimpleNamespace(
            status_code=0,
            status_msg="OK",
            server_msg_id=0,
            client_msg_id="client-1",
            biz_status_code=8610,
            biz_status_text="",
            biz_raw_check_code=2,
        )

        message = _format_send_business_failure(result, "send_reply")

        self.assertIn("抖音平台发送风控拦截", message)
        self.assertIn("business status=8610", message)
        self.assertIn("raw_check_code=2", message)
        self.assertNotIn("Cookie 失效", message)
        self.assertNotIn("msg=OK", message)

    async def test_post_send_reaches_signed_transport_after_protocol_sync(self):
        """协议同步后，发送日志不能再引用已移除的 template_body 局部变量。"""
        signer = _FakeSendSignProvider()
        transport = HttpProtocolTransport(sign_provider=signer)
        transport._get_identity_security_token = AsyncMock(
            return_value=("IDENTITY", "DEVICE")
        )
        transport._resolve_send_conversation_context = AsyncMock(
            return_value=(987, "conversation-ticket")
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
        request = R.Request()
        request.ParseFromString(signer.calls[0]["body"])
        self.assertNotIn("user_agent", request.headers)
        self.assertEqual(request.device_platform, "douyin_creator")
        self.assertEqual(request.body.send_message_body.conversation_short_id, 987)
        self.assertEqual(request.body.send_message_body.ticket, "conversation-ticket")

    async def test_send_context_uses_cached_short_id_and_fetches_ticket(self):
        signer = _FakeSendSignProvider()
        response = _conversation_info_response(
            conversation_id="0:1:test:peer",
            conversation_short_id=987,
            ticket="conversation-ticket",
        )
        signer.signed_fetch = AsyncMock(
            return_value=SignedResponse(
                status=200,
                url="https://imapi.douyin.com/v2/conversation/get_info_list",
                headers={},
                text="",
                content=response,
            )
        )
        transport = HttpProtocolTransport(sign_provider=signer)
        transport._conversation_send_context_cache["0:1:test:peer"] = (987, "", 1.0)

        context = await transport._resolve_send_conversation_context(
            SimpleNamespace(id="account-test"),
            "0:1:test:peer",
        )

        self.assertEqual(context, (987, "conversation-ticket"))
        call = signer.signed_fetch.call_args.kwargs
        self.assertEqual(
            call["url"],
            "https://imapi.douyin.com/v2/conversation/get_info_list",
        )
        request = R.Request()
        request.ParseFromString(call["body"])
        self.assertEqual(request.cmd, 610)

    async def test_send_context_ticket_login_expired_signal_raises_login_expired_error(self):
        """get_conversation_info 返回登录失效强信号时，应抛 LoginExpiredError 而非普通 RuntimeError。

        回归 09-03-login-expiry-detection-and-probe：修复前 `_resolve_send_conversation_context`
        对协议层失败直接抛裸 RuntimeError，导致 worker._send_manual_reply 的
        `except LoginExpiredError` 分支永远命中不了，账号不会被正确打回。
        """
        signer = _FakeSendSignProvider()
        response = _conversation_info_response(
            conversation_id="0:1:test:peer",
            conversation_short_id=987,
            ticket="",
            status_code=1,
            status_msg="unexepcted session length",
        )
        signer.signed_fetch = AsyncMock(
            return_value=SignedResponse(
                status=200,
                url="https://imapi.douyin.com/v2/conversation/get_info_list",
                headers={},
                text="",
                content=response,
            )
        )
        transport = HttpProtocolTransport(sign_provider=signer)
        transport._conversation_send_context_cache["0:1:test:peer"] = (987, "", 1.0)

        with self.assertRaises(LoginExpiredError):
            await transport._resolve_send_conversation_context(
                SimpleNamespace(id="account-test"),
                "0:1:test:peer",
            )

    async def test_send_context_get_by_user_login_expired_signal_raises_login_expired_error(self):
        """get_by_user（首次拿 short_id）返回登录失效强信号时，同样应抛 LoginExpiredError。"""
        signer = _FakeSendSignProvider()

        async def _fake_signed_fetch(**kwargs):
            signer.calls.append(kwargs)
            if kwargs["url"].endswith("/v1/message/get_by_user"):
                envelope = b"".join(
                    [
                        encode_field(1, 610),
                        encode_field(3, 1),
                        encode_field(4, "unexepcted session length"),
                    ]
                )
                return SignedResponse(
                    status=200,
                    url=kwargs["url"],
                    headers={},
                    text="",
                    content=envelope,
                )
            raise AssertionError(f"unexpected url {kwargs['url']}")

        signer.signed_fetch = _fake_signed_fetch
        transport = HttpProtocolTransport(sign_provider=signer)

        with self.assertRaises(LoginExpiredError):
            await transport._resolve_send_conversation_context(
                SimpleNamespace(id="account-test"),
                "0:1:test:peer",
            )


def _conversation_info_response(
    *,
    conversation_id: str,
    conversation_short_id: int,
    ticket: str,
    status_code: int = 0,
    status_msg: str = "OK",
) -> bytes:
    info = b"".join(
        [
            encode_field(1, conversation_id),
            encode_field(2, conversation_short_id),
            encode_field(4, ticket),
        ]
    )
    wrapper = encode_field(1, info)
    body = encode_field(610, wrapper)
    return b"".join(
        [
            encode_field(1, 610),
            encode_field(3, status_code),
            encode_field(4, status_msg),
            encode_field(6, body),
        ]
    )
