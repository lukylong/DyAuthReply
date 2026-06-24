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

    # 抖音托管事件推送（扫码二维码 / 登录结果 / 回复事件等）
    re_path(r'ws/douyin/$', consumers.DouyinConsumer.as_asgi()),

    # 桌面客户端实时私信（方案 D：data_version 探测推送，仅本机回环鉴权）
    re_path(r'ws/client/douyin/$', consumers.DouyinClientRealtimeConsumer.as_asgi()),
]
