#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音 Worker 进程监控 Schema"""
from datetime import datetime
from typing import List, Optional

from ninja import Schema


class WorkerMonitorAccountBrief(Schema):
    account_id: str
    nickname: str
    status: int
    shard_index: int


class WorkerMonitorLeaseItem(Schema):
    account_id: str
    nickname: str
    holder_worker_id: str
    ttl_seconds: int
    session_worker_id: Optional[str] = None
    consistent: bool = True


class WorkerMonitorProcessItem(Schema):
    worker_id: str
    hostname: str
    inferred_shard_index: Optional[int] = None
    account_count: int
    accounts: List[WorkerMonitorAccountBrief]
    status: str  # alive | stale | dead
    last_heartbeat: Optional[datetime] = None
    cpu_percent: float = 0
    memory_mb: float = 0
    lease_count: int = 0


class WorkerMonitorShardItem(Schema):
    shard_index: int
    expected_account_count: int
    active_account_count: int
    accounts: List[WorkerMonitorAccountBrief]
    worker_ids: List[str]
    status: str  # ok | missing_worker | partial | idle


class WorkerMonitorIssueItem(Schema):
    level: str  # error | warning | info
    code: str
    title: str
    detail: str


class WorkerMonitorOverviewOut(Schema):
    timestamp: datetime
    shard_count: int
    shard_index_hint: str
    lease_enabled: bool
    lease_ttl: int
    redis_ok: bool
    managed_account_total: int
    active_session_total: int
    lease_total: int
    worker_process_total: int
    shards: List[WorkerMonitorShardItem]
    workers: List[WorkerMonitorProcessItem]
    leases: List[WorkerMonitorLeaseItem]
    issues: List[WorkerMonitorIssueItem]
