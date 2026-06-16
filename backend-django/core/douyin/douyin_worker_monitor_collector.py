#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
抖音 Worker 进程监控采集器

基于 DB 会话心跳 + Redis 租约 + 分片规则，检测：
- 各分片是否有存活 worker
- 账号是否重复托管（租约与会话 worker_id 不一致）
- 在线账号是否无人托管
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

from core.douyin.douyin_account_model import DouyinAccount
from core.douyin.douyin_session_model import DouyinSession
from core.douyin.runtime.sharding import shard_count, stable_bucket


def _setting_int(name: str, default: int) -> int:
    val = getattr(settings, name, None)
    if val is not None:
        try:
            return int(val)
        except (TypeError, ValueError):
            pass
    return default


def _setting_bool(name: str, default: bool) -> bool:
    val = getattr(settings, name, None)
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).lower() in ('1', 'true', 'yes', 'on')


def _load_managed_accounts() -> list[dict[str, Any]]:
    qs = DouyinAccount.objects.filter(
        status__in=[0, 1, 2],
        work_mode__in=['auto', 'hybrid'],
        is_deleted=False,
    ).exclude(status=3).order_by('-priority', '-sort')
    rows: list[dict[str, Any]] = []
    for a in qs:
        aid = str(a.id)
        rows.append({
            'id': aid,
            'nickname': a.nickname or aid[:8],
            'status': a.status,
            'shard_index': stable_bucket(aid, max(1, shard_count())),
        })
    return rows


def _fetch_redis_leases() -> tuple[bool, dict[str, dict[str, Any]]]:
    """返回 (redis_ok, {account_id: {holder, ttl}})"""
    url = getattr(settings, 'REDIS_URL', None)
    if not url:
        return False, {}
    try:
        import redis
        client = redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
        client.ping()
        prefix = 'douyin:lease:'
        leases: dict[str, dict[str, Any]] = {}
        for key in client.scan_iter(match=f'{prefix}*', count=200):
            account_id = key[len(prefix):] if key.startswith(prefix) else key
            holder = client.get(key) or ''
            ttl = client.ttl(key)
            leases[account_id] = {'holder': holder, 'ttl': int(ttl) if ttl >= 0 else -1}
        client.close()
        return True, leases
    except Exception:  # noqa: BLE001
        return False, {}


def _session_alive(sess: DouyinSession, stale_seconds: int) -> bool:
    if sess.status == 'stopped':
        return False
    if not sess.last_heartbeat:
        return False
    cutoff = timezone.now() - timedelta(seconds=stale_seconds)
    return sess.last_heartbeat >= cutoff


def collect_worker_monitor_overview() -> dict[str, Any]:
    now = timezone.now()
    n = max(1, shard_count())
    lease_enabled = _setting_bool('DOUYIN_WORKER_LEASE_ENABLED', False)
    lease_ttl = _setting_int('DOUYIN_WORKER_LEASE_TTL', 45)
    stale_seconds = _setting_int('DOUYIN_SESSION_STALE_SECONDS', 120)

    accounts = _load_managed_accounts()
    account_by_id = {a['id']: a for a in accounts}

    sessions = list(
        DouyinSession.objects.select_related('account').filter(
            account_id__in=list(account_by_id.keys()) or ['__none__'],
        )
    )
    session_by_account = {str(s.account_id): s for s in sessions}

    redis_ok, lease_map = _fetch_redis_leases()
    nickname_map = {a['id']: a['nickname'] for a in accounts}

    # ── 分片视图 ──
    shard_accounts: dict[int, list[dict]] = defaultdict(list)
    for a in accounts:
        shard_accounts[a['shard_index']].append(a)

    shards_out: list[dict] = []
    for i in range(n):
        accs = shard_accounts.get(i, [])
        active_count = sum(
            1 for a in accs
            if (s := session_by_account.get(a['id'])) and _session_alive(s, stale_seconds)
        )
        worker_ids = sorted({
            session_by_account[a['id']].worker_id
            for a in accs
            if a['id'] in session_by_account
            and _session_alive(session_by_account[a['id']], stale_seconds)
        })
        if not accs:
            status = 'idle'
        elif active_count == 0:
            status = 'missing_worker'
        elif active_count < len(accs):
            status = 'partial'
        else:
            status = 'ok'
        shards_out.append({
            'shard_index': i,
            'expected_account_count': len(accs),
            'active_account_count': active_count,
            'accounts': [
                {
                    'account_id': a['id'],
                    'nickname': a['nickname'],
                    'status': a['status'],
                    'shard_index': a['shard_index'],
                }
                for a in accs
            ],
            'worker_ids': worker_ids,
            'status': status,
        })

    # ── Worker 进程视图（按 worker_id 聚合） ──
    worker_agg: dict[str, dict] = defaultdict(lambda: {
        'accounts': [],
        'last_heartbeat': None,
        'cpu_percent': 0.0,
        'memory_mb': 0.0,
        'alive': False,
    })
    for sess in sessions:
        wid = sess.worker_id or ''
        if not wid:
            continue
        alive = _session_alive(sess, stale_seconds)
        bucket = worker_agg[wid]
        aid = str(sess.account_id)
        acc = account_by_id.get(aid)
        if acc:
            bucket['accounts'].append(acc)
        if sess.last_heartbeat and (
            bucket['last_heartbeat'] is None or sess.last_heartbeat > bucket['last_heartbeat']
        ):
            bucket['last_heartbeat'] = sess.last_heartbeat
        bucket['cpu_percent'] += float(sess.cpu_percent or 0)
        bucket['memory_mb'] += float(sess.memory_mb or 0)
        if alive:
            bucket['alive'] = True

    lease_by_holder: dict[str, int] = defaultdict(int)
    for info in lease_map.values():
        if info.get('holder'):
            lease_by_holder[info['holder']] += 1

    workers_out: list[dict] = []
    for wid, bucket in sorted(worker_agg.items()):
        hostname = wid.split(':', 1)[0] if ':' in wid else wid
        shard_indices = {a['shard_index'] for a in bucket['accounts']}
        inferred = next(iter(shard_indices)) if len(shard_indices) == 1 else None
        if bucket['alive']:
            proc_status = 'alive'
        elif bucket['last_heartbeat']:
            proc_status = 'stale'
        else:
            proc_status = 'dead'
        workers_out.append({
            'worker_id': wid,
            'hostname': hostname,
            'inferred_shard_index': inferred,
            'account_count': len(bucket['accounts']),
            'accounts': [
                {
                    'account_id': a['id'],
                    'nickname': a['nickname'],
                    'status': a['status'],
                    'shard_index': a['shard_index'],
                }
                for a in bucket['accounts']
            ],
            'status': proc_status,
            'last_heartbeat': bucket['last_heartbeat'],
            'cpu_percent': round(bucket['cpu_percent'], 1),
            'memory_mb': round(bucket['memory_mb'], 1),
            'lease_count': lease_by_holder.get(wid, 0),
        })

    # 活跃 worker：有心跳但尚未出现在 session 表中的情况（刚启动）— 已通过 sessions 覆盖

    # ── 租约明细 ──
    leases_out: list[dict] = []
    for account_id, info in sorted(lease_map.items()):
        sess = session_by_account.get(account_id)
        session_wid = sess.worker_id if sess else None
        holder = info.get('holder') or ''
        consistent = (not session_wid) or (session_wid == holder)
        leases_out.append({
            'account_id': account_id,
            'nickname': nickname_map.get(account_id, account_id[:8]),
            'holder_worker_id': holder,
            'ttl_seconds': info.get('ttl', -1),
            'session_worker_id': session_wid,
            'consistent': consistent,
        })

    # ── 问题检测 ──
    issues: list[dict] = []

    if not redis_ok:
        issues.append({
            'level': 'error',
            'code': 'redis_unavailable',
            'title': 'Redis 不可用',
            'detail': '无法连接 Redis，租约状态未知；多 worker 场景下存在重复托管风险。',
        })

    if lease_enabled and redis_ok and len(lease_map) != sum(1 for s in sessions if _session_alive(s, stale_seconds)):
        issues.append({
            'level': 'warning',
            'code': 'lease_session_count_mismatch',
            'title': '租约数与会话数不一致',
            'detail': (
                f'活跃租约 {len(lease_map)} 个，存活会话 '
                f'{sum(1 for s in sessions if _session_alive(s, stale_seconds))} 个。'
            ),
        })

    for item in leases_out:
        if not item['consistent']:
            issues.append({
                'level': 'error',
                'code': 'lease_session_mismatch',
                'title': '租约与会话 worker 不一致',
                'detail': (
                    f"账号 {item['nickname']} 租约持有者 {item['holder_worker_id']}，"
                    f"会话 worker {item['session_worker_id']}。"
                ),
            })

    for shard in shards_out:
        if shard['status'] == 'missing_worker' and shard['expected_account_count'] > 0:
            issues.append({
                'level': 'error',
                'code': 'shard_missing_worker',
                'title': f"分片 {shard['shard_index']} 无存活 worker",
                'detail': (
                    f"该分片有 {shard['expected_account_count']} 个待托管账号，"
                    f"但没有任何 worker 心跳。"
                ),
            })
        elif shard['status'] == 'partial':
            issues.append({
                'level': 'warning',
                'code': 'shard_partial_coverage',
                'title': f"分片 {shard['shard_index']} 部分账号未托管",
                'detail': (
                    f"期望 {shard['expected_account_count']} 个，"
                    f"实际活跃 {shard['active_account_count']} 个。"
                ),
            })

    # 同一账号多个 worker（理论上 OneToOne session 不会，但租约+旧 worker 可能短暂出现）
    holder_accounts: dict[str, list[str]] = defaultdict(list)
    for item in leases_out:
        holder_accounts[item['holder_worker_id']].append(item['account_id'])
    for wid, acc_ids in holder_accounts.items():
        shard_set = {account_by_id[a]['shard_index'] for a in acc_ids if a in account_by_id}
        if len(shard_set) > 1:
            issues.append({
                'level': 'error',
                'code': 'worker_cross_shard',
                'title': 'Worker 跨分片持锁',
                'detail': f"Worker {wid} 同时持有多个分片的租约，请检查 SHARD_INDEX 配置或遗留容器。",
            })

    for wid, bucket in worker_agg.items():
        shard_set = {a['shard_index'] for a in bucket['accounts']}
        if len(shard_set) > 1:
            issues.append({
                'level': 'error',
                'code': 'worker_cross_shard_session',
                'title': 'Worker 跨分片托管会话',
                'detail': f"Worker {wid} 同时托管多个分片账号，可能存在配置错误或遗留 worker。",
            })

    online_no_session = DouyinAccount.objects.filter(
        status=1, work_mode__in=['auto', 'hybrid'], is_deleted=False,
    )
    for acc in online_no_session:
        aid = str(acc.id)
        if aid not in account_by_id:
            continue
        sess = session_by_account.get(aid)
        if not sess or not _session_alive(sess, stale_seconds):
            issues.append({
                'level': 'warning',
                'code': 'online_no_alive_session',
                'title': '在线账号无存活会话',
                'detail': f"账号「{acc.nickname}」标记在线，但 worker 未上报心跳。",
            })

    active_sessions = sum(1 for s in sessions if _session_alive(s, stale_seconds))

    return {
        'timestamp': now,
        'shard_count': n,
        'shard_index_hint': f'0..{n - 1}（md5(account_id) % {n}）',
        'lease_enabled': lease_enabled,
        'lease_ttl': lease_ttl,
        'redis_ok': redis_ok,
        'managed_account_total': len(accounts),
        'active_session_total': active_sessions,
        'lease_total': len(lease_map) if redis_ok else 0,
        'worker_process_total': len(workers_out),
        'shards': shards_out,
        'workers': workers_out,
        'leases': leases_out,
        'issues': issues,
    }
