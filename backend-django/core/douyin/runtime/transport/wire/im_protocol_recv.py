#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: transport/wire/im_protocol_recv.py
@Desc: 抖音 IM "拉取/接收" 路径协议 编码 + 解码

跟 im_protocol.py 配对：
  im_protocol.py      —— 出向（send_message 编码 + 响应解码）
  im_protocol_recv.py —— 入向（list_conversations / get_by_conversation 编码 + 解码）

字段表来自对真实 sniff 流量（21KB get_by_conversation 响应、1.3KB
stranger/get_conversation_list 响应）的反推，详见 docstring 注释。

**风险提示**：
  - 接收路径的字段映射"错一个，整批入向消息错落库"
  - 所以默认通过 `DOUYIN_HTTP_PROTOCOL_SCAN_INBOX_DUAL_RUN=true` 开 dual-run
    对账，跟 BrowserTransport.scan_inbox 的结果交叉验证若干轮再切主路径
  - 改字段编号必须同步改单测和这里的 docstring
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
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
from core.douyin.runtime.transport.wire.im_protocol import (
    GET_BY_CONVERSATION_CMD_ID,
    GET_BY_USER_CMD_ID,
    GET_CONVERSATION_LIST_CMD_ID,
    IM_BUILD_ID,
    IM_SDK_VERSION,
    next_seq_id,
)

logger = logging.getLogger(__name__)


# ---------------- envelope 常量 ----------------
# Request envelope 公共字段（跟 im_protocol.py 的 _wrap_send_envelope 对齐）：
#   f1 cmd_id, f2 sub_cmd_id, f3 sdk_version, f4 token=""
#   f5=3, f6=0/1, f7 build_id, f8 inner body
#   f9 "", f11 product, f15 repeated headers
_PRODUCT_NAME = "douyin_creator"
_CLIENT_SDK = "web_sdk"
_HEADER_KV = (
    ("aid_new", ""),
    ("app_name", _PRODUCT_NAME),
    ("is-retry", "0"),
)


def _encode_header_kv(key: str, value: str) -> bytes:
    """编码一组 f15 = {f1=key, f2=value}（重复字段，每个 header 一条）"""
    inner = encode_field(1, key) + encode_field(2, value)
    return encode_tag(15, WIRE_LEN) + encode_varint(len(inner)) + inner


def _wrap_recv_envelope(
    cmd_id: int,
    sub_cmd_id: int,
    seq_id: int,
    inner_body: bytes,
    *,
    sdk_version: str = IM_SDK_VERSION,
    build_id: str = IM_BUILD_ID,
    magic_f6: int = 0,
) -> bytes:
    """组装拉取类请求 envelope。

    inner_body 已经包过 fXXX wrapper（fXXX = 业务 cmd 对应的 wrapper field）。

    跟 _wrap_send_envelope 区别：
      - 增加 sub_cmd_id (f2) —— 拉取类接口需要细分子命令
      - magic_f6 默认 0（拉取），send_message 用 1（已读 in im_protocol.py 写死）
      - 加上 product 标识 + 静态 headers（创作者中心抓包能看到）
    """
    parts: list[bytes] = []
    parts.append(encode_field(1, cmd_id))
    parts.append(encode_field(2, sub_cmd_id))
    parts.append(encode_field(3, sdk_version))
    parts.append(encode_field(4, ""))
    parts.append(encode_field(5, 3))
    parts.append(encode_field(6, magic_f6))
    parts.append(encode_field(7, build_id))
    parts.append(encode_tag(8, WIRE_LEN))
    parts.append(encode_varint(len(inner_body)))
    parts.append(inner_body)
    parts.append(encode_field(9, ""))
    parts.append(encode_field(11, _PRODUCT_NAME))
    for k, v in _HEADER_KV:
        parts.append(_encode_header_kv(k, v))
    parts.append(encode_field(18, 1))
    parts.append(encode_field(21, _PRODUCT_NAME))
    parts.append(encode_field(22, _CLIENT_SDK))
    return b"".join(parts)


# ---------------- list_conversations: encode ----------------
# Request body: envelope.f8 = {f1000 = {f1=0, f2=1, f3=1}}
# 三个 0/1 是 sniff 报告里所有抓到的请求都用的固定值，含义未知（可能 page/cursor/flag）。
# 第一版照抄；如果分页有问题再回来调。
_LIST_CONV_SUB_CMD_ID = 10009
_LIST_CONV_INNER_FIELD = 1000


def encode_list_conversations_request(
    *,
    seq_id: Optional[int] = None,
    sdk_version: str = IM_SDK_VERSION,
    build_id: str = IM_BUILD_ID,
) -> tuple[bytes, int]:
    """组 list_conversations (stranger/get_conversation_list) HTTP body。

    Returns:
        (body_bytes, seq_id)
    """
    sq_id = seq_id if seq_id is not None else next_seq_id()
    # f1000 inner: {f1=0, f2=1, f3=1}
    f1000_payload = (
        encode_field(1, 0) + encode_field(2, 1) + encode_field(3, 1)
    )
    inner = (
        encode_tag(_LIST_CONV_INNER_FIELD, WIRE_LEN)
        + encode_varint(len(f1000_payload))
        + f1000_payload
    )
    body = _wrap_recv_envelope(
        GET_CONVERSATION_LIST_CMD_ID,
        _LIST_CONV_SUB_CMD_ID,
        sq_id,
        inner,
        sdk_version=sdk_version,
        build_id=build_id,
        magic_f6=1,  # list 请求用的是 1（sniff 抓的）
    )
    return body, sq_id


# ---------------- get_by_user: encode ----------------
# 这是 web 端 IM 真正的"扫描收件箱"入口：
# 抖音前端在内存里维护"全量会话快照"，初次靠 get_by_user_init，之后每次靠
# get_by_user(cursor) 增量拉**所有会话**新 message（覆盖朋友 / 已关注 / 陌生人 /
# 全部 tab，比 stranger/get_conversation_list 大得多）。
#
# 请求结构（sniff 155 字节真实样本反推）：
#   envelope.f8 = inner
#   inner.f200  = { f1 = cursor_us (varint), f2 = limit (varint, 默认 50) }
#
# 关键字段含义：
#   cursor_us: 服务端返回/接受的增量游标。抓包里看起来像 int64 时间值，但不能简单
#              等同于 message.create_time_us；真实响应里 message.create_time_us
#              经常为 0，而 wrapper 另有独立 cursor 字段负责续传。
#   limit: 一次最多返回多少条 message；sniff 默认 50，可改大或小。
#
# 响应结构：
#   envelope.f6 = inner
#   inner.f200  = { f1 = repeated IMMessage }
#                 也可能带 cursor next 等元信息，但目前我们只用 messages。
_GET_BY_USER_SUB_CMD_ID = 10004  # sniff 抓出来的实际值；待写入实现后会回填
_GET_BY_USER_INNER_FIELD = 200


def encode_get_by_user_request(
    *,
    cursor_us: int = 0,
    limit: int = 50,
    seq_id: Optional[int] = None,
    sdk_version: str = IM_SDK_VERSION,
    build_id: str = IM_BUILD_ID,
) -> tuple[bytes, int]:
    """组 get_by_user HTTP body —— 拉自 cursor_us 之后的全部新 messages。

    Args:
        cursor_us: 微秒级时间戳；首次或重置传 0（取最新一批）。
        limit: 一次拉多少条；sniff 默认 50，1-200。

    Returns:
        (body_bytes, seq_id)

    Raises:
        ValueError: limit 超出 1-200。
    """
    if limit <= 0 or limit > 200:
        raise ValueError(f"limit 必须 1-200，收到 {limit}")
    payload = encode_field(1, max(0, int(cursor_us))) + encode_field(2, int(limit))
    inner = (
        encode_tag(_GET_BY_USER_INNER_FIELD, WIRE_LEN)
        + encode_varint(len(payload))
        + payload
    )
    sq_id = seq_id if seq_id is not None else next_seq_id()
    body = _wrap_recv_envelope(
        GET_BY_USER_CMD_ID,
        _GET_BY_USER_SUB_CMD_ID,
        sq_id,
        inner,
        sdk_version=sdk_version,
        build_id=build_id,
        magic_f6=1,  # sniff 抓的 f6=1
    )
    return body, sq_id


@dataclass
class GetByUserResult:
    """get_by_user 响应解析结果。"""

    status_code: int
    status_msg: str
    messages: list[IMMessage] = field(default_factory=list)
    next_cursor_us: int = 0  # 服务端续传 cursor（优先取 wrapper 字段，fallback 到 message 时间）
    raw_envelope: dict[int, list] = field(default_factory=dict)


def decode_get_by_user_response(buf: bytes) -> GetByUserResult:
    """解 get_by_user 响应：返回**跨会话**的 message 列表。

    响应结构: envelope.f6.f200 = { f1 = repeated Message }
    """
    if not buf:
        return GetByUserResult(status_code=-1, status_msg="empty body")
    code, msg, raw = _envelope_status(buf)
    if code != 0:
        return GetByUserResult(status_code=code, status_msg=msg, raw_envelope=raw)

    wrapper = _iter_envelope_inner(buf, _GET_BY_USER_INNER_FIELD)
    if wrapper is None:
        return GetByUserResult(
            status_code=code, status_msg=msg, raw_envelope=raw
        )

    messages: list[IMMessage] = []
    for fnum, _wt, val in iter_fields(wrapper):
        if fnum != 1 or not isinstance(val, (bytes, bytearray)):
            continue
        m = decode_im_message(bytes(val))
        if m is not None:
            messages.append(m)

    # 抓包观察：wrapper 里除了 repeated f1=Message 外，还会带独立 cursor 字段：
    #   - f2 / f5: 两者在样本里相同，值类似 1777359065393749
    #   - message.create_time_us 经常全为 0，不能再拿它当唯一 cursor 来源
    # 因此 next_cursor_us 的优先级：
    #   1) wrapper.f2
    #   2) wrapper.f5
    #   3) messages 中最大 create_time_us（兼容旧样本/单测）
    wrapper_fields = {}
    for fnum, _wt, val in iter_fields(wrapper):
        wrapper_fields.setdefault(fnum, []).append(val)
    next_cursor = get_first_int(wrapper_fields, 2, default=0)
    if next_cursor <= 0:
        next_cursor = get_first_int(wrapper_fields, 5, default=0)

    max_ts = 0
    for m in messages:
        if m.create_time_us > max_ts:
            max_ts = m.create_time_us
    if next_cursor <= 0:
        next_cursor = max_ts

    return GetByUserResult(
        status_code=code,
        status_msg=msg,
        messages=messages,
        next_cursor_us=next_cursor,
        raw_envelope=raw,
    )


# ---------------- get_by_conversation: encode ----------------
# Request body: envelope.f8 = {f301 = {f1=conv_id, f2=1, f3=since_msg_id,
#                                     f4=1, f5=0, f6=limit}}
_GET_BY_CONV_SUB_CMD_ID = 10003
_GET_BY_CONV_INNER_FIELD = 301


def encode_get_by_conversation_request(
    *,
    conversation_id: str,
    since_message_id: int = 0,
    limit: int = 20,
    seq_id: Optional[int] = None,
    sdk_version: str = IM_SDK_VERSION,
    build_id: str = IM_BUILD_ID,
) -> tuple[bytes, int]:
    """组 get_by_conversation HTTP body —— 拉单会话历史消息。

    Args:
        conversation_id: "0:1:80549827440:3061476426516824" 形式
        since_message_id: 0 = 拉最新；非 0 = 从该 server_msg_id 之后拉。
            注意 sniff 抓到的真实请求把这个字段叫 cursor，>0 时是"上次最大
            server_msg_id"，限制为按 msg_type=1 (text) 起步，结合 limit 取一段。
        limit: 一次最多拉多少条；sniff 默认 20。

    Returns:
        (body_bytes, seq_id)

    Raises:
        ValueError: conversation_id 为空。
    """
    if not conversation_id:
        raise ValueError("conversation_id 不能为空")
    if limit <= 0 or limit > 200:
        raise ValueError(f"limit 必须 1-200，收到 {limit}")

    payload = (
        encode_field(1, conversation_id)
        + encode_field(2, 1)
        + encode_field(3, max(0, int(since_message_id)))
        + encode_field(4, 1)
        + encode_field(5, 0)
        + encode_field(6, int(limit))
    )
    inner = (
        encode_tag(_GET_BY_CONV_INNER_FIELD, WIRE_LEN)
        + encode_varint(len(payload))
        + payload
    )
    sq_id = seq_id if seq_id is not None else next_seq_id()
    body = _wrap_recv_envelope(
        GET_BY_CONVERSATION_CMD_ID,
        _GET_BY_CONV_SUB_CMD_ID,
        sq_id,
        inner,
        sdk_version=sdk_version,
        build_id=build_id,
        magic_f6=0,
    )
    return body, sq_id


# ---------------- Response decode 共用：Message ----------------
@dataclass
class IMMessage:
    """一条 IM 消息的协议层视图。

    字段映射（来自 sniff 21KB get_by_conversation + 17.8KB get_by_user 响应深挖）：
      f1  conversation_id     "0:1:80549827440:3061476426516824"
      f2  msg_type            1=普通消息（文本 / 系统命令都用 1，靠 content_json 区分）
      f3  server_message_id   服务端分配的 int64
      f4  create_time_us      微秒时间戳；有时为 0（增量推送场景）
      f5  conversation_short_id
      f6  conversation_type   1=单聊
      f7  sender_uid          ★ 发送方数字 user_id
      f8  content_json        {"text":"...", "command_type":1, ...} UTF-8 JSON
      f9  repeated ext_kv     {f1=key, f2=value}：client_message_id、command_type 等
      f14 sender_sec_uid      ★ 发送方 sec_uid（"MS4wLj..."），可直接用于 echo 比对

    msg_type=1 含**所有**消息类型（文本、已读回执、输入中等系统通知），
    判断"是不是文本"要靠 content_json["text"] 是否非空（系统消息 content 没有 text）。

    sender_sec_uid 是关键字段：之前要去查 user_detail 接口才能拿到 sec_uid，
    而 get_by_user 响应里**每条 message 都直接带 f14**，免去额外 RPC。
    """

    conversation_id: str
    msg_type: int
    server_message_id: int
    create_time_us: int
    sender_uid: int
    sender_sec_uid: str
    content_json: str
    text: str  # 从 content_json["text"] 取，失败或非文本类消息时为 ""
    client_message_id: str  # 从 ext_kv 找 s:client_message_id；失败时为 ""

    @property
    def is_text(self) -> bool:
        # msg_type=1 + content 里能解出 text 字段才算"用户文本消息"
        return self.msg_type == 1 and bool(self.text)


_MSG_F_CONV_ID = 1
_MSG_F_TYPE = 2
_MSG_F_SERVER_ID = 3
_MSG_F_CREATE_TIME = 4
_MSG_F_SENDER_UID = 7
_MSG_F_CONTENT = 8
_MSG_F_EXT_KV = 9
_MSG_F_SENDER_SEC_UID = 14


def _decode_text_from_content_json(content_json: str) -> str:
    """从 content_json 安全提取 text 字段。失败返回空串。

    抖音 IM 的 content 永远是 JSON 字符串，常见结构：
      {"text":"hello"} —— 普通文本
      {"text":"...", "richTextInfos":[...], "mention_users":[...], "ai_ext":"{}", ...}
    """
    if not content_json:
        return ""
    try:
        obj = json.loads(content_json)
    except (json.JSONDecodeError, ValueError, TypeError):
        return ""
    if not isinstance(obj, dict):
        return ""
    val = obj.get("text")
    return val if isinstance(val, str) else ""


def _decode_client_msg_id_from_ext(ext_payloads: list[bytes]) -> str:
    """从 ext_kv 列表里找 s:client_message_id 的 value。"""
    for raw in ext_payloads:
        if not isinstance(raw, (bytes, bytearray)) or not raw:
            continue
        kv: dict[int, list] = {}
        try:
            for fnum, _wt, val in iter_fields(raw):
                kv.setdefault(fnum, []).append(val)
        except Exception:  # noqa: BLE001
            continue
        key = get_first_str(kv, 1)
        if key == "s:client_message_id":
            return get_first_str(kv, 2)
    return ""


def decode_im_message(buf: bytes) -> Optional[IMMessage]:
    """把单条 message protobuf 解成 IMMessage。失败返回 None。"""
    if not buf:
        return None
    fields: dict[int, list] = {}
    try:
        for fnum, _wt, val in iter_fields(buf):
            fields.setdefault(fnum, []).append(val)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[wire.recv] decode_im_message 解析失败: {type(e).__name__}: {e}")
        return None

    conv_id = get_first_str(fields, _MSG_F_CONV_ID)
    msg_type = get_first_int(fields, _MSG_F_TYPE)
    server_id = get_first_int(fields, _MSG_F_SERVER_ID)
    create_us = get_first_int(fields, _MSG_F_CREATE_TIME)
    sender = get_first_int(fields, _MSG_F_SENDER_UID)
    sender_sec = get_first_str(fields, _MSG_F_SENDER_SEC_UID)
    content_raw = get_first_bytes(fields, _MSG_F_CONTENT)
    try:
        content_json = content_raw.decode("utf-8") if content_raw else ""
    except UnicodeDecodeError:
        content_json = ""

    text = _decode_text_from_content_json(content_json)
    ext_list = fields.get(_MSG_F_EXT_KV) or []
    client_msg_id = _decode_client_msg_id_from_ext(
        [v for v in ext_list if isinstance(v, (bytes, bytearray))]
    )

    if not conv_id or server_id <= 0:
        return None

    return IMMessage(
        conversation_id=conv_id,
        msg_type=msg_type,
        server_message_id=server_id,
        create_time_us=create_us,
        sender_uid=sender,
        sender_sec_uid=sender_sec,
        content_json=content_json,
        text=text,
        client_message_id=client_msg_id,
    )


# ---------------- Response: get_by_conversation ----------------
@dataclass
class GetByConversationResult:
    """get_by_conversation 响应的解析结果。"""

    status_code: int
    status_msg: str
    messages: list[IMMessage] = field(default_factory=list)
    raw_envelope: dict[int, list] = field(default_factory=dict)


def _iter_envelope_inner(buf: bytes, wrapper_field: int) -> Optional[bytes]:
    """从 envelope.f6 的 inner body 里取 fXXX wrapper 的 payload。"""
    envelope: dict[int, list] = {}
    try:
        for fnum, _wt, val in iter_fields(buf):
            envelope.setdefault(fnum, []).append(val)
    except Exception:  # noqa: BLE001
        return None
    inner = get_first_bytes(envelope, 6)
    if not inner:
        return None
    for fnum, _wt, val in iter_fields(inner):
        if fnum == wrapper_field and isinstance(val, (bytes, bytearray)):
            return bytes(val)
    return None


def _envelope_status(buf: bytes) -> tuple[int, str, dict[int, list]]:
    """解析 envelope status_code (f3) + status_msg (f4) + 完整字段表。"""
    fields: dict[int, list] = {}
    try:
        for fnum, _wt, val in iter_fields(buf):
            fields.setdefault(fnum, []).append(val)
    except Exception:  # noqa: BLE001
        return -1, "envelope unparseable", fields
    code = get_first_int(fields, 3, default=-1)
    msg = get_first_str(fields, 4)
    return code, msg, fields


def decode_get_by_conversation_response(buf: bytes) -> GetByConversationResult:
    """解 get_by_conversation 响应：返回该会话的 message 列表（按响应里出现的顺序）。

    响应结构：envelope.f6 = inner，inner.f301 = repeated f1 = Message
    """
    if not buf:
        return GetByConversationResult(status_code=-1, status_msg="empty body")

    code, msg, raw = _envelope_status(buf)
    if code != 0:
        return GetByConversationResult(
            status_code=code, status_msg=msg, raw_envelope=raw
        )

    wrapper = _iter_envelope_inner(buf, _GET_BY_CONV_INNER_FIELD)
    if wrapper is None:
        return GetByConversationResult(
            status_code=code,
            status_msg=msg,
            raw_envelope=raw,
        )

    messages: list[IMMessage] = []
    for fnum, _wt, val in iter_fields(wrapper):
        if fnum != 1 or not isinstance(val, (bytes, bytearray)):
            continue
        m = decode_im_message(bytes(val))
        if m is not None:
            messages.append(m)
    return GetByConversationResult(
        status_code=code, status_msg=msg, messages=messages, raw_envelope=raw
    )


# ---------------- Response: list_conversations ----------------
@dataclass
class IMParticipant:
    """会话参与者（含 sec_uid，避免再去刷新页面）"""

    user_id: int
    sec_uid: str


@dataclass
class ConversationListItem:
    """一条会话列表项的协议层视图。

    字段映射（sniff 1.3KB list response 反推）：
      f1 conversation_short_id
      f2 ?                         （可能是会话状态/未读位图，需要更多样本对照）
      f3 last_message              IMMessage 同结构
      f4 conversation_id           "0:1:80549827440:..."
      f5 repeated participant      {f1=user_id, f5=sec_uid}
    """

    conversation_short_id: int
    conversation_id: str
    last_message: Optional[IMMessage]
    participants: list[IMParticipant] = field(default_factory=list)
    raw: dict[int, list] = field(default_factory=dict)

    def peer_participant(self, self_uid: int) -> Optional[IMParticipant]:
        """从 participant 列表里找出"对方"。"""
        if self_uid <= 0:
            return next(iter(self.participants), None) if self.participants else None
        for p in self.participants:
            if p.user_id and p.user_id != self_uid:
                return p
        return None


@dataclass
class ListConversationsResult:
    status_code: int
    status_msg: str
    self_uid: int = 0
    items: list[ConversationListItem] = field(default_factory=list)
    raw_envelope: dict[int, list] = field(default_factory=dict)


_CONV_ITEM_F_SHORT_ID = 1
_CONV_ITEM_F_LAST_MSG = 3
_CONV_ITEM_F_CONV_ID = 4
_CONV_ITEM_F_PARTICIPANT = 5

_PARTICIPANT_F_USER_ID = 1
_PARTICIPANT_F_SEC_UID = 5


def _decode_participant(buf: bytes) -> Optional[IMParticipant]:
    fields: dict[int, list] = {}
    try:
        for fnum, _wt, val in iter_fields(buf):
            fields.setdefault(fnum, []).append(val)
    except Exception:  # noqa: BLE001
        return None
    uid = get_first_int(fields, _PARTICIPANT_F_USER_ID)
    sec = get_first_str(fields, _PARTICIPANT_F_SEC_UID)
    if uid <= 0 and not sec:
        return None
    return IMParticipant(user_id=uid, sec_uid=sec)


def _decode_conversation_item(buf: bytes) -> Optional[ConversationListItem]:
    fields: dict[int, list] = {}
    try:
        for fnum, _wt, val in iter_fields(buf):
            fields.setdefault(fnum, []).append(val)
    except Exception:  # noqa: BLE001
        return None

    short_id = get_first_int(fields, _CONV_ITEM_F_SHORT_ID)
    conv_id = get_first_str(fields, _CONV_ITEM_F_CONV_ID)
    if not conv_id:
        return None
    last_msg_raw = get_first_bytes(fields, _CONV_ITEM_F_LAST_MSG)
    last_msg = decode_im_message(last_msg_raw) if last_msg_raw else None
    participants: list[IMParticipant] = []
    for raw in fields.get(_CONV_ITEM_F_PARTICIPANT) or []:
        if not isinstance(raw, (bytes, bytearray)):
            continue
        p = _decode_participant(bytes(raw))
        if p is not None:
            participants.append(p)
    return ConversationListItem(
        conversation_short_id=short_id,
        conversation_id=conv_id,
        last_message=last_msg,
        participants=participants,
        raw=fields,
    )


def decode_list_conversations_response(buf: bytes) -> ListConversationsResult:
    """解 stranger/get_conversation_list 响应：返回会话列表 + last_message。

    响应结构:
      envelope.f6 = inner
      inner.f1000 = {f1=, f2=, f3=, f4 = repeated ConversationListItem}
      envelope.f13 = self_uid (account 数字 user_id)
    """
    if not buf:
        return ListConversationsResult(status_code=-1, status_msg="empty body")

    code, msg, raw = _envelope_status(buf)
    if code != 0:
        return ListConversationsResult(
            status_code=code, status_msg=msg, raw_envelope=raw
        )
    self_uid = get_first_int(raw, 13)

    wrapper = _iter_envelope_inner(buf, _LIST_CONV_INNER_FIELD)
    if wrapper is None:
        return ListConversationsResult(
            status_code=code, status_msg=msg, self_uid=self_uid, raw_envelope=raw
        )

    items: list[ConversationListItem] = []
    for fnum, _wt, val in iter_fields(wrapper):
        if fnum != 4 or not isinstance(val, (bytes, bytearray)):
            continue
        item = _decode_conversation_item(bytes(val))
        if item is not None:
            items.append(item)
    return ListConversationsResult(
        status_code=code,
        status_msg=msg,
        self_uid=self_uid,
        items=items,
        raw_envelope=raw,
    )
