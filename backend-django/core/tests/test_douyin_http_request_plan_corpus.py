"""Shared offline corpus for the PC IM HTTP request-planning boundary."""
from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.douyin.runtime.transport.js_sign_provider import JsSignProvider
from core.douyin.runtime.transport.request_plan import (
    PLAN_DIGEST_ALGORITHM,
    RequestPlanError,
    SignerOutputs,
    finalize_reference_send_request,
    parse_cookie_lookup,
    prepare_reference_send_request,
)
from core.douyin.runtime.transport.sign import js_signer
from core.douyin.runtime.transport.sign_types import SignerUnavailable


ROOT = Path(__file__).resolve().parents[3]
CORPUS_PATH = ROOT / "protocol-fixtures" / "douyin_pc_im_http_plan_v1.json"
WIRE_CORPUS_PATH = ROOT / "protocol-fixtures" / "douyin_pc_im_v1.json"
WIRE_CORPUS_SHA256 = (
    "043e92fc54582c16b9baab50f6c106776489f443ccb71f2862647b17200fa234"
)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _body_from_spec(spec: dict, wire_cases: dict[str, bytes]) -> bytes:
    if set(spec) == {"wire_request_case"}:
        try:
            return wire_cases[str(spec["wire_request_case"])]
        except KeyError as exc:
            raise RequestPlanError(
                "invalid_body_reference", "unknown wire request case"
            ) from exc
    if set(spec) == {"hex"}:
        try:
            return bytes.fromhex(str(spec["hex"]))
        except ValueError as exc:
            raise RequestPlanError("invalid_body_reference", "invalid body hex") from exc
    if set(spec) == {"repeat_byte_hex", "count"}:
        try:
            unit = bytes.fromhex(str(spec["repeat_byte_hex"]))
            count = int(spec["count"])
        except (TypeError, ValueError) as exc:
            raise RequestPlanError("invalid_body_reference", "invalid repeated body") from exc
        if len(unit) != 1 or count < 0:
            raise RequestPlanError("invalid_body_reference", "invalid repeated body")
        return unit * count
    raise RequestPlanError("invalid_body_reference", "unsupported body reference")


def _prepare(case_input: dict, wire_cases: dict[str, bytes]):
    ticket = case_input["ticket_guard"]
    fingerprint = case_input["fingerprint"]
    ecdh_hex = ticket["ecdh_key_hex"]
    try:
        ecdh_key = bytes.fromhex(ecdh_hex) if ecdh_hex else None
    except ValueError as exc:
        raise RequestPlanError("invalid_ecdh_key", "invalid ECDH key hex") from exc
    return prepare_reference_send_request(
        method=case_input["method"],
        url=case_input["url"],
        raw_cookie_header=case_input["raw_cookie_header"],
        user_agent=case_input["user_agent"],
        caller_headers=[tuple(pair) for pair in case_input["caller_headers"]],
        body=_body_from_spec(case_input["body"], wire_cases),
        timeout_ms=case_input["timeout_ms"],
        query_ms_token=case_input["query_ms_token"],
        verify_fp=fingerprint["verify_fp"],
        fp=fingerprint["fp"],
        private_key=ticket["private_key"],
        ticket=ticket["ticket"],
        ts_sign=ticket["ts_sign"],
        timestamp=ticket["timestamp"],
        ecdh_key=ecdh_key,
    )


class _FakeHttpClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    async def request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        return SimpleNamespace(
            status_code=200,
            url=url,
            headers={"content-type": "application/x-protobuf"},
            content=b"OK",
        )


class DouyinHttpRequestPlanCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.corpus = _load_json(CORPUS_PATH)
        cls.wire_corpus = _load_json(WIRE_CORPUS_PATH)
        cls.wire_cases = {
            case["id"]: bytes.fromhex(case["expected"]["body_hex"])
            for case in cls.wire_corpus["request_cases"]
        }

    def test_corpus_declares_independent_non_secret_boundary(self):
        self.assertEqual(self.corpus["schema_version"], 1)
        self.assertEqual(self.corpus["corpus_id"], "douyin-pc-im-http-plan-v1")
        self.assertEqual(
            self.corpus["reference"]["revision"],
            "9afaf79580b1ee84e8954ff906ff26869d5b7f1f",
        )
        self.assertFalse(self.corpus["contains_secrets"])
        self.assertTrue(self.corpus["scope"]["request_plan"])
        for excluded in ("signer_algorithms", "http_network", "credential_loader"):
            self.assertFalse(self.corpus["scope"][excluded])
        self.assertEqual(
            self.corpus["constants"]["plan_digest_algorithm"],
            PLAN_DIGEST_ALGORITHM,
        )
        self.assertEqual(len(self.corpus["happy_cases"]), 2)
        self.assertEqual(len(self.corpus["rejection_cases"]), 30)

    def test_cookie_lookup_is_exact_case_and_last_exact_duplicate_wins(self):
        self.assertEqual(
            parse_cookie_lookup(
                "msToken=old; mstoken=wrong-case; msToken=new; "
                "bd_ticket_guard_ts_sign_id=ts.1; "
                "BD_TICKET_GUARD_TS_SIGN_ID=ignored; "
                "bd_ticket_guard_ts_sign_id=ts.2; "
                "_bd_ticket_crypt_cookie=old-trust; "
                "_BD_TICKET_CRYPT_COOKIE=ignored; "
                "_bd_ticket_crypt_cookie=new-trust"
            ),
            {
                "msToken": "new",
                "bd_ticket_guard_ts_sign_id": "ts.2",
                "_bd_ticket_crypt_cookie": "new-trust",
            },
        )

    def test_original_wire_corpus_is_still_the_frozen_body_source(self):
        self.assertEqual(
            hashlib.sha256(WIRE_CORPUS_PATH.read_bytes()).hexdigest(),
            WIRE_CORPUS_SHA256,
        )
        self.assertEqual(self.corpus["wire_corpus"]["sha256"], WIRE_CORPUS_SHA256)
        self.assertEqual(len(self.wire_corpus["request_cases"]), 2)
        self.assertEqual(len(self.wire_corpus["response_cases"]), 31)

    def test_python_prepare_and_finalize_match_every_happy_case(self):
        for case in self.corpus["happy_cases"]:
            with self.subTest(case=case["id"]):
                prepared = _prepare(case["input"], self.wire_cases)
                signer = case["signer_outputs"]
                finalized = finalize_reference_send_request(
                    prepared,
                    SignerOutputs(
                        plan_digest=signer["plan_digest"],
                        a_bogus=signer["a_bogus"],
                        client_data=signer["client_data"],
                        ree_public_key=signer["ree_public_key"],
                    ),
                )
                expected = case["expected"]
                self.assertEqual(
                    parse_cookie_lookup(case["input"]["raw_cookie_header"]),
                    expected["cookie_lookup"],
                )
                self.assertEqual(prepared.a_bogus_query, expected["a_bogus_query"])
                self.assertEqual(prepared.a_bogus_body, expected["a_bogus_body"])
                self.assertIn(
                    f"msToken={prepared.a_bogus_query.removeprefix('msToken=')}",
                    finalized.url,
                )
                self.assertEqual(
                    {
                        "path": prepared.path,
                        "ticket": prepared.ticket,
                        "ts_sign": prepared.ts_sign,
                        "private_key": prepared.private_key,
                        "timestamp": prepared.timestamp,
                        "ecdh_present": prepared.ecdh_present,
                        "t_trust": prepared.t_trust,
                    },
                    expected["ticket_guard_input"],
                )
                self.assertEqual(
                    [list(pair) for pair in prepared.headers],
                    expected["unsigned_headers"],
                )
                self.assertEqual(prepared.body.hex(), expected["body_hex"])
                self.assertEqual(len(prepared.body), expected["body_length"])
                self.assertEqual(prepared.body_sha256, expected["body_sha256"])
                self.assertEqual(prepared.plan_digest, expected["plan_digest"])
                self.assertEqual(finalized.url, expected["final_url"])
                self.assertEqual(
                    [list(pair) for pair in finalized.headers],
                    expected["final_headers"],
                )
                self.assertEqual(finalized.timeout_ms / 1000.0, expected["timeout_s"])

    def test_every_rejection_has_the_expected_stable_code(self):
        for case in self.corpus["rejection_cases"]:
            with self.subTest(case=case["id"]):
                with self.assertRaises(RequestPlanError) as caught:
                    prepared = _prepare(case["input"], self.wire_cases)
                    if case["stage"] == "finalize":
                        signer = case["signer_outputs"]
                        finalize_reference_send_request(
                            prepared,
                            SignerOutputs(
                                plan_digest=signer["plan_digest"],
                                a_bogus=signer["a_bogus"],
                                client_data=signer["client_data"],
                                ree_public_key=signer["ree_public_key"],
                            ),
                        )
                self.assertEqual(caught.exception.code, case["expected_error"])

    def test_private_key_accepts_pem_line_endings_but_rejects_other_controls(self):
        case_input = copy.deepcopy(self.corpus["happy_cases"][0]["input"])
        pem_key = (
            "-----BEGIN PRIVATE KEY-----\r\n"
            "synthetic-private-key\n"
            "-----END PRIVATE KEY-----"
        )
        case_input["ticket_guard"]["private_key"] = pem_key

        prepared = _prepare(case_input, self.wire_cases)
        self.assertEqual(prepared.private_key, pem_key)

        case_input["ticket_guard"]["private_key"] = f"{pem_key}\x00"
        with self.assertRaises(RequestPlanError) as caught:
            _prepare(case_input, self.wire_cases)
        self.assertEqual(caught.exception.code, "invalid_control_character")


class DouyinHttpRequestPlanProductionSeamTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.corpus = _load_json(CORPUS_PATH)
        wire_corpus = _load_json(WIRE_CORPUS_PATH)
        cls.wire_cases = {
            case["id"]: bytes.fromhex(case["expected"]["body_hex"])
            for case in wire_corpus["request_cases"]
        }

    async def test_production_seam_matches_happy_cases_without_node_or_network(self):
        for case in self.corpus["happy_cases"]:
            with self.subTest(case=case["id"]):
                case_input = case["input"]
                ticket = case_input["ticket_guard"]
                signer = case["signer_outputs"]
                expected = case["expected"]
                fake_client = _FakeHttpClient()
                provider = JsSignProvider(clock=lambda: ticket["timestamp"])
                provider._client = fake_client
                provider._ready = True
                provider._user_agent = case_input["user_agent"]
                provider._cookie_headers = {
                    "www.douyin.com": case_input["raw_cookie_header"]
                }
                provider._cookies = {"msToken": case_input["query_ms_token"]}
                provider._bd_ticket = {
                    "private_key": ticket["private_key"],
                    "ticket": ticket["ticket"],
                    "ts_sign": ticket["ts_sign"],
                    "client_cert": "pub.synthetic" if ticket["ecdh_key_hex"] else "",
                }
                ecdh_key = (
                    bytes.fromhex(ticket["ecdh_key_hex"])
                    if ticket["ecdh_key_hex"]
                    else None
                )
                provider._resolve_ecdh_key = AsyncMock(return_value=ecdh_key)
                body = _body_from_spec(case_input["body"], self.wire_cases)

                with (
                    patch.object(js_signer, "get_ab", return_value=signer["a_bogus"]) as get_ab,
                    patch.object(
                        js_signer,
                        "build_bd_ticket_client_data",
                        return_value=signer["client_data"],
                    ) as build_client_data,
                    patch.object(
                        js_signer, "get_ree_key", return_value=signer["ree_public_key"]
                    ) as get_ree_key,
                    patch.object(js_signer.subprocess, "Popen") as popen,
                ):
                    await provider.signed_fetch(
                        case_input["method"],
                        case_input["url"],
                        body=body,
                        headers=dict(case_input["caller_headers"]),
                        timeout_ms=case_input["timeout_ms"],
                        base_params="",
                        post_sign_params={
                            "verifyFp": case_input["fingerprint"]["verify_fp"],
                            "fp": case_input["fingerprint"]["fp"],
                        },
                    )

                self.assertEqual(len(fake_client.calls), 1)
                method, final_url, kwargs = fake_client.calls[0]
                self.assertEqual(method, "POST")
                self.assertEqual(final_url, expected["final_url"])
                self.assertEqual(kwargs["content"], body)
                self.assertEqual(kwargs["timeout"], expected["timeout_s"])
                self.assertEqual(
                    list(kwargs["headers"].items()),
                    [tuple(x) for x in expected["final_headers"]],
                )
                self.assertEqual(kwargs["headers"]["cookie"], case_input["raw_cookie_header"])
                self.assertEqual(provider._query_ms_token, case_input["query_ms_token"])
                get_ab.assert_called_once_with(expected["a_bogus_query"], "")
                self.assertEqual(
                    build_client_data.call_args.args,
                    (
                        expected["ticket_guard_input"]["path"],
                        expected["ticket_guard_input"]["ticket"],
                        expected["ticket_guard_input"]["ts_sign"],
                        expected["ticket_guard_input"]["private_key"],
                    ),
                )
                self.assertEqual(
                    build_client_data.call_args.kwargs,
                    {
                        "ecdh_key": ecdh_key,
                        "timestamp": ticket["timestamp"],
                        "t_trust": expected["ticket_guard_input"]["t_trust"],
                    },
                )
                get_ree_key.assert_called_once_with(ticket["private_key"])
                popen.assert_not_called()

    async def test_reference_send_rejects_before_signer_or_http(self):
        case_input = copy.deepcopy(self.corpus["happy_cases"][0]["input"])
        case_input["ticket_guard"]["private_key"] = ""
        ticket = case_input["ticket_guard"]
        fake_client = _FakeHttpClient()
        provider = JsSignProvider(clock=lambda: ticket["timestamp"])
        provider._client = fake_client
        provider._ready = True
        provider._user_agent = case_input["user_agent"]
        provider._cookie_headers = {"www.douyin.com": case_input["raw_cookie_header"]}
        provider._cookies = {"msToken": case_input["query_ms_token"]}
        provider._bd_ticket = ticket
        provider._resolve_ecdh_key = AsyncMock(return_value=None)
        with patch.object(js_signer, "get_ab") as get_ab:
            with self.assertRaisesRegex(SignerUnavailable, r"request_plan\[missing_private_key\]"):
                await provider.signed_fetch(
                    "POST",
                    case_input["url"],
                    body=b"\x08\x01",
                    headers=dict(case_input["caller_headers"]),
                    post_sign_params={"verifyFp": "FP", "fp": "FP"},
                )
        provider._resolve_ecdh_key.assert_not_awaited()
        get_ab.assert_not_called()
        self.assertEqual(fake_client.calls, [])

    async def test_oversized_resolved_ecdh_key_stays_inside_signer_error_boundary(self):
        case_input = copy.deepcopy(self.corpus["happy_cases"][0]["input"])
        ticket = case_input["ticket_guard"]
        fake_client = _FakeHttpClient()
        provider = JsSignProvider(clock=lambda: ticket["timestamp"])
        provider._client = fake_client
        provider._ready = True
        provider._user_agent = case_input["user_agent"]
        provider._cookie_headers = {"www.douyin.com": case_input["raw_cookie_header"]}
        provider._cookies = {"msToken": case_input["query_ms_token"]}
        provider._bd_ticket = ticket
        provider._resolve_ecdh_key = AsyncMock(return_value=b"K" * 8193)

        with patch.object(js_signer, "get_ab") as get_ab:
            with self.assertRaisesRegex(SignerUnavailable, r"request_plan\[field_too_large\]"):
                await provider.signed_fetch(
                    case_input["method"],
                    case_input["url"],
                    body=b"\x08\x01",
                    headers=dict(case_input["caller_headers"]),
                    post_sign_params={"verifyFp": "FP", "fp": "FP"},
                )

        get_ab.assert_not_called()
        self.assertEqual(fake_client.calls, [])

    async def test_malformed_endpoint_stays_inside_request_plan_error_boundary(self):
        fake_client = _FakeHttpClient()
        provider = JsSignProvider()
        provider._client = fake_client
        provider._ready = True

        with patch.object(js_signer, "get_ab") as get_ab:
            with self.assertRaisesRegex(
                SignerUnavailable,
                r"request_plan\[unsupported_endpoint\]",
            ):
                await provider.signed_fetch(
                    "POST",
                    "https://[",
                    body=b"\x08\x01",
                    post_sign_params={"verifyFp": "FP", "fp": "FP"},
                )

        get_ab.assert_not_called()
        self.assertEqual(fake_client.calls, [])

    async def test_non_reference_endpoint_keeps_body_signing_behavior(self):
        fake_client = _FakeHttpClient()
        provider = JsSignProvider()
        provider._client = fake_client
        provider._ready = True
        provider._cookies = {"msToken": "TOKEN"}
        with patch.object(js_signer, "get_ab", return_value="RAW/AB") as get_ab:
            await provider.signed_fetch(
                "POST",
                "https://www.douyin.com/api/synthetic",
                body="body-content",
                base_params="aid=6383",
            )
        get_ab.assert_called_once_with("aid=6383&msToken=TOKEN", "body-content")
        self.assertEqual(
            fake_client.calls[0][1],
            "https://www.douyin.com/api/synthetic?aid=6383&msToken=TOKEN&a_bogus=RAW/AB",
        )


if __name__ == "__main__":
    unittest.main()
