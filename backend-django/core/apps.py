#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: apps.py
@Desc: Core Apps - Django 应用配置
"""
"""
Core Apps - Django 应用配置
"""
from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Core 应用配置"""
    name = 'core'
    verbose_name = '核心权限管理'

    def ready(self):
        """应用初始化时执行"""
        import os

        from django.conf import settings

        # 自托管下载：Web 进程后台轮询新版本，先同步安装包再发布版本号（见 download_sync）。
        if (
            os.environ.get("ROLE") == "web"
            and settings.DOWNLOAD_PUBLIC_BASE_URL
            and os.environ.get("DOWNLOAD_AUTO_SYNC", "true").lower() != "false"
        ):
            from core.license.download_sync import start_background

            start_background()
