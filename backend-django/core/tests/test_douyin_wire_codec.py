"""
Phase 3.2b wire codec 单测

锚定：
  - 字段编号正确性 — 用 sniff 报告里的真实 hex 做事实断言
  - 编解码自洽 — varint / LEN 字段 round-trip
  - SendMessageRequest envelope 前缀必须与 sniff 完全一致（反向证明字段映射对）
  - response 解析能正确提取 status_code / status_msg / server_msg_id / client_msg_id
"""
from __future__ import annotations

import unittest

from core.douyin.runtime.transport.wire.codec import (
    decode_message,
    decode_varint,
    encode_field,
    encode_tag,
    encode_varint,
    iter_fields,
    WIRE_LEN,
    WIRE_VARINT,
)
from core.douyin.runtime.transport.wire.im_protocol import (
    IM_BUILD_ID,
    IM_SDK_VERSION,
    SEND_MESSAGE_CMD_ID,
    decode_send_message_response,
    encode_send_message_request,
)


# ---------------- 基础 codec ----------------
class WireCodecBasicsTests(unittest.TestCase):
    def test_varint_roundtrip(self):
        for v in [0, 1, 127, 128, 255, 256, 16383, 16384, 10013, 100_000_000]:
            buf = encode_varint(v)
            decoded, offset = decode_varint(buf, 0)
            self.assertEqual(decoded, v, f"v={v}")
            self.assertEqual(offset, len(buf))

    def test_varint_negative_rejected(self):
        with self.assertRaises(ValueError):
            encode_varint(-1)

    def test_varint_truncated_raises(self):
        # 0x80 0x80 0x80 ... 没有终止字节
        with self.assertRaises(ValueError):
            decode_varint(b"\x80\x80\x80", 0)

    def test_encode_tag_known_values(self):
        # field 1, wire VARINT → tag = (1<<3)|0 = 8 → 一个字节 0x08
        self.assertEqual(encode_tag(1, WIRE_VARINT), b"\x08")
        # field 8, wire LEN → tag = (8<<3)|2 = 66 = 0x42
        self.assertEqual(encode_tag(8, WIRE_LEN), b"\x42")
        # field 100, wire LEN → tag = (100<<3)|2 = 802 → varint 0xA2 0x06
        self.assertEqual(encode_tag(100, WIRE_LEN), b"\xa2\x06")

    def test_encode_field_int(self):
        self.assertEqual(encode_field(1, 100), b"\x08\x64")
        self.assertEqual(encode_field(2, 10013), b"\x10\x9d\x4e")

    def test_encode_field_bool(self):
        self.assertEqual(encode_field(1, True), b"\x08\x01")
        self.assertEqual(encode_field(1, False), b"\x08\x00")

    def test_encode_field_string(self):
        self.assertEqual(encode_field(3, "1.3.0"), b"\x1a\x05" + b"1.3.0")

    def test_encode_field_empty_string(self):
        # 抖音 envelope.field 4 经常是空字符串
        self.assertEqual(encode_field(4, ""), b"\x22\x00")

    def test_encode_field_bytes(self):
        self.assertEqual(encode_field(8, b"\x01\x02\x03"), b"\x42\x03\x01\x02\x03")

    def test_iter_fields_mixed(self):
        # 模拟 sniff envelope 头部前 6 个字段
        buf = (
            encode_field(1, 100)
            + encode_field(2, 10013)
            + encode_field(3, "1.3.0")
            + encode_field(4, "")
            + encode_field(5, 3)
            + encode_field(6, 0)
        )
        items = list(iter_fields(buf))
        self.assertEqual(len(items), 6)
        self.assertEqual(items[0], (1, WIRE_VARINT, 100))
        self.assertEqual(items[1], (2, WIRE_VARINT, 10013))
        self.assertEqual(items[2], (3, WIRE_LEN, b"1.3.0"))
        self.assertEqual(items[3], (4, WIRE_LEN, b""))
        self.assertEqual(items[4], (5, WIRE_VARINT, 3))
        self.assertEqual(items[5], (6, WIRE_VARINT, 0))

    def test_decode_message_repeated(self):
        # 同一 field 重复出现应保留所有值
        buf = encode_field(1, 1) + encode_field(1, 2) + encode_field(2, "x")
        msg = decode_message(buf)
        self.assertEqual(msg[1], [1, 2])
        self.assertEqual(msg[2], [b"x"])

    def test_iter_fields_unsupported_wire_type_raises(self):
        # tag = (1<<3) | 5 = 13 → wire_type 5 (fixed32)，不支持
        with self.assertRaises(ValueError):
            list(iter_fields(b"\x0d\x00\x00\x00\x00"))


# ---------------- 与 sniff 报告事实对齐 ----------------
class IMProtocolEnvelopePrefixTests(unittest.TestCase):
    """
    sniff 报告里 send_message 请求 hex 前缀（37 字节）：
        08 64                              cmd_id=100
        10 9d 4e                           seq_id=10013
        1a 05 31 2e 33 2e 30               sdk_version="1.3.0"
        22 00                              token=""
        28 03                              field 5 = 3
        30 00                              field 6 = 0
        3a 0e 66 31 65 39 36 64 65 3a 6d 61 73 74 65 72   build_id="f1e96de:master"
        42 ?? ??                           field 8 wrapper (length follows)

    无论 text / client_msg_id 怎么变，这 35 字节 + `0x42` 都不变。
    我们用同样输入构造 envelope，前 35 字节必须 byte-for-byte 完全相同。
    """

    EXPECTED_PREFIX = bytes.fromhex(
        "08 64 "
        "10 9d 4e "
        "1a 05 31 2e 33 2e 30 "
        "22 00 "
        "28 03 "
        "30 00 "
        "3a 0e 66 31 65 39 36 64 65 3a 6d 61 73 74 65 72".replace(" ", "")
    )

    def test_envelope_prefix_matches_sniff(self):
        body, _cm, sq = encode_send_message_request(
            conversation_id="0:1:80549827440:3061476426516824",
            text="hello world",
            client_msg_id="dummy-client-msg-id",
            seq_id=10013,  # 与 sniff 报告一致
            build_id=IM_BUILD_ID,
            sdk_version=IM_SDK_VERSION,
        )
        self.assertEqual(sq, 10013)
        self.assertEqual(body[: len(self.EXPECTED_PREFIX)], self.EXPECTED_PREFIX)
        # field 8 wrapper 紧跟在后：tag 0x42
        self.assertEqual(body[len(self.EXPECTED_PREFIX)], 0x42)

    def test_envelope_inner_field_100_wrapper_present(self):
        """envelope.field 8 内必须以 field 100 wrapper（tag 0xA2 0x06）开头"""
        body, _cm, _ = encode_send_message_request(
            conversation_id="0:1:80549827440:3061476426516824",
            text="hi",
        )
        # 找到 0x42 之后的 length varint，跳过它，应当看到 0xA2 0x06
        idx = body.find(b"\x42", 30)
        self.assertGreater(idx, 0)
        # length varint
        _len, after_len = decode_varint(body, idx + 1)
        self.assertEqual(body[after_len], 0xA2)
        self.assertEqual(body[after_len + 1], 0x06)

    def test_send_request_validates_inputs(self):
        with self.assertRaises(ValueError):
            encode_send_message_request(conversation_id="", text="x")
        with self.assertRaises(ValueError):
            encode_send_message_request(conversation_id="cid", text="")

    def test_client_msg_id_auto_generated_uuid(self):
        body, cm_id, _ = encode_send_message_request(
            conversation_id="cid", text="hi"
        )
        # uuid4 长度 36，含连字符
        self.assertEqual(len(cm_id), 36)
        self.assertEqual(cm_id.count("-"), 4)
        # client_msg_id 应当出现在 body 里（utf-8 编码后）
        self.assertIn(cm_id.encode("utf-8"), body)


# ---------------- 响应解码 ----------------
class IMProtocolResponseTests(unittest.TestCase):
    """
    sniff 报告里 send_message 响应 hex（前 81 字节）：
        08 64                                     cmd_id=100 (echo)
        10 9d 4e                                  seq_id=10013 (echo)
        18 00                                     status_code=0
        22 02 4f 4b                               status_msg="OK"
        28 00                                     field 5 = 0
        32 e1 01                                  field 6 (length 225) = body
          a2 06 dd 01                             field 100 (length 221)
            08 b1 8c ac a2 ea 89 cc f7 69         field 1 = server_msg_id (varint)
            18 00                                 field 3 = 0
            22 24 34 61 38 30 62 31 32 37 ...     field 4 = "4a80b127-2f83-4125-8ece-49451035cee8"
    """

    def _sample_response(self) -> bytes:
        # 完整还原：cmd_id + seq_id + status + status_msg + field 5 + body
        body_inner = (
            # SendMessageResponse: server_msg_id + field 3=0 + client_msg_id
            encode_field(1, 0xF7CC89EAA2AC8CB1)  # 真实 sniff varint，对应 server_msg_id
            + encode_field(3, 0)
            + encode_field(4, "4a80b127-2f83-4125-8ece-49451035cee8")
        )
        # field 100 wrapper
        body_wrapped = (
            encode_tag(100, WIRE_LEN) + encode_varint(len(body_inner)) + body_inner
        )
        # field 6 wrapper
        full = (
            encode_field(1, 100)
            + encode_field(2, 10013)
            + encode_field(3, 0)
            + encode_field(4, "OK")
            + encode_field(5, 0)
            + encode_tag(6, WIRE_LEN)
            + encode_varint(len(body_wrapped))
            + body_wrapped
        )
        return full

    def test_decode_send_message_response_extracts_fields(self):
        buf = self._sample_response()
        result = decode_send_message_response(buf)
        self.assertEqual(result.status_code, 0)
        self.assertEqual(result.status_msg, "OK")
        self.assertEqual(result.server_msg_id, 0xF7CC89EAA2AC8CB1)
        self.assertEqual(result.client_msg_id, "4a80b127-2f83-4125-8ece-49451035cee8")

    def test_decode_send_message_response_handles_missing_body(self):
        # 只有 envelope，没有 field 6 body
        buf = (
            encode_field(1, 100)
            + encode_field(2, 10013)
            + encode_field(3, 0)
            + encode_field(4, "OK")
        )
        result = decode_send_message_response(buf)
        self.assertEqual(result.status_code, 0)
        self.assertEqual(result.status_msg, "OK")
        self.assertEqual(result.server_msg_id, 0)
        self.assertEqual(result.client_msg_id, "")

    def test_decode_send_message_response_status_error(self):
        buf = (
            encode_field(1, 100)
            + encode_field(2, 10013)
            + encode_field(3, 4001)
            + encode_field(4, "rate limit")
        )
        result = decode_send_message_response(buf)
        self.assertEqual(result.status_code, 4001)
        self.assertEqual(result.status_msg, "rate limit")

    def test_decode_empty_buf_raises(self):
        with self.assertRaises(ValueError):
            decode_send_message_response(b"")


if __name__ == "__main__":
    unittest.main()
