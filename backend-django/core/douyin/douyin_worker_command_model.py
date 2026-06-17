#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""客户端模式：API → Worker 命令队列（SQLite，无需 Redis）。"""
from django.db import models

from common.fu_model import RootModel


class DouyinWorkerCommand(RootModel):
    """待 Worker 消费的命令（manual_reply / pause / logout 等）。"""

    channel = models.CharField(max_length=255, db_index=True, help_text='同 Redis 频道名')
    payload = models.JSONField(default=dict, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = 'core_douyin_worker_command'
        ordering = ['sys_create_datetime']
        indexes = [
            models.Index(fields=['consumed_at', 'sys_create_datetime']),
        ]
