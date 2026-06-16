#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音 Worker 进程监控 API"""
from ninja import Router

from core.douyin.douyin_worker_monitor_collector import collect_worker_monitor_overview
from core.douyin.douyin_worker_monitor_schema import WorkerMonitorOverviewOut

router = Router()


@router.get(
    '/worker-monitor/overview',
    response=WorkerMonitorOverviewOut,
    summary='Worker 进程监控概览',
)
def worker_monitor_overview(request):
    """分片分布、存活 worker、Redis 租约与告警一览。"""
    return collect_worker_monitor_overview()
