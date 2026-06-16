# -*- coding: utf-8 -*-
"""Unit tests for WebSocket Protobuf decoding and ACK generation."""

import gzip
import unittest

from core.douyin.runtime.transport.frontier_ws_decoder import decode_frontier_ws_messages, encode_ws_ack


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


class FrontierWsDecoderTests(unittest.TestCase):

    def test_empty_or_short_returns_empty(self):
        msgs, log_id, ack_ext = decode_frontier_ws_messages(b"")
        self.assertEqual(msgs, [])
        self.assertEqual(log_id, 0)
        self.assertEqual(ack_ext, "")

        msgs, log_id, ack_ext = decode_frontier_ws_messages(b"\x01\x02\x03\x04")
        self.assertEqual(msgs, [])

    def test_decode_valid_ws_push_frame(self):
        # 1. 构造内层 IMMessage 消息的 Protobuf 序列化字节
        msg_body = (
            _enc_field(1, 2, "0:1:80549827440:3061476426516824")  # conversation_id
            + _enc_field(2, 0, 1)                                  # msg_type = 1
            + _enc_field(3, 0, 987654321)                          # server_message_id
            + _enc_field(4, 0, 1700000000000000)                  # create_time_us
            + _enc_field(7, 0, 80549827440)                       # sender_uid
            + _enc_field(8, 2, '{"text": "Hello, world!"}')        # content_json
            + _enc_field(14, 2, "MS4wLjABAAAAabcdefg")            # sender_sec_uid
        )

        # 2. 构造 Response 里的 Message 结构
        inner_msg = _enc_field(1, 2, "sys.chat_msg") + _enc_field(2, 2, msg_body)

        # 3. 构造 Response ( needAck=True, internalExt="token-abc", messagesList=[inner_msg] )
        response_body = (
            _enc_field(1, 2, inner_msg)
            + _enc_field(5, 2, "token-abc")
            + _enc_field(9, 0, 1)
        )

        # 4. Gzip 压缩 Response
        compressed_payload = gzip.compress(response_body)

        # 5. 构造外层 PushFrame
        push_frame_bytes = (
            _enc_field(2, 0, 12345)
            + _enc_field(6, 2, "gzip")
            + _enc_field(8, 2, compressed_payload)
        )

        # 6. 调用解码器进行解析
        msgs, log_id, ack_ext = decode_frontier_ws_messages(push_frame_bytes)

        # 7. 验证结果
        self.assertEqual(log_id, 12345)
        self.assertEqual(ack_ext, "token-abc")
        self.assertEqual(len(msgs), 1)

        im_msg = msgs[0]
        self.assertEqual(im_msg.conversation_id, "0:1:80549827440:3061476426516824")
        self.assertEqual(im_msg.msg_type, 1)
        self.assertEqual(im_msg.server_message_id, 987654321)
        self.assertEqual(im_msg.create_time_us, 1700000000000000)
        self.assertEqual(im_msg.sender_uid, 80549827440)
        self.assertEqual(im_msg.sender_sec_uid, "MS4wLjABAAAAabcdefg")
        self.assertEqual(im_msg.text, "Hello, world!")

    def test_decode_no_ack_clears_ack_ext(self):
        # 1. 构造内层消息
        msg_body = (
            _enc_field(1, 2, "0:1:80549827440:3061476426516824")
            + _enc_field(2, 0, 1)
            + _enc_field(3, 0, 987654321)
            + _enc_field(8, 2, '{"text": "Hi"}')
        )
        inner_msg = _enc_field(1, 2, "sys.chat_msg") + _enc_field(2, 2, msg_body)

        # 2. 构造 Response ( needAck=False )
        response_body = (
            _enc_field(1, 2, inner_msg)
            + _enc_field(5, 2, "token-abc")
            + _enc_field(9, 0, 0)  # needAck = False
        )

        push_frame_bytes = (
            _enc_field(2, 0, 12345)
            + _enc_field(6, 2, "gzip")
            + _enc_field(8, 2, gzip.compress(response_body))
        )

        msgs, log_id, ack_ext = decode_frontier_ws_messages(push_frame_bytes)
        self.assertEqual(log_id, 12345)
        # needAck 为 False，ack_ext 必须被清空（返回空字符串）
        self.assertEqual(ack_ext, "")
        self.assertEqual(len(msgs), 1)

    def test_encode_ws_ack(self):
        ack_bytes = encode_ws_ack(12345, "token-abc")
        expected = _enc_field(2, 0, 12345) + _enc_field(7, 2, "token-abc")
        self.assertEqual(ack_bytes, expected)

        # 参数为空时返回空字节
        self.assertEqual(encode_ws_ack(0, "token"), b"")
        self.assertEqual(encode_ws_ack(123, ""), b"")
