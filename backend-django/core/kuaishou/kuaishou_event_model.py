#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: kuaishou_event_model.py
@Desc: Kuaishou Event - 快手系统事件流
"""
from django.db import models

from common.fu_model import RootModel


class KuaishouEvent(RootModel):
    """快手运行时事件（登录、掉线、风控告警、发送失败等）"""

    LEVEL_CHOICES = [
        ('info', '信息'),
        ('warning', '警告'),
        ('error', '错误'),
        ('critical', '严重'),
    ]

    EVENT_TYPE_CHOICES = [
        ('login_success', '登录成功'),
        ('login_expired', '登录失效'),
        ('qr_generated', '二维码生成'),
        ('qr_scanned', '已扫码'),
        ('worker_started', 'Worker 启动'),
        ('worker_stopped', 'Worker 停止'),
        ('session_paused', '会话暂停'),
        ('session_resumed', '会话恢复'),
        ('message_received', '收到消息'),
        ('reply_sent', '已发送回复'),
        ('reply_failed', '回复失败'),
        ('risk_alert', '风控告警'),
        ('rate_limit', '触发限流'),
        ('quota_exceeded', '配额超限'),
        ('blacklist_hit', '命中黑名单'),
        ('unknown_error', '未知异常'),
    ]

    account = models.ForeignKey(
        to='core.KuaishouAccount',
        on_delete=models.CASCADE,
        db_constraint=False,
        null=True, blank=True,
        related_name='events',
        help_text="关联账号",
        db_index=True,
    )

    session = models.ForeignKey(
        to='core.KuaishouSession',
        on_delete=models.SET_NULL,
        db_constraint=False,
        null=True, blank=True,
        related_name='events',
        help_text="关联会话",
    )

    event_type = models.CharField(
        max_length=32,
        choices=EVENT_TYPE_CHOICES,
        db_index=True,
        help_text="事件类型",
    )

    level = models.CharField(
        max_length=16,
        choices=LEVEL_CHOICES,
        default='info',
        db_index=True,
        help_text="级别",
    )

    title = models.CharField(max_length=128, help_text="事件标题")
    detail = models.TextField(blank=True, default='', help_text="详细描述")
    payload = models.JSONField(default=dict, blank=True, help_text="附加结构化数据")

    worker_id = models.CharField(max_length=64, null=True, blank=True, help_text="来源 worker")
    occurred_at = models.DateTimeField(help_text="事件发生时间", db_index=True)
    is_read = models.BooleanField(default=False, help_text="是否已读（告警类）")

    class Meta:
        db_table = 'core_kuaishou_event'
        verbose_name = '快手运行时事件'
        verbose_name_plural = verbose_name
        ordering = ('-occurred_at',)
        indexes = [
            models.Index(fields=['account', 'event_type', 'occurred_at']),
            models.Index(fields=['level', 'is_read', 'occurred_at']),
        ]

    def __str__(self):
        return f"[{self.level}] {self.title}"
