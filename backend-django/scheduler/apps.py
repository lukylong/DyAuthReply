#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: apps.py
@Desc: Scheduler App Config - 定时任务应用配置 - 在 Django 启动时自动启动调度器
"""
"""
Scheduler App Config - 定时任务应用配置
在 Django 启动时自动启动调度器
"""
import logging

from django.apps import AppConfig

from application import settings

logger = logging.getLogger(__name__)


class SchedulerConfig(AppConfig):
    """定时任务应用配置"""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'scheduler'
    verbose_name = '定时任务管理'

    def ready(self):
        """
        应用准备就绪时的回调
        
        在这里自动启动调度器
        """
        import os

        # 判断是否应该启动调度器
        # 1. Django runserver 开发模式：只在主进程启动
        # 2. Gunicorn/Uvicorn 生产模式：通过环境变量控制
        should_start = False

        # 开发模式检测
        if os.environ.get('RUN_MAIN') == 'true' or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
            should_start = True

        # 生产模式检测（Gunicorn）
        # 设置环境变量 ENABLE_SCHEDULER=true 来启动调度器
        # 建议只在一个 worker 中启动，避免多个调度器实例
        elif os.environ.get('ENABLE_SCHEDULER') == 'true':
            should_start = True

        if should_start and settings.ENABLE_SCHEDULER:
            try:
                from scheduler.service import scheduler_service

                # 启动调度器
                if not scheduler_service.is_running():
                    scheduler_service.start()
                    logger.info("定时任务调度器已自动启动")

                    # 初始化工作流任务超时检查定时任务
                    self._init_workflow_timeout_job()
            except Exception as e:
                logger.error(f"定时任务调度器启动失败: {str(e)}")

    def _init_workflow_timeout_job(self):
        """初始化工作流任务超时检查定时任务"""
        try:
            from core.workflow.engine.task_timeout_process import create_timeout_check_job
            if create_timeout_check_job():
                logger.info("工作流任务超时检查定时任务已创建")
        except Exception as e:
            logger.error(f"创建工作流任务超时检查定时任务失败: {e}")
