#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: asgi.py
@Desc: ASGI config for application project. - 
"""
"""
ASGI config for application project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.0/howto/deployment/asgi/
"""

import os

import django
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.sessions import SessionMiddlewareStack
from django.core.asgi import get_asgi_application

# 设置Django设置模块
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'application.settings')

# 初始化Django
django.setup()

# 初始化Django ASGI应用
django_asgi_app = get_asgi_application()

# 导入WebSocket路由（必须在django.setup()之后）
from core.websocket.routing import websocket_urlpatterns

# 配置ASGI应用程序以支持HTTP和WebSocket
# 使用 SessionMiddlewareStack 而非 AuthMiddlewareStack，因为我们在 Consumer 中使用自定义 Token 认证
application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": SessionMiddlewareStack(
        URLRouter(websocket_urlpatterns)
    ),
})
