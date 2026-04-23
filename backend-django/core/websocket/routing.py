#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: routing.py
@Desc: 
"""
from django.urls import re_path

from . import consumers

# WebSocket URL patterns
websocket_urlpatterns = [
    # WebSocket测试连接
    re_path(r'ws/test/$', consumers.TestWebSocketConsumer.as_asgi()),

    # 通知推送连接
    re_path(r'ws/notifications/$', consumers.NotificationConsumer.as_asgi()),
    re_path(r'ws/notification/$', consumers.NotificationConsumer.as_asgi()),

    # 服务器监控连接
    re_path(r'ws/server-monitor/$', consumers.ServerMonitorConsumer.as_asgi()),

    # Redis监控连接
    re_path(r'ws/redis-monitor/$', consumers.RedisMonitorConsumer.as_asgi()),

    # 数据库监控连接
    re_path(r'ws/database-monitor/$', consumers.DatabaseMonitorConsumer.as_asgi()),
]
