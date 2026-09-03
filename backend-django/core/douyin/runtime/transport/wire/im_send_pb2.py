#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: transport/wire/im_send_pb2.py
@Desc: 基于权威 protobuf（dy_request_pb2，vendored 自 DouYin_Spider/static/Request_pb2）
        的私信「发送」请求体编码器。

为什么单独一个 pb2 编码器（而不是手写 codec im_protocol.py）：
  imapi `/v1/message/send` 是**写接口**，强校验 HTTP 层 bd-ticket-guard
  与 body header map 里的短期 identity-security 凭证。2026 PC IM 升级后，
  envelope.token / ts_sign / sdk_cert / reuqest_sign 均不再携带鉴权材料。

对照 DouYin_Spider/builder/proto.py: build_normal_request + build_send_message_request。
"""
from __future__ import annotations

import json
import random
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from core.douyin.runtime.transport.wire import dy_request_pb2 as R
from core.douyin.runtime.transport.wire.codec import (
    encode_field,
    get_first_bytes,
    get_first_int,
    get_first_str,
    iter_fields,
)
from core.douyin.runtime.transport.wire.im_protocol import IM_BUILD_ID, IM_SDK_VERSION

SEND_MESSAGE_CMD_ID = 100
GET_CONVERSATION_INFO_CMD_ID = 610
# 保留旧导入名，单一真值收敛到 im_protocol。
IM_BUILD_NUMBER = IM_BUILD_ID
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _header_entry(key: str, value: str) -> bytes:
    return encode_field(1, key) + encode_field(2, value)


def _serialize_creator_envelope(
    req: "R.Request",
    *,
    body_field: int,
    body_payload: bytes,
    headers: list[tuple[str, str]],
) -> bytes:
    """Serialize creator IM with explicit proto3 defaults seen on Chromium's wire."""

    request_body = encode_field(body_field, body_payload)
    parts = [
        encode_field(1, int(req.cmd)),
        encode_field(2, int(req.sequence_id)),
        encode_field(3, req.sdk_version),
        encode_field(4, ""),
        encode_field(5, int(req.refer)),
        encode_field(6, 0),
        encode_field(7, req.build_number),
        encode_field(8, request_body),
        encode_field(9, ""),
        encode_field(11, req.device_platform),
    ]
    parts.extend(encode_field(15, _header_entry(key, value)) for key, value in headers)
    parts.extend(
        [
            encode_field(18, int(req.auth_type)),
            encode_field(21, req.biz),
            encode_field(22, req.access),
        ]
    )
    return b"".join(parts)


def _build_envelope(
    cmd: int,
    bd_ticket: Optional[dict] = None,  # noqa: ARG001 旧调用兼容；新 envelope 不带票据
    *,
    s_v_web_id: str = "",
    webid: str = "",  # noqa: ARG001 旧调用兼容
    user_agent: str = _DEFAULT_UA,  # noqa: ARG001 浏览器 UA 仅用于 HTTP 层
) -> "R.Request":
    """组装 2026 PC IM Request envelope。

    鉴权已移到 HTTP bd-ticket-guard 头；顶层 token/ts_sign/sdk_cert/request_sign
    保持未设置，与当前浏览器线上包一致。
    """
    req = R.Request()
    req.cmd = cmd
    req.sequence_id = random.randint(10000, 11000)
    req.sdk_version = IM_SDK_VERSION
    req.refer = 3
    req.inbox_type = 0
    req.build_number = IM_BUILD_NUMBER
    # 2026-09-03 creator.douyin.com 实际请求画像。这里必须与创作者中心
    # PC IM 包一致；使用主站 douyin_pc/aid=6383 会被 send 返回 7911。
    req.device_platform = "douyin_creator"
    h = req.headers
    h["aid_new"] = ""
    h["app_name"] = "douyin_creator"
    h["is-retry"] = "0"
    req.auth_type = 1
    req.biz = "douyin_creator"
    req.access = "web_sdk"
    return req


def encode_send_message_request_pb2(
    *,
    conversation_id: str,
    text: str,
    bd_ticket: dict,
    conversation_short_id: int = 0,
    ticket: str = "",
    s_v_web_id: str = "",
    user_agent: str = _DEFAULT_UA,
    client_msg_id: Optional[str] = None,
    content_override: Optional[dict] = None,
    message_type: int = 7,
    identity_security_token: str = "",
    identity_security_device_id: str = "",
    mentioned_users: Optional[list[int]] = None,
    ext: Optional[dict[str, str]] = None,
) -> tuple[bytes, str, int]:
    """构造 2026 PC IM send_message 的 HTTP protobuf body。

    Args:
        conversation_id: 平台会话 id，如 "0:1:80549827440:3061476426516824"。
        text: 纯文本消息内容。
        bd_ticket: 仅作旧调用兼容；凭证用于 HTTP header，不写入 envelope。
        conversation_short_id: 会话短 id（int64）；回复已存在会话暂未持久化时传 0。
        ticket: 会话票据（建会话场景才有）；回复老会话传空。
        s_v_web_id: 旧调用兼容；设备指纹现在仅放 URL verifyFp/fp。

    Returns:
        (body_bytes, client_msg_id, sequence_id)

    Raises:
        ValueError: 入参非法。
    """
    if not conversation_id:
        raise ValueError("conversation_id 不能为空")
    if not text and content_override is None:
        raise ValueError("text 不能为空")
    cm_id = client_msg_id or str(uuid.uuid4())
    short_id = int(conversation_short_id or 0)

    req = _build_envelope(
        SEND_MESSAGE_CMD_ID, bd_ticket, s_v_web_id=s_v_web_id, user_agent=user_agent
    )

    # content_override：发送非文本消息（如伪装卡片），直接用调用方给的完整 content dict。
    if content_override is not None:
        msg_content = content_override
    else:
        # Chromium preserves this insertion order in the compact JSON string.
        msg_content = {"text": text, "aweType": 774}
    content_json = json.dumps(msg_content, ensure_ascii=False, separators=(",", ":"))

    ext_pairs = [
        ("s:mentioned_users", ""),
        ("s:client_message_id", cm_id),
    ]
    for key, value in (ext or {}).items():
        if key not in {"s:mentioned_users", "s:client_message_id", "s:stime"}:
            ext_pairs.append((str(key), str(value)))
    # Browser JS does not left-pad the random suffix (observed widths 2..5).
    ext_pairs.append(("s:stime", f"{_now_ms()}.{random.randrange(100000)}"))

    send_parts = [
        encode_field(1, conversation_id),
        encode_field(2, 1),
        encode_field(3, short_id),
        encode_field(4, content_json),
    ]
    send_parts.extend(
        encode_field(5, _header_entry(key, value)) for key, value in ext_pairs
    )
    send_parts.extend(
        [
            encode_field(6, int(message_type)),
            encode_field(7, ticket or ""),
            encode_field(8, cm_id),
        ]
    )
    send_parts.extend(encode_field(9, int(uid)) for uid in (mentioned_users or []))

    headers: list[tuple[str, str]] = []
    if identity_security_token:
        headers.append(
            (
                "identity_security_token",
                json.dumps(
                    {"token": str(identity_security_token)}, separators=(",", ":")
                ),
            )
        )
    if identity_security_device_id:
        headers.append(("identity_security_device_id", str(identity_security_device_id)))
    headers.extend(
        [
            ("identity_security_aid", "2906"),
            ("aid_new", ""),
            ("app_name", "douyin_creator"),
            ("is-retry", "0"),
        ]
    )
    serialized = _serialize_creator_envelope(
        req,
        body_field=SEND_MESSAGE_CMD_ID,
        body_payload=b"".join(send_parts),
        headers=headers,
    )
    return serialized, cm_id, req.sequence_id


def encode_get_conversation_info_request_pb2(
    *,
    conversation_id: str,
    conversation_short_id: int,
    user_agent: str = _DEFAULT_UA,
) -> tuple[bytes, int]:
    """构造 PC IM ``conversation/get_info_list`` 请求。"""

    if not conversation_id:
        raise ValueError("conversation_id 不能为空")
    short_id = int(conversation_short_id or 0)
    if short_id <= 0:
        raise ValueError("conversation_short_id 必须大于 0")

    req = _build_envelope(GET_CONVERSATION_INFO_CMD_ID, user_agent=user_agent)
    data_payload = b"".join(
        [
            encode_field(1, conversation_id),
            encode_field(2, short_id),
            encode_field(3, 1),
        ]
    )
    get_info_payload = encode_field(1, data_payload)
    serialized = _serialize_creator_envelope(
        req,
        body_field=GET_CONVERSATION_INFO_CMD_ID,
        body_payload=get_info_payload,
        headers=[
            ("aid_new", ""),
            ("app_name", "douyin_creator"),
            ("is-retry", "0"),
        ],
    )
    return serialized, req.sequence_id


@dataclass(frozen=True)
class ConversationSendContext:
    """发送所需的会话短 ID 与会话票据。"""

    status_code: int
    status_msg: str
    conversation_id: str = ""
    conversation_short_id: int = 0
    ticket: str = ""


def decode_get_conversation_info_response_pb2(buf: bytes) -> ConversationSendContext:
    """解析 ``conversation/get_info_list`` 的首条会话信息。"""

    if not buf:
        return ConversationSendContext(status_code=-1, status_msg="empty body")
    envelope: dict[int, list] = {}
    try:
        for field_number, _wire_type, value in iter_fields(buf):
            envelope.setdefault(field_number, []).append(value)
    except Exception:
        return ConversationSendContext(status_code=-1, status_msg="envelope unparseable")

    status_code = get_first_int(envelope, 3, default=-1)
    status_msg = get_first_str(envelope, 4)
    if status_code != 0:
        return ConversationSendContext(status_code=status_code, status_msg=status_msg)

    body = get_first_bytes(envelope, 6)
    body_fields: dict[int, list] = {}
    try:
        for field_number, _wire_type, value in iter_fields(body):
            body_fields.setdefault(field_number, []).append(value)
    except Exception:
        return ConversationSendContext(status_code=-1, status_msg="body unparseable")
    wrapper = get_first_bytes(body_fields, GET_CONVERSATION_INFO_CMD_ID)
    wrapper_fields: dict[int, list] = {}
    try:
        for field_number, _wire_type, value in iter_fields(wrapper):
            wrapper_fields.setdefault(field_number, []).append(value)
    except Exception:
        return ConversationSendContext(status_code=-1, status_msg="wrapper unparseable")

    info = get_first_bytes(wrapper_fields, 1)
    info_fields: dict[int, list] = {}
    try:
        for field_number, _wire_type, value in iter_fields(info):
            info_fields.setdefault(field_number, []).append(value)
    except Exception:
        return ConversationSendContext(status_code=-1, status_msg="conversation unparseable")
    return ConversationSendContext(
        status_code=0,
        status_msg=status_msg,
        conversation_id=get_first_str(info_fields, 1),
        conversation_short_id=get_first_int(info_fields, 2),
        ticket=get_first_str(info_fields, 4),
    )
