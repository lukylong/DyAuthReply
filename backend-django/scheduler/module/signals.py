#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: signals.py
@Desc: Scheduler Signals - 定时任务信号处理 - 用于在任务变更时通知调度器进程
"""
"""
Scheduler Signals - 定时任务信号处理
用于在任务变更时通知调度器进程
"""
import json
import logging
from datetime import datetime
from pathlib import Path

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from scheduler.models import SchedulerJob

logger = logging.getLogger(__name__)

# 信号文件路径（用于进程间通信）
SIGNAL_FILE = Path(__file__).parent.parent / 'tmp' / 'scheduler_signals.json'


def ensure_signal_dir():
    """确保信号目录存在"""
    SIGNAL_FILE.parent.mkdir(parents=True, exist_ok=True)


def write_signal(action: str, job_code: str):
    """
    写入信号文件
    
    Args:
        action: 操作类型（create/update/delete）
        job_code: 任务编码
    """
    try:
        ensure_signal_dir()

        # 读取现有信号
        signals = []
        if SIGNAL_FILE.exists():
            try:
                with open(SIGNAL_FILE, 'r', encoding='utf-8') as f:
                    signals = json.load(f)
            except:
                signals = []

        # 添加新信号
        signals.append({
            'action': action,
            'job_code': job_code,
            'timestamp': datetime.now().isoformat(),
        })

        # 保持最近100条信号
        signals = signals[-100:]

        # 写入文件
        with open(SIGNAL_FILE, 'w', encoding='utf-8') as f:
            json.dump(signals, f, ensure_ascii=False, indent=2)

        logger.debug(f"写入调度器信号: {action} - {job_code}")

    except Exception as e:
        logger.error(f"写入调度器信号失败: {str(e)}")


@receiver(post_save, sender=SchedulerJob)
def on_job_saved(sender, instance, created, **kwargs):
    """
    任务保存后的信号处理
    
    - 创建任务：通知调度器添加任务
    - 更新任务：通知调度器修改任务
    """
    action = 'create' if created else 'update'
    write_signal(action, instance.code)
    logger.info(f"任务 {action}: {instance.code}")


@receiver(post_delete, sender=SchedulerJob)
def on_job_deleted(sender, instance, **kwargs):
    """
    任务删除后的信号处理
    
    通知调度器移除任务
    """
    write_signal('delete', instance.code)
    logger.info(f"任务删除: {instance.code}")
