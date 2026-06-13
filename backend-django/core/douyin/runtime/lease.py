#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: core/douyin/runtime/lease.py
@Desc: 账号 Redis 租约锁 —— 保证「一个账号同一时刻只被一个 worker 托管」。

与 sharding.py 的关系：
  - 分片（sharding）把账号集合静态切给各 worker，是常态下的无冲突划分。
  - 租约（lease）是动态兜底：rebalance/扩缩容/分片配置不一致/worker 崩溃的瞬间，
    可能出现两个 worker 同时认领同一账号；租约用 Redis SET NX + TTL 抢占，
    只有持锁者才托管，崩溃后 TTL 到期自动转移给其它 worker。

键：douyin:lease:<account_id>，值：worker_id，TTL=DOUYIN_WORKER_LEASE_TTL（默认 45s）。
托管期间由 worker 的续约循环每 TTL/3 续期一次（CAS：值匹配才续）。

默认关闭（DOUYIN_WORKER_LEASE_ENABLED=false），单 worker 部署行为不变；
多 worker 部署时开启以获得崩溃自动转移能力。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_LEASE_PREFIX = "douyin:lease:"

# CAS 续约：仅当当前值等于本 worker 时刷新 TTL（避免续掉别人的锁）
_RENEW_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
else
    return 0
end
"""

# CAS 释放：仅当当前值等于本 worker 时删除
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


def lease_enabled() -> bool:
    try:
        from django.conf import settings
        return bool(getattr(settings, "DOUYIN_WORKER_LEASE_ENABLED", False))
    except Exception:  # noqa: BLE001
        return False


def lease_ttl() -> int:
    try:
        from django.conf import settings
        return max(10, int(getattr(settings, "DOUYIN_WORKER_LEASE_TTL", 45)))
    except Exception:  # noqa: BLE001
        return 45


def _key(account_id: str) -> str:
    return f"{_LEASE_PREFIX}{account_id}"


def _wid(worker_id: str) -> bytes:
    return worker_id.encode("utf-8")


async def acquire(redis, account_id: str, worker_id: str, ttl: int | None = None) -> bool:
    """抢占账号租约。已被自己持有也返回 True；被他人持有返回 False。"""
    if redis is None:
        return True
    ttl = ttl or lease_ttl()
    key = _key(account_id)
    try:
        ok = await redis.set(key, _wid(worker_id), nx=True, ex=ttl)
        if ok:
            return True
        cur = await redis.get(key)
        if cur == _wid(worker_id):
            await redis.expire(key, ttl)
            return True
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[lease] acquire 异常 account={account_id}: {e}")
        # Redis 抖动时不阻断托管（可用性优先），退化为分片保证不冲突
        return True


async def renew(redis, account_id: str, worker_id: str, ttl: int | None = None) -> bool:
    """续约（CAS）。返回 False 表示锁已不属于自己（应停掉本地托管）。"""
    if redis is None:
        return True
    ttl = ttl or lease_ttl()
    try:
        res = await redis.eval(_RENEW_LUA, 1, _key(account_id), _wid(worker_id), str(ttl))
        return bool(res)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[lease] renew 异常 account={account_id}: {e}")
        return True  # 抖动不误杀本地托管


async def release(redis, account_id: str, worker_id: str) -> None:
    """释放租约（CAS，仅删自己的）。"""
    if redis is None:
        return
    try:
        await redis.eval(_RELEASE_LUA, 1, _key(account_id), _wid(worker_id))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[lease] release 异常 account={account_id}: {e}")
