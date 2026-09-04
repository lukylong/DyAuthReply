"""Cross-language golden corpus for the bounded Rust PC IM migration gate."""
from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from core.douyin.runtime.health import (
    PROBE_INCONCLUSIVE,
    classify_signed_response,
)
from core.douyin.runtime.transport.wire import dy_request_pb2 as R
from core.douyin.runtime.transport.wire.im_protocol import (
    classify_send_message_delivery,
    decode_send_message_response,
)
from core.douyin.runtime.transport.wire.im_send_pb2 import (
    encode_send_message_request_pb2,
)


CORPUS_PATH = (
    Path(__file__).resolve().parents[3]
    / "protocol-fixtures"
    / "douyin_pc_im_v1.json"
)


def _load_corpus() -> dict:
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


class DouyinProtocolCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.corpus = _load_corpus()

    def test_corpus_declares_bounded_non_secret_scope(self):
        self.assertEqual(self.corpus["schema_version"], 1)
        self.assertEqual(self.corpus["corpus_id"], "douyin-pc-im-send-v1")
        self.assertFalse(self.corpus["contains_secrets"])
        self.assertTrue(self.corpus["scope"]["send_request"])
        self.assertTrue(self.corpus["scope"]["send_response"])
        for excluded in ("signer", "http_transport", "inbox", "websocket"):
            self.assertFalse(self.corpus["scope"][excluded])

    def test_python_pb2_matches_every_request_golden(self):
        for case in self.corpus["request_cases"]:
            with self.subTest(case=case["id"]):
                request_input = case["input"]
                body, client_msg_id, sequence_id = encode_send_message_request_pb2(
                    conversation_id=request_input["conversation_id"],
                    conversation_short_id=request_input["conversation_short_id"],
                    ticket=request_input["ticket"],
                    text=request_input["text"],
                    bd_ticket={},
                    user_agent=request_input["user_agent"],
                    client_msg_id=request_input["client_msg_id"],
                    sequence_id=request_input["sequence_id"],
                    stime=request_input["stime"],
                    message_type=request_input["message_type"],
                    identity_security_token=request_input[
                        "identity_security_token"
                    ],
                    identity_security_device_id=request_input[
                        "identity_security_device_id"
                    ],
                    mentioned_users=request_input["mentioned_users"],
                    ext={item["key"]: item["value"] for item in request_input["ext"]},
                    deterministic=True,
                )
                expected = case["expected"]
                self.assertEqual(body.hex(), expected["body_hex"])
                self.assertEqual(len(body), expected["body_length"])
                self.assertEqual(hashlib.sha256(body).hexdigest(), expected["body_sha256"])
                self.assertEqual(client_msg_id, request_input["client_msg_id"])
                self.assertEqual(sequence_id, request_input["sequence_id"])

                envelope = R.Request.FromString(body)
                send = envelope.body.send_message_body
                self.assertEqual(envelope.token, "")
                self.assertEqual(envelope.ts_sign, "")
                self.assertEqual(envelope.sdk_cert, "")
                self.assertEqual(envelope.reuqest_sign, "")
                self.assertEqual(
                    envelope.headers["browser_version"],
                    request_input["user_agent"].replace("Mozilla/", "", 1),
                )
                self.assertEqual(
                    send.content,
                    json.dumps(
                        {
                            "aweType": 700,
                            "type": 0,
                            "richTextInfos": [],
                            "text": request_input["text"],
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
                self.assertEqual(
                    [entry.key for entry in send.ext],
                    ["s:mentioned_users", "s:client_message_id"]
                    + [
                        entry["key"]
                        for entry in request_input["ext"]
                        if entry["key"]
                        not in {
                            "s:mentioned_users",
                            "s:client_message_id",
                            "s:stime",
                        }
                    ]
                    + ["s:stime"],
                )
                self.assertEqual(send.ext[-1].value, request_input["stime"])
                self.assertEqual(list(send.mentioned_users), request_input["mentioned_users"])

    def test_python_decoder_and_classifier_match_every_response_golden(self):
        decoded_fields = (
            "status_code",
            "status_msg",
            "server_msg_id",
            "client_msg_id",
            "biz_status_code",
            "biz_status_text",
            "biz_raw_check_code",
            "outer_status_present",
            "has_response_body",
            "has_inner_response",
            "business_payload_present",
            "business_payload_valid",
        )
        for case in self.corpus["response_cases"]:
            with self.subTest(case=case["id"]):
                result = decode_send_message_response(
                    bytes.fromhex(case["body_hex"]),
                    strict_business_payload=True,
                )
                expected = case["expected"]
                for field in decoded_fields:
                    self.assertEqual(getattr(result, field), expected[field], field)
                classification = classify_send_message_delivery(
                    http_status=case["http_status"],
                    result=result,
                    expected_client_msg_id=case["expected_client_msg_id"],
                )
                self.assertEqual(classification, expected["classification"])

    def test_deterministic_controls_reject_invalid_values(self):
        with self.assertRaisesRegex(ValueError, "sequence_id"):
            encode_send_message_request_pb2(
                conversation_id="synthetic-conversation",
                text="hello",
                bd_ticket={},
                client_msg_id="client-1",
                sequence_id=0,
                stime="1700000000000.00001",
                deterministic=True,
            )

    def test_strict_business_types_do_not_change_default_decoder_fields(self):
        case = next(
            item
            for item in self.corpus["response_cases"]
            if item["id"] == "uncertain_string_business_code"
        )
        raw = bytes.fromhex(case["body_hex"])
        live_compatible = decode_send_message_response(raw)
        strict = decode_send_message_response(raw, strict_business_payload=True)

        self.assertEqual(live_compatible.biz_status_code, 8610)
        self.assertFalse(live_compatible.business_payload_valid)
        self.assertEqual(strict.biz_status_code, 0)
        self.assertFalse(strict.business_payload_valid)

        deeply_nested = next(
            item
            for item in self.corpus["response_cases"]
            if item["id"] == "uncertain_deeply_nested_business_json"
        )
        nested_raw = bytes.fromhex(deeply_nested["body_hex"])
        nested_default = decode_send_message_response(nested_raw)
        nested_strict = decode_send_message_response(
            nested_raw, strict_business_payload=True
        )
        self.assertEqual(nested_default.biz_status_code, 0)
        self.assertFalse(nested_default.business_payload_valid)
        self.assertFalse(nested_strict.business_payload_valid)
        with self.assertRaisesRegex(ValueError, "deterministic"):
            encode_send_message_request_pb2(
                conversation_id="synthetic-conversation",
                text="hello",
                bd_ticket={},
                deterministic=True,
            )

    def test_missing_http_status_is_not_reported_as_valid(self):
        self.assertEqual(
            classify_signed_response(None, 0, None),
            PROBE_INCONCLUSIVE,
        )


if __name__ == "__main__":
    unittest.main()
