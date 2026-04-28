#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: transport/wire/im_protocol.py
@Desc: 抖音 IM 协议消息构造与解析

字段定义来自 Phase 3.0 sniff 报告（im_protocol_map.md）的 hex 推断。
**重要警告**：
  - 完整 protobuf body 没有 .proto 文件支持，字段编号是从 hex 推断
  - sniff 抓到的 send body 共 819 字节，但报告里只截了前 80 字节，
    所以 SendMessageRequest 的完整字段表没法保证
  - 我们的策略：编最小必要字段（conversation_id + text + client_msg_id +
    msg_type），其余靠平台默认值；如果发送失败就 fallback 到 BrowserTransport。
  - 任何字段映射改了请同时更新 docstring 和单测

Envelope（外层固定字段，所有 IM 接口共用）：
  field 1  varint  cmd_id            send_message=100, get_by_conversation=302...
  field 2  varint  seq_id            客户端递增序号
  field 3  string  sdk_version       "1.3.0"
  field 4  string  token             空 / 鉴权 token，留空即可
  field 5  varint  ?                 sniff 抓到 3
  field 6  varint  ?                 sniff 抓到 0
  field 7  string  build_id          "f1e96de:master" — 平台升级会变
  field 8  bytes   body              内嵌业务 message

SendMessageRequest (envelope.field 8 的内层 message)：
  field 100 bytes  send_payload      内嵌另一层（推断）
    field 1  string  conversation_id     "0:1:80549827440:3061476426516824"
    field 2  varint  msg_type            1=text (推断)
    field 3  varint  ?                   sniff 出现，作用未知
    field ?  string  text                ← 待 sniff 完整 body 后定位

Response envelope:
  field 1  varint  cmd_id (echo)
  field 2  varint  seq_id (echo)
  field 3  varint  status_code         0=OK
  field 4  string  status_msg          "OK"
  field 5  varint  ?
  field 6  bytes   body                内嵌响应业务 message
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from core.douyin.runtime.transport.wire.codec import (
    WIRE_LEN,
    encode_field,
    encode_tag,
    encode_varint,
    get_first_bytes,
    get_first_int,
    get_first_str,
    iter_fields,
)

logger = logging.getLogger(__name__)


# ---------------- 协议常量 ----------------
SEND_MESSAGE_CMD_ID = 100        # 来自 sniff hex `08 64` (varint=100)
GET_BY_CONVERSATION_CMD_ID = 302  # 来自 sniff hex `08 ad 02` (varint=301) — 待回放确认
GET_BY_USER_INIT_CMD_ID = 203     # 来自 sniff hex `08 cb 01` (varint=203)
GET_CONVERSATION_LIST_CMD_ID = 1001  # 来自 sniff hex `08 e9 07` (varint=1001)

# envelope 中的 build_id —— 跟随抖音前端打包版本，平台升级时这里会失效
# sniff 报告里看到的值；改 build 必须同步改这里（或者上层运行时 inject）
IM_BUILD_ID = "f1e96de:master"
IM_SDK_VERSION = "1.3.0"

# envelope field 5/6 的固定 magic（sniff 报告里所有 send 请求都用这俩值）
_ENVELOPE_FIELD5 = 3
_ENVELOPE_FIELD6 = 0

# SendMessageRequest 的字段编号（从 sniff hex 推断；不全字段时默认服务端补全）
# field 100 是 send_payload 的容器（hex `a2 06 ...`，0xa2=10100010 → field=20, wire=2 ?? 实际上
#  10100010 varint = 0xA2; tag 第一个字节 0xA2 → 后续 byte `06` → 完整 tag varint = 0x06A2 = 1698
#  → 1698 >> 3 = 212, wire = 1698 & 7 = 2  → 实际是 field 212 wire LEN
# 重新解：08 64 ... 42 e8 02 a2 06 e4 02
#   42 = 01000010 → field 8, wire LEN
#   e8 02 = 0x02e8 = 360 (length of envelope.field 8)
#   a2 06 = 0x06A2 → varint = (0xA2 & 0x7f) | ((0x06) << 7) = 0x22 | 0x300 = 0x322 = 802
#     802 >> 3 = 100, wire = 2  → field 100, wire LEN
#   e4 02 = 0x02e4 = 356 (length)
# 所以 envelope field 8 内只有一个 field 100 = SendMessageRequest，正确。
SEND_PAYLOAD_FIELD_NUMBER = 100

# SendMessageRequest 内层字段（从 hex `0a 20 30 3a 31 ... 10 01 18 9b 84` 推断）
SMR_CONVERSATION_ID = 1   # 0a → tag, field 1 wire LEN, "0:1:80549827440:..."
SMR_FIELD_2 = 2            # 10 01 → field 2 = 1 (推断 msg_type=1=text)
# 后续字段我们没法 100% 确认；按照"最小必要"策略：
# 多观察样本会发现 text 出现在某个 LEN 字段，client_msg_id 也是 LEN 字段。
# 实践经验：抖音 web 端 IM 协议中，文本字段位置常在 field 4-7 区间。
# 安全做法：只发 conversation_id + 必要 marker，让服务端拒绝时再补字段。
# Phase 3.2b 第一版**先尝试常见布局**：text=field 4, client_msg_id=field 5
SMR_FIELD_TEXT = 4         # 推断；如平台拒收，调到 5/6/7 试
SMR_FIELD_CLIENT_MSG_ID = 5  # 推断

# Response 字段编号
RESP_STATUS_CODE = 3       # `18 00` → field 3 = 0
RESP_STATUS_MSG = 4        # `22 02 4f 4b` → field 4 = "OK"
RESP_BODY_FIELD = 6        # `32 e1 01 ...` → field 6 = inner body
RESP_BODY_INNER_FIELD = 100  # body 内层 field 100 = SendMessageResponse
SMResp_SERVER_MSG_ID = 1   # `08 b1 8c ac ...` → field 1 = server-assigned int64
SMResp_CLIENT_MSG_ID = 4   # `22 24 34 61 38 30 ...` → field 4 = "uuid"


# ---------------- seq_id 管理 ----------------
class _SeqIdAllocator:
    """全局单调递增的 seq_id 分配器。线程安全。

    抖音 web 客户端的 seq_id 看起来是单调递增的整数（hex 里 0x4e9d, 0x4ead 这种），
    我们采用 "epoch_seconds_of_today << 12 | counter" 的方式生成，保证：
      - 进程重启后不会重复
      - 多账号共用一个全局空间，不冲突
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counter = 0
        # 起始基数：当前时间秒，左移 12 位留 4096 / 秒的空间
        self._base = int(time.time())

    def next(self) -> int:
        with self._lock:
            self._counter = (self._counter + 1) & 0xFFF
            if self._counter == 0:
                self._base = int(time.time())
            return ((self._base & 0x3FFFF) << 12) | self._counter


_seq_alloc = _SeqIdAllocator()


def next_seq_id() -> int:
    return _seq_alloc.next()


# ---------------- 编码 ----------------
def _encode_send_payload(
    conversation_id: str,
    text: str,
    client_msg_id: str,
) -> bytes:
    """编码 SendMessageRequest 内层（envelope.field 8.field 100）。

    最小字段集：conversation_id + msg_type=1 + text + client_msg_id。
    其它字段（ticket / extension / mentions...）让服务端用默认值。
    """
    parts: list[bytes] = []
    parts.append(encode_field(SMR_CONVERSATION_ID, conversation_id))
    parts.append(encode_field(SMR_FIELD_2, 1))  # msg_type=1=text
    parts.append(encode_field(SMR_FIELD_TEXT, text))
    parts.append(encode_field(SMR_FIELD_CLIENT_MSG_ID, client_msg_id))
    return b"".join(parts)


def _wrap_send_envelope(
    cmd_id: int,
    seq_id: int,
    inner_body: bytes,
    *,
    sdk_version: str = IM_SDK_VERSION,
    build_id: str = IM_BUILD_ID,
) -> bytes:
    """组装 envelope。inner_body 已经包好 field 100 wrapper。"""
    parts: list[bytes] = []
    parts.append(encode_field(1, cmd_id))                 # cmd_id
    parts.append(encode_field(2, seq_id))                 # seq_id
    parts.append(encode_field(3, sdk_version))            # sdk_version
    parts.append(encode_field(4, ""))                     # token (empty)
    parts.append(encode_field(5, _ENVELOPE_FIELD5))       # magic 3
    parts.append(encode_field(6, _ENVELOPE_FIELD6))       # magic 0
    parts.append(encode_field(7, build_id))               # build_id
    # field 8 = inner_body
    parts.append(encode_tag(8, WIRE_LEN))
    parts.append(encode_varint(len(inner_body)))
    parts.append(inner_body)
    return b"".join(parts)


def encode_send_message_request(
    *,
    conversation_id: str,
    text: str,
    client_msg_id: Optional[str] = None,
    seq_id: Optional[int] = None,
    build_id: str = IM_BUILD_ID,
    sdk_version: str = IM_SDK_VERSION,
) -> tuple[bytes, str, int]:
    """构造 send_message HTTP body。

    Returns:
        (body_bytes, client_msg_id, seq_id)：方便上层落库时引用 client_msg_id。
    """
    if not conversation_id:
        raise ValueError("conversation_id 不能为空")
    if not text:
        raise ValueError("text 不能为空")
    cm_id = client_msg_id or str(uuid.uuid4())
    sq_id = seq_id if seq_id is not None else next_seq_id()

    payload = _encode_send_payload(conversation_id, text, cm_id)
    # field 100 wrapper
    inner = (
        encode_tag(SEND_PAYLOAD_FIELD_NUMBER, WIRE_LEN)
        + encode_varint(len(payload))
        + payload
    )
    body = _wrap_send_envelope(
        SEND_MESSAGE_CMD_ID,
        sq_id,
        inner,
        sdk_version=sdk_version,
        build_id=build_id,
    )
    return body, cm_id, sq_id


# ---------------- 解码 ----------------
@dataclass
class SendMessageResult:
    """send_message 响应解析结果。"""

    status_code: int
    status_msg: str
    server_msg_id: int          # 服务端 message_id，可能很大（int64）
    client_msg_id: str          # 客户端 uuid 回显，用来对账
    raw_envelope: dict[int, list]  # 调试用，便于排查未识别字段


def decode_send_message_response(buf: bytes) -> SendMessageResult:
    """解析 send_message 响应 envelope。"""
    if not buf:
        raise ValueError("响应 body 为空")

    envelope: dict[int, list] = {}
    for fnum, _w, val in iter_fields(buf):
        envelope.setdefault(fnum, []).append(val)

    status_code = get_first_int(envelope, RESP_STATUS_CODE, default=-1)
    status_msg = get_first_str(envelope, RESP_STATUS_MSG)

    inner = get_first_bytes(envelope, RESP_BODY_FIELD)
    server_msg_id = 0
    client_msg_id = ""

    if inner:
        # inner = field 100 wrapper
        for fnum, _w, val in iter_fields(inner):
            if fnum == RESP_BODY_INNER_FIELD and isinstance(val, (bytes, bytearray)):
                inner2: dict[int, list] = {}
                for f2, _w2, v2 in iter_fields(val):
                    inner2.setdefault(f2, []).append(v2)
                server_msg_id = get_first_int(inner2, SMResp_SERVER_MSG_ID)
                client_msg_id = get_first_str(inner2, SMResp_CLIENT_MSG_ID)
                break

    return SendMessageResult(
        status_code=status_code,
        status_msg=status_msg,
        server_msg_id=server_msg_id,
        client_msg_id=client_msg_id,
        raw_envelope=envelope,
    )
