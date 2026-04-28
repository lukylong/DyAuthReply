#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: transport/wire/codec.py
@Desc: 极简 protobuf wire format 编解码

Wire format spec: https://protobuf.dev/programming-guides/encoding/

只支持 4 种 tag type 中的 2 种（够 IM 协议用了）：
  0 = VARINT       (int32/int64/bool/enum)
  2 = LEN          (string/bytes/embedded message)

field tag = (field_number << 3) | wire_type

不实现 fixed32 / fixed64，因为 sniff 报告里没看到 IM 接口用这两个类型。
真出现了再补。

设计目标：
  - 编码：encode_field(num, value) → bytes，自动判断类型
  - 解码：iter_fields(buf) → 迭代器返回 (field_number, wire_type, raw_value)
  - decode_message：把整段 buf 解成 dict[field_num] = list[raw_value]，重复字段全保留
"""
from __future__ import annotations

from typing import Iterator, Union


WIRE_VARINT = 0
WIRE_LEN = 2


def encode_varint(value: int) -> bytes:
    """无符号 varint 编码。负数请上层先 zigzag。"""
    if value < 0:
        # protobuf 对 int64 负数补码到 64 位，但 IM 几乎不传负数；
        # 真要传负数 caller 自己处理 zigzag/补码。
        raise ValueError(f"encode_varint 不支持负数: {value}")
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def decode_varint(buf: bytes, offset: int) -> tuple[int, int]:
    """从 buf[offset:] 解一个 varint，返回 (value, new_offset)。"""
    value = 0
    shift = 0
    o = offset
    while True:
        if o >= len(buf):
            raise ValueError("varint 截断：到达 buf 末尾仍未结束")
        b = buf[o]
        o += 1
        value |= (b & 0x7F) << shift
        if not (b & 0x80):
            return value, o
        shift += 7
        if shift > 63:
            raise ValueError("varint 超过 64 位")


def encode_tag(field_number: int, wire_type: int) -> bytes:
    if field_number <= 0 or field_number > 536870911:
        raise ValueError(f"field_number 越界: {field_number}")
    if wire_type not in (WIRE_VARINT, WIRE_LEN):
        raise ValueError(f"暂不支持 wire_type={wire_type}")
    return encode_varint((field_number << 3) | wire_type)


def encode_field(field_number: int, value: Union[int, bool, str, bytes]) -> bytes:
    """根据 value 类型自动判断 wire_type 并编码出 (tag + payload) 字节。

    类型映射：
      bool / int     → VARINT (0)
      str            → LEN (2), utf-8 编码
      bytes/bytearray→ LEN (2)，原样

    嵌套 message 由 caller 先 encode 成 bytes 再传进来。
    """
    if isinstance(value, bool):
        return encode_tag(field_number, WIRE_VARINT) + encode_varint(1 if value else 0)
    if isinstance(value, int):
        return encode_tag(field_number, WIRE_VARINT) + encode_varint(value)
    if isinstance(value, str):
        payload = value.encode("utf-8")
        return encode_tag(field_number, WIRE_LEN) + encode_varint(len(payload)) + payload
    if isinstance(value, (bytes, bytearray, memoryview)):
        payload = bytes(value)
        return encode_tag(field_number, WIRE_LEN) + encode_varint(len(payload)) + payload
    raise TypeError(f"encode_field 暂不支持类型 {type(value).__name__}")


def iter_fields(buf: bytes) -> Iterator[tuple[int, int, Union[int, bytes]]]:
    """迭代 buf 里每个字段，产出 (field_number, wire_type, raw_value)。

    raw_value：
      VARINT → int
      LEN    → bytes（仅 payload；caller 决定是否当 string / 嵌套 message 解）

    遇到不支持的 wire_type 会抛 ValueError。
    """
    o = 0
    n = len(buf)
    while o < n:
        tag, o = decode_varint(buf, o)
        field_number = tag >> 3
        wire_type = tag & 0x07
        if wire_type == WIRE_VARINT:
            value, o = decode_varint(buf, o)
            yield field_number, wire_type, value
        elif wire_type == WIRE_LEN:
            length, o = decode_varint(buf, o)
            if o + length > n:
                raise ValueError(
                    f"LEN 字段越界：field={field_number} length={length} "
                    f"剩余={n - o}"
                )
            payload = buf[o : o + length]
            o += length
            yield field_number, wire_type, payload
        else:
            # fixed32 / fixed64 / SGROUP / EGROUP — 暂不支持
            raise ValueError(
                f"不支持 wire_type={wire_type} field={field_number} offset={o}"
            )


def decode_message(buf: bytes) -> dict[int, list[Union[int, bytes]]]:
    """
    把整段 protobuf message 解成 {field_number: [value, ...]}。
    同一 field_number 出现多次（repeated）会全部保留为 list。
    """
    out: dict[int, list[Union[int, bytes]]] = {}
    for fnum, _wtype, value in iter_fields(buf):
        out.setdefault(fnum, []).append(value)
    return out


def get_first_int(msg: dict[int, list], field_number: int, default: int = 0) -> int:
    """便捷：取第一个 varint 字段值，缺省 default。"""
    items = msg.get(field_number) or []
    if not items:
        return default
    v = items[0]
    return int(v) if isinstance(v, int) else default


def get_first_bytes(msg: dict[int, list], field_number: int) -> bytes:
    """便捷：取第一个 LEN 字段的 bytes，缺省 b''。"""
    items = msg.get(field_number) or []
    if not items:
        return b""
    v = items[0]
    return v if isinstance(v, (bytes, bytearray)) else b""


def get_first_str(msg: dict[int, list], field_number: int, default: str = "") -> str:
    raw = get_first_bytes(msg, field_number)
    if not raw:
        return default
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return default
