"""2026-08 DouYin_Spider PC IM 协议同步回归测试。"""
from __future__ import annotations

import base64
import asyncio
import hashlib
import json
import unittest
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.test import TestCase, override_settings
from django.utils import timezone

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
from core.douyin.runtime.transport.frontier_ws import (
    FrontierImWsClient,
    FrontierWsDecorator,
    _stable_jitter_factor,
)
from core.douyin.runtime.transport.js_sign_provider import JsSignProvider
from core.douyin.runtime.transport.sign import js_signer
from core.douyin.runtime.transport.sign import secsdk_web_sign
from core.douyin.runtime.transport.sign.bd_ticket import derive_ecdh_key
from core.douyin.runtime.transport.sign.dtrait import build_session_dtrait
from core.douyin.runtime.transport.sign_types import (
    LoginExpiredError,
    SendRiskControlError,
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

        self.assertEqual(IM_SDK_VERSION, "0.1.8")
        self.assertEqual(IM_BUILD_NUMBER, "0d50935:feat/pc-im-groupB")
        self.assertEqual(req.version_code, "360000")
        self.assertEqual(req.token, "")
        self.assertEqual(req.ts_sign, "")
        self.assertEqual(req.sdk_cert, "")
        self.assertEqual(req.reuqest_sign, "")
        self.assertEqual(req.device_platform, "douyin_pc")
        self.assertEqual(req.auth_type, 4)
        self.assertEqual(req.biz, "douyin_web")
        self.assertEqual(req.access, "web_sdk")
        self.assertEqual(req.headers["app_name"], "douyin_pc")
        self.assertEqual(req.headers["is-retry"], "0")
        self.assertNotIn("webid", req.headers)
        self.assertNotIn("fp", req.headers)
        self.assertIn("user_agent", req.headers)
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

    def test_send_envelope_includes_reference_browser_fingerprint(self):
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
        self.assertEqual(req.headers["user_agent"], ua)
        self.assertEqual(req.headers["browser_platform"], "Win32")
        self.assertEqual(req.headers["browser_version"], ua.replace("Mozilla/", "", 1))

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
        self.assertEqual(
            url,
            "https://imapi.douyin.com/v1/message/send"
            "?msToken=TOKEN&a_bogus=AB&verifyFp=FP&fp=FP",
        )
        headers = kwargs["headers"]
        self.assertEqual(headers["user-agent"], provider._user_agent)
        self.assertEqual(headers["bd-ticket-guard-client-data"], "CLIENT")
        self.assertEqual(headers["bd-ticket-guard-ree-public-key"], "REE")
        self.assertEqual(headers["bd-ticket-guard-version"], "2")
        self.assertEqual(headers["bd-ticket-guard-web-version"], "1")
        self.assertEqual(headers["bd-ticket-guard-web-sign-type"], "0")

    async def test_reference_send_uses_www_cookie_header(self):
        provider = JsSignProvider()
        provider._client = _FakeHttpClient()
        provider._ready = True
        provider._cookies = {"msToken": "FLAT", "sessionid": "flat-session"}
        provider._cookie_headers = {
            "www.douyin.com": "sessionid=www-session; msToken=WWW; same=first; same=last"
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
            "sessionid=www-session; msToken=WWW; same=first; same=last",
        )

    async def test_identity_injects_path_bound_dtrait_and_www_cookie(self):
        provider = JsSignProvider()
        provider._client = _FakeHttpClient()
        provider._ready = True
        provider._cookies = {"msToken": "FLAT"}
        provider._cookie_headers = {
            "www.douyin.com": "sessionid=www-session; msToken=WWW"
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
        self.assertIn("msToken=WWW", url)
        self.assertEqual(kwargs["headers"]["cookie"],
                         "sessionid=www-session; msToken=WWW")
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
        self.assertEqual(signer.domain_contains, "www.douyin.com")
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


class PeerProfileCacheTests(unittest.IsolatedAsyncioTestCase):
    @override_settings(
        DOUYIN_PROFILE_CACHE_TTL_S=3600,
        DOUYIN_PROFILE_NEGATIVE_CACHE_TTL_S=300,
    )
    async def test_positive_and_negative_profile_results_are_cached(self):
        transport = HttpProtocolTransport(sign_provider=SimpleNamespace())
        account = SimpleNamespace(id="account-profile")
        fetch = AsyncMock(return_value={"nickname": "用户A", "avatar": "a.jpg"})
        transport._fetch_peer_profile_by_sec_uid = fetch

        first = await transport._resolve_user_details_by_sec_uids(account, ["sec-a"])
        second = await transport._resolve_user_details_by_sec_uids(account, ["sec-a"])

        self.assertEqual(first["sec-a"]["nickname"], "用户A")
        self.assertEqual(second, first)
        self.assertEqual(fetch.await_count, 1)

        missing = AsyncMock(return_value=None)
        transport._fetch_peer_profile_by_sec_uid = missing
        self.assertEqual(
            await transport._resolve_user_details_by_sec_uids(account, ["sec-missing"]),
            {},
        )
        self.assertEqual(
            await transport._resolve_user_details_by_sec_uids(account, ["sec-missing"]),
            {},
        )
        self.assertEqual(missing.await_count, 1)

    async def test_concurrent_profile_requests_are_coalesced(self):
        transport = HttpProtocolTransport(sign_provider=SimpleNamespace())
        account = SimpleNamespace(id="account-profile")
        started = asyncio.Event()
        release = asyncio.Event()
        call_count = 0

        async def _fetch(_account, _sec_uid):
            nonlocal call_count
            call_count += 1
            started.set()
            await release.wait()
            return {"nickname": "用户B", "avatar": ""}

        transport._fetch_peer_profile_by_sec_uid = _fetch
        first = asyncio.create_task(
            transport._resolve_user_details_by_sec_uids(account, ["sec-b"])
        )
        await started.wait()
        second = asyncio.create_task(
            transport._resolve_user_details_by_sec_uids(account, ["sec-b"])
        )
        await asyncio.sleep(0)
        release.set()
        first_result, second_result = await asyncio.gather(first, second)

        self.assertEqual(call_count, 1)
        self.assertEqual(first_result, second_result)
        self.assertEqual(first_result["sec-b"]["nickname"], "用户B")


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

    async def test_7911_raw_check_two_raises_typed_send_risk_control_error(self):
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
            server_msg_id=0,
            client_msg_id="",
            biz_status_code=7911,
            biz_status_text="系统繁忙，重新登录后可以正常使用私信功能",
            biz_raw_check_code=2,
        )

        with patch(
            "core.douyin.runtime.transport.http_protocol.decode_send_message_response",
            return_value=decoded,
        ):
            with self.assertRaises(SendRiskControlError) as raised:
                await transport._post_send_message(
                    SimpleNamespace(id="account-risk"),
                    "0:1:test:peer",
                    "风控测试",
                    log_tag="send_text",
                )

        self.assertEqual(raised.exception.biz_status_code, 7911)
        self.assertEqual(raised.exception.raw_check_code, 2)
        self.assertIn("系统繁忙", str(raised.exception))

    async def test_send_text_keeps_typed_risk_error_for_worker(self):
        signer = _FakeSendSignProvider()
        transport = HttpProtocolTransport(
            sign_provider=signer,
            send_text_enabled=True,
        )
        transport._http_send_strict = True
        risk = SendRiskControlError(
            "send_text business status=7911 raw_check_code=2",
            biz_status_code=7911,
            raw_check_code=2,
        )
        transport._impl_send_text_via_http = AsyncMock(side_effect=risk)

        with self.assertRaises(SendRiskControlError) as raised:
            await transport.send_text(
                SimpleNamespace(id="account-risk"),
                None,
                conversation_id="conversation-risk",
                text="风控测试",
            )

        self.assertIs(raised.exception, risk)

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
        self.assertIn("user_agent", request.headers)
        self.assertEqual(request.device_platform, "douyin_pc")
        self.assertEqual(
            signer.calls[0]["post_sign_params"],
            {"verifyFp": "verify-fp", "fp": "verify-fp"},
        )
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
        transport.remember_inbound_message_context(
            SimpleNamespace(
                conversation_id="0:1:test:peer",
                conversation_short_id=987,
            )
        )

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

    async def test_send_context_uses_fresh_persisted_ticket_without_network(self):
        signer = _FakeSendSignProvider()
        signer.signed_fetch = AsyncMock()
        transport = HttpProtocolTransport(sign_provider=signer)
        load_context = AsyncMock(
            return_value=(987, "persisted-ticket", timezone.now())
        )

        with patch(
            "core.douyin.runtime.message_store.load_conversation_send_context",
            new=load_context,
        ):
            context = await transport._resolve_send_conversation_context(
                SimpleNamespace(id="account-test"),
                "0:1:test:peer",
            )

        self.assertEqual(context, (987, "persisted-ticket"))
        signer.signed_fetch.assert_not_awaited()
        load_context.assert_awaited_once_with("account-test", "0:1:test:peer")

    async def test_persisted_short_id_skips_wide_get_by_user_and_saves_ticket(self):
        signer = _FakeSendSignProvider()
        response = _conversation_info_response(
            conversation_id="0:1:test:peer",
            conversation_short_id=987,
            ticket="refreshed-ticket",
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
        load_context = AsyncMock(return_value=(987, "", None))
        save_context = AsyncMock(return_value=True)

        with (
            patch(
                "core.douyin.runtime.message_store.load_conversation_send_context",
                new=load_context,
            ),
            patch(
                "core.douyin.runtime.message_store.save_conversation_send_context",
                new=save_context,
            ),
        ):
            context = await transport._resolve_send_conversation_context(
                SimpleNamespace(id="account-test"),
                "0:1:test:peer",
            )

        self.assertEqual(context, (987, "refreshed-ticket"))
        self.assertEqual(signer.signed_fetch.await_count, 1)
        self.assertTrue(
            signer.signed_fetch.await_args.kwargs["url"].endswith(
                "/v2/conversation/get_info_list"
            )
        )
        save_context.assert_awaited_once_with(
            "account-test",
            "0:1:test:peer",
            987,
            "refreshed-ticket",
        )

    async def test_send_context_refresh_is_singleflight_per_conversation(self):
        signer = _FakeSendSignProvider()
        response = _conversation_info_response(
            conversation_id="0:1:test:peer",
            conversation_short_id=987,
            ticket="shared-ticket",
        )

        async def _delayed_fetch(**kwargs):
            signer.calls.append(kwargs)
            await asyncio.sleep(0.01)
            return SignedResponse(
                status=200,
                url=kwargs["url"],
                headers={},
                text="",
                content=response,
            )

        signer.signed_fetch = _delayed_fetch
        transport = HttpProtocolTransport(sign_provider=signer)
        load_context = AsyncMock(return_value=(987, "", None))
        save_context = AsyncMock(return_value=True)
        account = SimpleNamespace(id="account-test")

        with (
            patch(
                "core.douyin.runtime.message_store.load_conversation_send_context",
                new=load_context,
            ),
            patch(
                "core.douyin.runtime.message_store.save_conversation_send_context",
                new=save_context,
            ),
        ):
            first, second = await asyncio.gather(
                transport._resolve_send_conversation_context(
                    account, "0:1:test:peer"
                ),
                transport._resolve_send_conversation_context(
                    account, "0:1:test:peer"
                ),
            )

        self.assertEqual(first, (987, "shared-ticket"))
        self.assertEqual(second, first)
        self.assertEqual(len(signer.calls), 1)
        self.assertEqual(load_context.await_count, 1)
        self.assertEqual(save_context.await_count, 1)

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


class _ContextRecordingInner:
    name = "http_protocol"

    def __init__(self):
        self.remembered = []
        self.profile_calls = 0

    def remember_inbound_message_context(self, message):
        self.remembered.append(message)

    async def _resolve_user_details_by_sec_uids(self, account, sec_uids):
        self.profile_calls += 1
        return {}


class _ScanRecordingInner:
    name = "http_protocol"

    def __init__(self):
        self.scan_calls = []

    async def scan_inbox(self, account, **kwargs):
        self.scan_calls.append((account, kwargs))
        return []


class FrontierSendContextBridgeTests(unittest.IsolatedAsyncioTestCase):
    def test_stable_jitter_is_bounded_and_repeatable(self):
        first = _stable_jitter_factor("account-a", 0.2)
        second = _stable_jitter_factor("account-a", 0.2)
        self.assertEqual(first, second)
        self.assertGreaterEqual(first, 0.8)
        self.assertLessEqual(first, 1.2)

    async def test_frontier_device_id_is_reused_across_reconnects(self):
        client = FrontierImWsClient(
            account_id="account-test",
            cookies={},
            user_agent="test-agent",
            on_inbound=lambda _message: None,
        )
        client._device_id = "cached-device"
        self.assertEqual(await client._get_device_id(), "cached-device")

    async def test_frontier_inbound_uses_bounded_consumer_queue(self):
        transport = FrontierWsDecorator(_ContextRecordingInner())
        transport._account_id = "account-test"
        transport._process_message = AsyncMock(return_value=None)
        processor = asyncio.create_task(transport._consume_inbound())
        try:
            message = SimpleNamespace(server_message_id=1)
            transport._on_inbound(message)
            await asyncio.wait_for(transport._raw_inbound_queue.join(), timeout=1)
            transport._process_message.assert_awaited_once_with(message)
        finally:
            processor.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await processor

    def test_frontier_inbound_overflow_forces_http_reconciliation(self):
        transport = FrontierWsDecorator(_ContextRecordingInner())
        transport._account_id = "account-test"
        transport._raw_inbound_queue = asyncio.Queue(maxsize=1)
        transport._last_http_fallback_at = 999.0
        transport._raw_inbound_queue.put_nowait(SimpleNamespace(server_message_id=1))

        transport._on_inbound(SimpleNamespace(server_message_id=2))

        self.assertEqual(transport._inbound_overflow_count, 1)
        self.assertEqual(transport._last_http_fallback_at, 0.0)
        self.assertTrue(transport._signal.is_set())

    @override_settings(
        DOUYIN_WS_HTTP_FALLBACK_HEALTHY_INTERVAL=300,
        DOUYIN_WS_HTTP_FALLBACK_OFFLINE_INTERVAL=20,
        DOUYIN_WS_HTTP_FALLBACK_JITTER_RATIO=0,
    )
    async def test_http_reconciliation_uses_adaptive_ws_intervals(self):
        inner = _ScanRecordingInner()
        transport = FrontierWsDecorator(inner)
        transport._account_id = "account-test"
        transport._client = SimpleNamespace(connected=True, last_frame_at=0.0)
        transport._last_http_fallback_at = 100.0
        account = SimpleNamespace(id="account-test")

        with patch(
            "core.douyin.runtime.transport.frontier_ws.time.monotonic",
            return_value=399.0,
        ):
            self.assertEqual(await transport.scan_inbox(account), [])
        self.assertEqual(inner.scan_calls, [])

        with patch(
            "core.douyin.runtime.transport.frontier_ws.time.monotonic",
            return_value=400.0,
        ):
            self.assertEqual(await transport.scan_inbox(account), [])
        self.assertEqual(len(inner.scan_calls), 1)

        transport._client.connected = False
        with patch(
            "core.douyin.runtime.transport.frontier_ws.time.monotonic",
            return_value=419.0,
        ):
            self.assertEqual(await transport.scan_inbox(account), [])
        self.assertEqual(len(inner.scan_calls), 1)

        with patch(
            "core.douyin.runtime.transport.frontier_ws.time.monotonic",
            return_value=420.0,
        ):
            self.assertEqual(await transport.scan_inbox(account), [])
        self.assertEqual(len(inner.scan_calls), 2)

    async def test_ws_inbound_primes_send_context_before_worker_dispatch(self):
        inner = _ContextRecordingInner()
        transport = FrontierWsDecorator(inner)
        transport._account_id = "account-test"
        transport._account_sec_uid = "self-sec"
        transport._get_existing_peer_info = AsyncMock(
            return_value=("peer", None)
        )
        transport._get_account_orm = AsyncMock(return_value=None)
        message = SimpleNamespace(
            conversation_id="0:1:123:456",
            conversation_short_id=987,
            server_message_id=12345,
            client_message_id="client-1",
            sender_uid=456,
            sender_sec_uid="peer-sec",
            create_time_us=1_788_430_244_866_124,
            msg_type=1,
            text="hello",
            content_json={},
            content_type="text",
            media=None,
        )

        upsert = AsyncMock(return_value=("db-conv", "db-msg"))
        with patch(
            "core.douyin.runtime.message_store._upsert_conversation_and_message",
            new=upsert,
        ):
            await transport._process_message(message)

        self.assertEqual(inner.remembered, [message])
        self.assertEqual(inner.profile_calls, 0)
        scanned = transport._scanned_messages_queue.get_nowait()
        self.assertEqual(scanned.conversation_id, "db-conv")
        self.assertEqual(scanned.raw["conversation_id"], "0:1:123:456")
        self.assertEqual(scanned.raw["conversation_short_id"], 987)
        self.assertEqual(
            upsert.await_args.kwargs["platform_conversation_short_id"], 987
        )

    async def test_ws_inbound_missing_profile_does_not_call_remote_resolver(self):
        inner = _ContextRecordingInner()
        transport = FrontierWsDecorator(inner)
        transport._account_id = "account-test"
        transport._account_sec_uid = "self-sec"
        transport._get_existing_peer_info = AsyncMock(return_value=(None, None))
        message = SimpleNamespace(
            conversation_id="0:1:123:456",
            conversation_short_id=987,
            server_message_id=12346,
            client_message_id="client-2",
            sender_uid=456,
            sender_sec_uid="peer-sec",
            create_time_us=1_788_430_244_866_124,
            msg_type=1,
            text="hello without profile",
            content_json={},
            content_type="text",
            media=None,
        )

        with patch(
            "core.douyin.runtime.message_store._upsert_conversation_and_message",
            new=AsyncMock(return_value=("db-conv", "db-msg-2")),
        ):
            await transport._process_message(message)

        self.assertEqual(inner.profile_calls, 0)
        scanned = transport._scanned_messages_queue.get_nowait()
        self.assertIsNone(scanned.peer_nickname)


class ConversationSendContextPersistenceTests(TestCase):
    def setUp(self):
        from core.douyin.douyin_account_model import DouyinAccount
        from core.user.user_model import User

        owner = User.objects.create(
            username="send_context_owner",
            password="test-password",
            email="send-context@example.com",
        )
        self.account = DouyinAccount.objects.create(
            nickname="send-context-account",
            owner=owner,
            sec_uid="self-send-context",
            status=1,
        )

    def test_inbound_upsert_persists_short_id_and_ticket_helper_is_account_bound(self):
        from core.douyin.douyin_conversation_model import DouyinConversation
        from core.douyin.runtime.message_store import (
            _upsert_conversation_and_message,
            load_conversation_send_context,
            save_conversation_send_context,
        )

        platform_id = "0:1:10001:20002"
        created = _upsert_conversation_and_message.func(
            str(self.account.id),
            "peer-send-context",
            "peer",
            "hello",
            timezone.now(),
            {"conversation_id": platform_id, "conversation_short_id": 987},
            external_msg_id="srv_100",
            platform_conversation_id=platform_id,
            platform_conversation_short_id=987,
        )
        self.assertIsNotNone(created)
        conv = DouyinConversation.objects.get(account=self.account)
        self.assertEqual(conv.platform_conversation_short_id, 987)

        self.assertTrue(
            save_conversation_send_context.func(
                str(self.account.id), platform_id, 987, "stored-ticket"
            )
        )
        short_id, ticket, updated_at = load_conversation_send_context.func(
            str(self.account.id), platform_id
        )
        self.assertEqual((short_id, ticket), (987, "stored-ticket"))
        self.assertIsNotNone(updated_at)
        self.assertEqual(
            load_conversation_send_context.func("other-account", platform_id),
            (0, "", None),
        )

    def test_short_id_change_invalidates_persisted_ticket(self):
        from core.douyin.douyin_conversation_model import DouyinConversation
        from core.douyin.runtime.message_store import (
            _upsert_conversation_and_message,
            save_conversation_send_context,
        )

        platform_id = "0:1:10003:20004"
        common = {
            "account_id": str(self.account.id),
            "peer_sec_uid": "peer-short-change",
            "peer_nickname": "peer",
            "received_at": timezone.now(),
            "platform_conversation_id": platform_id,
        }
        _upsert_conversation_and_message.func(
            text="first",
            raw={},
            external_msg_id="srv_101",
            platform_conversation_short_id=987,
            **common,
        )
        self.assertTrue(
            save_conversation_send_context.func(
                str(self.account.id), platform_id, 987, "old-ticket"
            )
        )
        _upsert_conversation_and_message.func(
            text="second",
            raw={},
            external_msg_id="srv_102",
            platform_conversation_short_id=988,
            **common,
        )

        conv = DouyinConversation.objects.get(account=self.account)
        self.assertEqual(conv.platform_conversation_short_id, 988)
        self.assertIsNone(conv.platform_conversation_ticket)
        self.assertIsNone(conv.platform_conversation_ticket_updated_at)


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
