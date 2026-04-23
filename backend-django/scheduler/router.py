#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: router.py
@Desc: Core Router - 核心模块路由配置 - 统一管理 core 模块的所有路由
"""
"""
Core Router - 核心模块路由配置
统一管理 core 模块的所有路由
"""
from ninja import Router

from scheduler.api import router

# 创建核心模块的总路由
scheduler_router = Router()

# 注册子路由
scheduler_router.add_router("", router, tags=["Scheduler"])
