"""Phase 2 transport.frontier 单测 —— 通用 protobuf 解析 + IM 帧启发式识别。"""
import unittest

from core.douyin.runtime.transport.frontier import (
    decode_frontier_frame,
    is_im_websocket_url,
    parse_protobuf_fields,
)


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
    elif wire_type == 1:
        out += int(payload).to_bytes(8, "little", signed=False)
    elif wire_type == 2:
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        out += _enc_varint(len(payload)) + payload
    elif wire_type == 5:
        out += int(payload).to_bytes(4, "little", signed=False)
    else:
        raise AssertionError(f"unsupported wire_type={wire_type}")
    return bytes(out)


class FrontierProtobufParserTests(unittest.TestCase):
    """通用 protobuf 字段解析。"""

    def test_parse_varint_and_string(self):
        frame = _enc_field(1, 0, 12345) + _enc_field(2, 2, "hello")
        fields = parse_protobuf_fields(frame)
        self.assertEqual(len(fields), 2)
        self.assertEqual(fields[0].field_id, 1)
        self.assertEqual(fields[0].wire_type, 0)
        self.assertEqual(fields[0].value, 12345)
        self.assertEqual(fields[1].field_id, 2)
        self.assertEqual(fields[1].wire_type, 2)
        self.assertEqual(fields[1].text, "hello")

    def test_parse_nested_message(self):
        inner = _enc_field(1, 2, "你好世界") + _enc_field(2, 0, 42)
        frame = _enc_field(3, 2, inner)
        fields = parse_protobuf_fields(frame)
        self.assertEqual(len(fields), 1)
        f = fields[0]
        self.assertEqual(f.field_id, 3)
        # 这一段虽然能被 utf-8 解码（含非 ASCII 控制字符），但启发式应判定为非"纯文本"
        # 然后递归 nested 出来
        self.assertIsNotNone(f.nested)
        self.assertEqual(len(f.nested), 2)
        self.assertEqual(f.nested[0].text, "你好世界")
        self.assertEqual(f.nested[1].value, 42)

    def test_parse_invalid_returns_error(self):
        with self.assertRaises(ValueError):
            parse_protobuf_fields(b"\x88\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff")

    def test_text_decoding_skips_binary(self):
        # 全二进制噪声不应被当作文本字段
        binary_blob = bytes(range(256))[:32]
        frame = _enc_field(1, 2, binary_blob)
        fields = parse_protobuf_fields(frame)
        self.assertEqual(len(fields), 1)
        self.assertIsNone(fields[0].text)


class FrontierIMHintTests(unittest.TestCase):
    """decode_frontier_frame 的启发式识别。"""

    def test_short_payload_returns_none(self):
        self.assertIsNone(decode_frontier_frame(b"\x01\x02"))
        self.assertIsNone(decode_frontier_frame(b""))

    def test_no_im_keywords_returns_none(self):
        # 一段普通字符串，没有任何 IM 关键字
        frame = _enc_field(1, 2, "hello world") + _enc_field(2, 0, 12345)
        self.assertIsNone(decode_frontier_frame(frame))

    def test_only_weak_keywords_returns_none(self):
        # 只命中 "send" / "message" 这类弱关键字 → 不应触发信号
        frame = _enc_field(1, 2, "we should send a message someday")
        self.assertIsNone(decode_frontier_frame(frame))

    def test_strong_keywords_returns_hint(self):
        # 'conversation' + 'text_content' 都是强关键字
        text = "conversation_id=abc text_content=hi"
        frame = _enc_field(1, 2, text) + _enc_field(2, 0, 1700000000000)
        hint = decode_frontier_frame(frame)
        self.assertIsNotNone(hint)
        self.assertIn("conversation", hint.keywords_matched)
        self.assertIsNotNone(hint.text_candidate)
        self.assertTrue(hint.text_candidate.startswith("conversation_id=abc"))

    def test_inbound_direction_via_received_keyword(self):
        text = "message_received: hello there, conversation #1"
        frame = _enc_field(1, 2, text)
        hint = decode_frontier_frame(frame)
        self.assertIsNotNone(hint)
        self.assertEqual(hint.direction, "inbound")

    def test_outbound_direction_via_send_message_request(self):
        text = "send_message_request conversation 12345 content"
        frame = _enc_field(1, 2, text)
        hint = decode_frontier_frame(frame)
        self.assertIsNotNone(hint)
        self.assertEqual(hint.direction, "outbound")

    def test_server_ts_extracted_from_int(self):
        # 1.7e12 量级是合理的 ms 时间戳
        frame = (
            _enc_field(1, 2, "conversation text_content payload")
            + _enc_field(2, 0, 1_700_123_456_789)
        )
        hint = decode_frontier_frame(frame)
        self.assertIsNotNone(hint)
        self.assertEqual(hint.server_ts_ms, 1_700_123_456_789)

    def test_sec_uid_candidate_extracted(self):
        sec_uid = "MS4wLjABAAAAabcdefgHIJKLMNopqrstuvWXyz1234567890ABCDEFGH"
        frame = (
            _enc_field(1, 2, "conversation chat content")
            + _enc_field(2, 2, sec_uid)
        )
        hint = decode_frontier_frame(frame)
        self.assertIsNotNone(hint)
        self.assertEqual(hint.sender_hint, sec_uid)


class FrontierUrlHintTests(unittest.TestCase):
    def test_is_im_websocket_url_positive(self):
        self.assertTrue(is_im_websocket_url("wss://frontier-msns.douyin.com/ws"))
        self.assertTrue(is_im_websocket_url("wss://im-api.bytedance.com/foo"))

    def test_is_im_websocket_url_negative(self):
        self.assertFalse(is_im_websocket_url(""))
        self.assertFalse(is_im_websocket_url("https://example.com"))


if __name__ == "__main__":
    unittest.main()
