#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: sign/_gmssl_compat.py
@Desc: gmssl 最小兼容层 —— 纯 Python SM3

vendored abogus.py 原本依赖 `from gmssl import sm3, func`，仅用到：
    sm3.sm3_hash(func.bytes_to_list(b)) -> 64 位十六进制字符串

容器内常因网络（MITM 证书）装不上 gmssl，这里内置一份标准 SM3（GM/T 0004-2012），
对外暴露与 gmssl 同名的 `sm3.sm3_hash` 与 `func.bytes_to_list`，做到 import 即替换、零行为差异。

自检向量：sm3(b"abc") ==
  66c7f0f4 62eeedd9 d1f2d46b dc10e4e2 4167c487 5cf2f7a2 297da02b 8f4ba8e0
"""
from __future__ import annotations

import types
from typing import List, Union

_IV = [
    0x7380166F, 0x4914B2B9, 0x172442D7, 0xDA8A0600,
    0xA96F30BC, 0x163138AA, 0xE38DEE4D, 0xB0FB0E4E,
]

_MASK = 0xFFFFFFFF


def _rotl(x: int, n: int) -> int:
    n &= 31
    x &= _MASK
    return ((x << n) | (x >> (32 - n))) & _MASK


def _p0(x: int) -> int:
    return x ^ _rotl(x, 9) ^ _rotl(x, 17)


def _p1(x: int) -> int:
    return x ^ _rotl(x, 15) ^ _rotl(x, 23)


def _ff(x: int, y: int, z: int, j: int) -> int:
    if j < 16:
        return x ^ y ^ z
    return (x & y) | (x & z) | (y & z)


def _gg(x: int, y: int, z: int, j: int) -> int:
    if j < 16:
        return x ^ y ^ z
    return (x & y) | ((~x & _MASK) & z)


def _cf(v: List[int], block: bytes) -> List[int]:
    w = [0] * 68
    for i in range(16):
        w[i] = (
            (block[4 * i] << 24)
            | (block[4 * i + 1] << 16)
            | (block[4 * i + 2] << 8)
            | block[4 * i + 3]
        )
    for i in range(16, 68):
        w[i] = (
            _p1(w[i - 16] ^ w[i - 9] ^ _rotl(w[i - 3], 15))
            ^ _rotl(w[i - 13], 7)
            ^ w[i - 6]
        ) & _MASK

    w1 = [(w[i] ^ w[i + 4]) & _MASK for i in range(64)]

    a, b, c, d, e, f, g, h = v
    for j in range(64):
        t = 0x79CC4519 if j < 16 else 0x7A879D8A
        ss1 = _rotl((_rotl(a, 12) + e + _rotl(t, j)) & _MASK, 7)
        ss2 = ss1 ^ _rotl(a, 12)
        tt1 = (_ff(a, b, c, j) + d + ss2 + w1[j]) & _MASK
        tt2 = (_gg(e, f, g, j) + h + ss1 + w[j]) & _MASK
        d = c
        c = _rotl(b, 9)
        b = a
        a = tt1
        h = g
        g = _rotl(f, 19)
        f = e
        e = _p0(tt2)

    return [
        (v[0] ^ a) & _MASK,
        (v[1] ^ b) & _MASK,
        (v[2] ^ c) & _MASK,
        (v[3] ^ d) & _MASK,
        (v[4] ^ e) & _MASK,
        (v[5] ^ f) & _MASK,
        (v[6] ^ g) & _MASK,
        (v[7] ^ h) & _MASK,
    ]


def _sm3_digest(msg: bytes) -> bytes:
    length = len(msg)
    bit_len = length * 8
    msg += b"\x80"
    while len(msg) % 64 != 56:
        msg += b"\x00"
    msg += bit_len.to_bytes(8, "big")

    v = _IV[:]
    for i in range(0, len(msg), 64):
        v = _cf(v, msg[i:i + 64])

    return b"".join(x.to_bytes(4, "big") for x in v)


def bytes_to_list(data: Union[bytes, bytearray, List[int]]) -> List[int]:
    """与 gmssl.func.bytes_to_list 一致：把 bytes 转成 int 列表。"""
    return list(data)


def sm3_hash(msg: List[int]) -> str:
    """与 gmssl.sm3.sm3_hash 一致：入参为 int 列表，返回 64 位小写十六进制。"""
    return _sm3_digest(bytes(msg)).hex()


# 暴露与 `from gmssl import sm3, func` 同名的对象
sm3 = types.SimpleNamespace(sm3_hash=sm3_hash)
func = types.SimpleNamespace(bytes_to_list=bytes_to_list)


if __name__ == "__main__":
    got = sm3_hash(bytes_to_list(b"abc"))
    expect = "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0"
    print("SM3(abc) =", got)
    print("OK" if got == expect else f"FAIL expect={expect}")
