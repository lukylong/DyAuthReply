#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: redis_monitor_api.py
@Desc: 获取项目Redis配置 - redis_host = getattr(settings, 'REDIS_HOST', '127.0.0.1')
"""
# time: 2024/redis_monitor/12/redis_monitor/20
# file: redis_monitor_api.py
# author: AI Assistant

from django.conf import settings
from ninja import Router

from core.redis_monitor.redis_collector import RedisInfoCollector
from core.redis_monitor.redis_monitor_schema import (
    RedisMonitorOverviewSchema,
    RedisRealtimeStatsSchema,
    RedisConnectionTestSchema,
    RedisConfigSchema,
)

router = Router()


def get_redis_config():
    """获取项目Redis配置"""
    redis_host = getattr(settings, 'REDIS_HOST', '127.0.0.1')
    redis_port = getattr(settings, 'REDIS_PORT', 6379)
    redis_password = getattr(settings, 'REDIS_PASSWORD', None)
    redis_db = int(getattr(settings, 'REDIS_DB', '0'))

    # 如果密码为空字符串，设置为None
    if redis_password == '':
        redis_password = None

    return redis_host, redis_port, redis_password, redis_db


@router.get("/redis_monitor/overview", response=RedisMonitorOverviewSchema)
def get_redis_monitor_overview(request):
    """获取Redis监控概览信息"""
    redis_host, redis_port, redis_password, redis_db = get_redis_config()

    collector = RedisInfoCollector(
        host=redis_host,
        port=redis_port,
        password=redis_password,
        db=redis_db
    )

    data = collector.get_all_info('project_redis', '项目Redis')
    return RedisMonitorOverviewSchema(**data)


@router.get("/redis_monitor/realtime", response=RedisRealtimeStatsSchema)
def get_redis_realtime_stats(request):
    """获取Redis实时统计信息"""
    redis_host, redis_port, redis_password, redis_db = get_redis_config()

    collector = RedisInfoCollector(
        host=redis_host,
        port=redis_port,
        password=redis_password,
        db=redis_db
    )

    data = collector.get_realtime_stats('project_redis')
    return RedisRealtimeStatsSchema(**data)


@router.post("/redis_monitor/test", response=RedisConnectionTestSchema)
def test_redis_connection(request):
    """测试Redis连接"""
    redis_host, redis_port, redis_password, redis_db = get_redis_config()

    collector = RedisInfoCollector(
        host=redis_host,
        port=redis_port,
        password=redis_password,
        db=redis_db
    )

    result = collector.test_connection()
    return RedisConnectionTestSchema(**result)


@router.get("/redis_monitor/config", response=RedisConfigSchema)
def get_redis_config_info(request):
    """获取Redis配置信息"""
    redis_host, redis_port, redis_password, redis_db = get_redis_config()

    return RedisConfigSchema(
        host=redis_host,
        port=redis_port,
        database=redis_db,
        has_password=bool(redis_password),
        redis_url=getattr(settings, 'REDIS_URL', '')
    )
