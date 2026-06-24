#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: transport/frontier_ws_decoder.py
@Desc: WebSocket PushFrame 与 Response 的 Protobuf 协议直接解析器。
"""

import gzip
import logging
from typing import List, Tuple

from core.douyin.runtime.transport.frontier import parse_protobuf_fields
from core.douyin.runtime.transport.wire.codec import encode_field
from core.douyin.runtime.transport.wire.im_protocol_recv import decode_im_message, IMMessage

logger = logging.getLogger(__name__)


def dump_proto_fields(fields: list, indent: str = "") -> str:
    lines = []
    for f in fields:
        val_str = ""
        if isinstance(f.value, bytes):
            val_str = f"bytes(len={len(f.value)}) hex={f.value.hex()[:60]}"
            if len(f.value) > 30:
                val_str += "..."
        else:
            val_str = str(f.value)
        
        line = f"{indent}- Field {f.field_id} (wire={f.wire_type}): {val_str}"
        if f.text:
            line += f" | text={f.text!r}"
        lines.append(line)
        if f.nested:
            lines.append(dump_proto_fields(f.nested, indent + "  "))
    return "\n".join(lines)


def decode_frontier_ws_messages(data: bytes) -> Tuple[List[IMMessage], int, str]:
    """
    解码 WebSocket 二进制帧。
    
    外层结构 (PushFrame):
      - f2  logId (uint64)
      - f6  payloadEncoding (string, e.g. "gzip")
      - f7  payloadType (string)
      - f8  payload (bytes)

    内层结构 (Response):
      - f1  messagesList (repeated Message)
      - f5  internalExt (string)
      - f9  needAck (bool)

    内层 Message 结构:
      - f1  method (string)
      - f2  payload (bytes, serialized IMMessage)

    Returns:
        (decoded_messages, log_id, internal_ext_for_ack)
    """
    if not data or len(data) < 12:
        return [], 0, ""

    try:
        fields = parse_protobuf_fields(data, max_depth=2)
    except Exception as e:
        logger.debug(f"[frontier.ws_decoder] 解析 PushFrame 失败: {e}")
        return [], 0, ""

    log_id = 0
    encoding = ""
    payload = b""

    logger.debug(f"[frontier.ws_decoder] PushFrame 解析字段:\n{dump_proto_fields(fields)}")

    for f in fields:
        if f.field_id == 2 and isinstance(f.value, int):
            log_id = f.value
        elif f.field_id == 6:
            if isinstance(f.value, bytes):
                encoding = f.value.decode("utf-8", errors="ignore")
            elif f.text:
                encoding = f.text
        elif f.field_id == 8 and isinstance(f.value, bytes):
            payload = f.value

    if not payload:
        logger.debug(f"[frontier.ws_decoder] 没有 payload")
        return [], log_id, ""

    logger.debug(f"[frontier.ws_decoder] encoding={encoding!r} payload_len={len(payload)}")

    # 解压 payload
    if encoding == "gzip":
        try:
            decompressed = gzip.decompress(payload)
        except Exception as e:
            logger.warning(f"[frontier.ws_decoder] gzip 解压失败: {e}")
            return [], log_id, ""
    else:
        decompressed = payload

    logger.debug(f"[frontier.ws_decoder] decompressed_len={len(decompressed)}")

    try:
        resp_fields = parse_protobuf_fields(decompressed, max_depth=6)
    except Exception as e:
        logger.debug(f"[frontier.ws_decoder] 解析 Response 失败: {e}")
        return [], log_id, ""

    logger.debug(f"[frontier.ws_decoder] Response 解析字段:\n{dump_proto_fields(resp_fields)}")

    messages_raw_list = []
    need_ack = False
    internal_ext = ""

    for f in resp_fields:
        if f.field_id == 1 and isinstance(f.value, bytes):
            messages_raw_list.append(f.value)
        elif f.field_id == 9:
            need_ack = bool(f.value)
        elif f.field_id == 5:
            if isinstance(f.value, bytes):
                internal_ext = f.value.decode("utf-8", errors="ignore")
            elif f.text:
                internal_ext = f.text

    # 仅在需要 ACK 时返回 internal_ext (mock 结构)
    ack_ext = internal_ext if need_ack else ""

    # 如果 mock 结构没找到 ack_ext，尝试从 PushFrame 寻找真实的 msg_id
    if not ack_ext:
        for f in fields:
            if f.text and f.text.startswith("msg_"):
                ack_ext = f.text
                break

    decoded_messages = []

    # 1. 尝试以真实结构解析 (Envelope -> Body -> Wrapper -> Repeated Message)
    try:
        cmd_id = 0
        body_bytes = b""
        for f in resp_fields:
            if f.field_id == 1 and isinstance(f.value, int):
                cmd_id = f.value
            elif f.field_id == 6 and isinstance(f.value, bytes):
                body_bytes = f.value
        
        if body_bytes and cmd_id > 0:
            body_fields = parse_protobuf_fields(body_bytes, max_depth=2)
            wrapper_bytes = b""
            for f in body_fields:
                if f.field_id == cmd_id and isinstance(f.value, bytes):
                    wrapper_bytes = f.value
                    break
            
            if wrapper_bytes:
                wrapper_fields = parse_protobuf_fields(wrapper_bytes, max_depth=2)
                for f in wrapper_fields:
                    if f.field_id in (1, 5) and isinstance(f.value, bytes):
                        im_msg = decode_im_message(f.value)
                        if im_msg is not None:
                            decoded_messages.append(im_msg)
                            logger.debug(f"[frontier.ws_decoder] 从 real 结构成功解码 IMMessage: {im_msg.__dict__}")
    except Exception as e:
        logger.debug(f"[frontier.ws_decoder] 尝试以 real 结构解码失败: {e}")

    # 2. 如果 real 结构没解出消息，则 fallback 到 mock/测试结构
    if not decoded_messages:
        for idx, msg_bytes in enumerate(messages_raw_list):
            try:
                msg_fields = parse_protobuf_fields(msg_bytes, max_depth=2)
                msg_payload = b""
                method = ""
                for f in msg_fields:
                    if f.field_id == 1:
                        if isinstance(f.value, bytes):
                            method = f.value.decode("utf-8", errors="ignore")
                        elif f.text:
                            method = f.text
                    elif f.field_id == 2 and isinstance(f.value, bytes):
                        msg_payload = f.value

                if msg_payload:
                    im_msg = decode_im_message(msg_payload)
                    if im_msg is not None:
                        decoded_messages.append(im_msg)
                        logger.debug(f"[frontier.ws_decoder] 从 mock 结构成功解码 IMMessage: {im_msg.__dict__}")
                    else:
                        if method:
                            logger.debug(f"[frontier.ws_decoder] 跳过非 IM 消息 method={method}")
            except Exception as e:
                logger.debug(f"[frontier.ws_decoder] 解析 mock Message {idx} 失败: {e}")

    logger.debug(f"[frontier.ws_decoder] 最终解析出消息数 count={len(decoded_messages)} ack_ext={ack_ext!r}")
    return decoded_messages, log_id, ack_ext


def encode_ws_ack(log_id: int, internal_ext: str) -> bytes:
    """
    编码确认（ACK）包。
    
    外层结构 (PushFrame):
      - f2  logId (uint64)
      - f7  payloadType (string)
    """
    if not log_id or not internal_ext:
        return b""
    return encode_field(2, log_id) + encode_field(7, internal_ext)
