#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: core/douyin/runtime/sharding.py
@Desc: 多 worker 水平分片 —— 按 account_id 稳定哈希取模，把账号集合切给多个 worker 实例。

面向 200+ 账号规模：单进程压榨到极限后（见 P3），用多 worker 分片横向扩展。
每个 worker 通过环境变量声明自己负责的分片：

    DOUYIN_WORKER_SHARD_COUNT = 总分片数 N（默认 1，即单 worker 托管全部）
    DOUYIN_WORKER_SHARD_INDEX = 本实例分片号 i ∈ [0, N)（默认 0）

账号归属：owns(account_id) := stable_hash(account_id) % N == i。
稳定哈希用 md5（与 Python 进程级 hash 随机化无关），保证跨进程/重启一致 —— 同一账号
永远落在同一分片，配合 P4 的 Redis 租约锁实现「一个账号同一时刻只被一个 worker 托管」。
"""
from __future__ import annotations

import hashlib
import logging

logger = logging.getLogger(__name__)


def _setting_int(name: str, default: int) -> int:
    """优先 settings，回退环境变量，再回退默认值（worker 进程可能未走 settings 注入）。"""
    try:
        from django.conf import settings
        val = getattr(settings, name, None)
        if val is not None:
            return int(val)
    except Exception:  # noqa: BLE001
        pass
    import os
    raw = os.environ.get(name)
    if raw is not None and str(raw).strip() != "":
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    return default


def shard_count() -> int:
    """总分片数 N（>=1）。"""
    return max(1, _setting_int("DOUYIN_WORKER_SHARD_COUNT", 1))


def shard_index() -> int:
    """本实例分片号 i，规范化到 [0, N)。"""
    n = shard_count()
    i = _setting_int("DOUYIN_WORKER_SHARD_INDEX", 0)
    if i < 0 or i >= n:
        logger.warning(f"[shard] SHARD_INDEX={i} 超出 [0,{n})，已取模归一")
        i %= n
    return i


def stable_bucket(account_id: str, n: int) -> int:
    """account_id 的稳定分桶号 ∈ [0, n)。用 md5 避免进程级 hash 随机化导致跨进程不一致。"""
    if n <= 1:
        return 0
    digest = hashlib.md5(str(account_id).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % n


def owns(account_id: str, *, index: int | None = None, count: int | None = None) -> bool:
    """本分片是否负责该账号。index/count 可显式传入（便于单测），默认读 settings/env。"""
    n = count if count is not None else shard_count()
    i = index if index is not None else shard_index()
    if n <= 1:
        return True
    return stable_bucket(account_id, n) == i


def describe() -> str:
    """日志友好的分片描述。"""
    return f"shard {shard_index()}/{shard_count()}"
