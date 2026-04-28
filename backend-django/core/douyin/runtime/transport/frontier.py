#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: transport/frontier.py
@Desc: ByteDance Frontier wire-format 最小解码器

我们目前没有官方 .proto，因此采用"通用 protobuf 字段扫描 + 启发式特征识别"两步法：

  1. parse_protobuf_fields(data): 不依赖 schema，遍历输出 (field_id, wire_type, value) 列表
  2. decode_frontier_frame(payload): 在第 1 步基础上递归展开嵌套 length-delimited 字段，
     再用关键字 / 长度区间启发式判断这帧是否像"IM 收信"事件，
     如是则返回 FrontierIMHint（含 conversation_hint / text_candidate / sender_hint）

对外只用 hint 作为"该 scan_inbox 了"的信号；正文真正落库仍然走 BrowserTransport.scan_inbox。
这是 fallback 策略：协议层只解出最小必要信号，不替代浏览器扫描的内容真实性。

参考:
  - protobuf wire-format: https://protobuf.dev/programming-guides/encoding/
  - 抓包样本来自 sniffer dump，本模块对未知帧返回 None（worker 退化为节流轮询）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

# Protobuf wire types
WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_LDELIM = 2  # length-delimited (string / bytes / 嵌套 message)
WIRE_FIXED32 = 5

# 已知 IM 帧里常见的"业务关键字"。命中越多越像入向消息。
_IM_KEYWORDS_BYTES = (
    b"conversation",
    b"message",
    b"send",
    b"text",
    b"content",
    b"sec_uid",
    b"msg_id",
    b"server_message_id",
    b"server_msg_id",
    b"client_msg_id",
    b"chat",
    b"im",
    b"text_content",
)

# Frontier 包外层 push payload 常见的 url path / 关键路径字符串
_FRONTIER_URL_HINTS = (
    "frontier",
    "im-api",
    "imapi",
    "msns",
    "wss://",
)


# -------------------- 数据结构 --------------------
ProtoValue = Union[int, bytes, "List[Tuple[int, int, object]]"]


@dataclass
class ProtoField:
    """一个 protobuf 字段的解析结果。"""

    field_id: int
    wire_type: int
    value: ProtoValue
    # 如果 value 是 length-delimited 且能进一步当 protobuf 子消息解析，则这里是子字段
    nested: Optional[List["ProtoField"]] = None
    # 如果 value 是 length-delimited 且能解码为 utf-8 文本，则这里是文本
    text: Optional[str] = None


@dataclass
class FrontierIMHint:
    """
    Frontier 帧解出的 IM 信号（最小必要字段）。

    字段都是 hint，可能为 None。下游只把它当成"有可能是新消息"的信号。
    """

    direction: str  # 'inbound' | 'outbound' | 'unknown'
    conversation_hint: Optional[str] = None
    sender_hint: Optional[str] = None
    text_candidate: Optional[str] = None
    server_ts_ms: Optional[int] = None
    keywords_matched: List[str] = field(default_factory=list)
    # 完整解析树（debug 用）
    fields_dump: List[ProtoField] = field(default_factory=list)


# -------------------- 通用 protobuf 解析 --------------------
def _read_varint(buf: bytes, pos: int) -> Tuple[int, int]:
    """读取一个 varint，返回 (value, new_pos)。"""
    result = 0
    shift = 0
    n = len(buf)
    start = pos
    while True:
        if pos >= n:
            raise ValueError(f"varint 越界 start={start} pos={pos} len={n}")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if (b & 0x80) == 0:
            return result, pos
        shift += 7
        if shift > 63:
            raise ValueError("varint 太长（>10 字节），疑似非 protobuf")


def parse_protobuf_fields(data: bytes, *, max_depth: int = 6) -> List[ProtoField]:
    """
    不依赖 .proto，按 wire-format 直接解出全部字段。

    遇到无法解释的字节序列时抛 ValueError；调用方应自行 try/except。
    """
    return list(_iter_fields(data, depth=0, max_depth=max_depth))


def _iter_fields(data: bytes, *, depth: int, max_depth: int) -> Iterator[ProtoField]:
    pos = 0
    n = len(data)
    while pos < n:
        try:
            tag, pos = _read_varint(data, pos)
        except ValueError as e:
            raise ValueError(f"读取 tag 失败 depth={depth} pos={pos}: {e}") from e
        wire_type = tag & 0x7
        field_id = tag >> 3
        if field_id == 0:
            raise ValueError(f"非法 field_id=0 wire_type={wire_type} depth={depth}")

        if wire_type == WIRE_VARINT:
            value, pos = _read_varint(data, pos)
            yield ProtoField(field_id=field_id, wire_type=wire_type, value=value)
        elif wire_type == WIRE_FIXED64:
            if pos + 8 > n:
                raise ValueError("fixed64 越界")
            value = int.from_bytes(data[pos : pos + 8], "little", signed=False)
            pos += 8
            yield ProtoField(field_id=field_id, wire_type=wire_type, value=value)
        elif wire_type == WIRE_FIXED32:
            if pos + 4 > n:
                raise ValueError("fixed32 越界")
            value = int.from_bytes(data[pos : pos + 4], "little", signed=False)
            pos += 4
            yield ProtoField(field_id=field_id, wire_type=wire_type, value=value)
        elif wire_type == WIRE_LDELIM:
            length, pos = _read_varint(data, pos)
            if length < 0 or pos + length > n:
                raise ValueError(f"length-delim 越界 length={length} pos={pos} n={n}")
            chunk = data[pos : pos + length]
            pos += length
            f = ProtoField(field_id=field_id, wire_type=wire_type, value=chunk)
            # 尝试当作 utf-8 文本（仅打印字符占多数才认）
            txt = _try_decode_text(chunk)
            if txt is not None:
                f.text = txt
            # 尝试当作嵌套 protobuf（仅在文本不像可读字符串时尝试）
            if depth + 1 <= max_depth and (txt is None or _looks_binary(chunk)):
                try:
                    nested = list(_iter_fields(chunk, depth=depth + 1, max_depth=max_depth))
                    if nested:
                        f.nested = nested
                except Exception:  # noqa: BLE001
                    f.nested = None
            yield f
        else:
            # wire_type 3/4 已废弃、6/7 不存在；遇到就跳出
            raise ValueError(f"未知 wire_type={wire_type} depth={depth} pos={pos}")


def _try_decode_text(chunk: bytes) -> Optional[str]:
    if not chunk:
        return ""
    try:
        s = chunk.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    # 至少 70% 是可显示字符（含中日韩）才认作文本
    printable = 0
    for ch in s:
        cp = ord(ch)
        if cp == 0x09 or cp == 0x0A or cp == 0x0D:
            printable += 1
        elif 0x20 <= cp <= 0x7E:
            printable += 1
        elif cp >= 0xA0 and cp != 0xFFFD:
            printable += 1
    if not s:
        return ""
    return s if printable / len(s) >= 0.7 else None


def _looks_binary(chunk: bytes) -> bool:
    """简单判断字节串是否像二进制（非纯 ASCII / 空字节较多）。"""
    if not chunk:
        return False
    nul = chunk.count(0)
    if nul > 0:
        return True
    high = sum(1 for b in chunk if b > 0x7F)
    return high / len(chunk) >= 0.3


# -------------------- Frontier 帧解码 --------------------
def decode_frontier_frame(
    payload: bytes,
    *,
    url: str = "",
) -> Optional[FrontierIMHint]:
    """
    尝试把一帧 WS 数据解释为 Frontier IM 事件。

    返回:
      - FrontierIMHint: 解析成功，且看起来像 IM 业务帧
      - None: 不像 / 解析失败 / 是心跳/控制帧（worker 应忽略）

    设计原则：宁可漏（返回 None），不可误（返回错的 hint）。
    误信号会触发额外 scan_inbox，浪费资源；漏信号最多退化为节流轮询。
    """
    if not payload:
        return None
    # 极短帧大概率是心跳/控制；跳过
    if len(payload) < 12:
        return None

    try:
        fields = parse_protobuf_fields(payload, max_depth=6)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[frontier] payload 不是合法 protobuf: {type(e).__name__}: {e}")
        return None

    if not fields:
        return None

    # 收集所有文本字段，按长度排序
    texts: List[Tuple[int, str]] = []  # (depth, text)
    keywords_hit: set[str] = set()
    candidate_ids: List[str] = []
    candidate_ts: List[int] = []

    def walk(fs: List[ProtoField], depth: int) -> None:
        for f in fs:
            if f.text is not None:
                if f.text:
                    texts.append((depth, f.text))
                    for kw in _IM_KEYWORDS_BYTES:
                        if kw in f.text.encode("utf-8", "ignore"):
                            keywords_hit.add(kw.decode())
                    # sec_uid 候选
                    if f.text.startswith("MS4w") or "sec_uid" in f.text:
                        candidate_ids.append(f.text)
            if f.wire_type in (WIRE_VARINT, WIRE_FIXED64) and isinstance(f.value, int):
                # 1.4e12 ~ 2e13: ms 时间戳常见量级（2014-11-05 ~ 2603）
                if 1_400_000_000_000 <= f.value <= 20_000_000_000_000:
                    candidate_ts.append(f.value)
            if f.nested:
                walk(f.nested, depth + 1)

    walk(fields, 0)

    # 没命中任何 IM 关键字 → 大概率是心跳 / push notify / settings sync
    if not keywords_hit:
        # url 里如果出现 frontier 关键字，至少把它视作"WS 通道活着"，但仍不当作消息
        return None

    # 只有 'send' / 'message' 两个最弱关键字时也忽略
    strong = keywords_hit - {"send", "message"}
    if not strong:
        return None

    # 选最长的 utf-8 文本作为 text_candidate（但限制长度，避免大 payload 干扰）
    text_candidate: Optional[str] = None
    if texts:
        sorted_texts = sorted(texts, key=lambda x: len(x[1]), reverse=True)
        for _, t in sorted_texts:
            stripped = t.strip()
            if 2 <= len(stripped) <= 1024:
                text_candidate = stripped[:512]
                break

    sender_hint: Optional[str] = None
    if candidate_ids:
        sender_hint = candidate_ids[0][:128]

    server_ts_ms: Optional[int] = None
    if candidate_ts:
        # 取最大值（最近的事件）
        server_ts_ms = max(candidate_ts)

    direction = _guess_direction(keywords_hit, fields, url=url)

    return FrontierIMHint(
        direction=direction,
        conversation_hint=sender_hint,
        sender_hint=sender_hint,
        text_candidate=text_candidate,
        server_ts_ms=server_ts_ms,
        keywords_matched=sorted(keywords_hit),
        fields_dump=fields,
    )


def _guess_direction(
    keywords: set[str],
    fields: List[ProtoField],
    *,
    url: str = "",
) -> str:
    """启发式猜测方向：默认 unknown，禁不起则继续；只有非常确定才说 inbound/outbound。"""
    text_blob = b"".join(
        (f.value if isinstance(f.value, bytes) else b"") for f in _flatten(fields)
    ).decode("utf-8", "ignore")
    low = text_blob.lower()

    # 字段名形式的启发：'send_message_request' 通常是出向，'fetch_message_response' / 'message_received' 通常是入向
    if "received" in low or "new_message" in low or "push" in low or "notify" in low:
        return "inbound"
    if "send_message" in low and "request" in low:
        return "outbound"
    return "unknown"


def _flatten(fields: List[ProtoField]) -> Iterator[ProtoField]:
    for f in fields:
        yield f
        if f.nested:
            yield from _flatten(f.nested)


# -------------------- 工具函数 --------------------
def is_im_websocket_url(url: str) -> bool:
    """判断 ws url 是否像抖音 IM 长连接。"""
    if not url:
        return False
    low = url.lower()
    return any(h in low for h in _FRONTIER_URL_HINTS)
