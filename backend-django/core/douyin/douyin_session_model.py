#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: douyin_session_model.py
@Desc: Douyin Session - 抖音并发会话运行时模型（纯 HTTP 协议，无浏览器）
        每个被 worker 托管并运行中的账号一条记录；worker 定期心跳上报。
        支撑"多账号并发托管"能力，后台可见每路会话的实时状态。
        多 worker 分片部署时，account 与 worker 一一对应，账号迁移到新 worker 后
        由新 worker 的心跳覆盖 worker_id（OneToOne，键为 account）。
"""
from django.db import models

from common.fu_model import RootModel


class DouyinSession(RootModel):
    """抖音并发会话（纯 HTTP 协议托管）

    一个 worker 进程可以托管多个账号（每账号一路协程 + 独立签名/httpx），
    每个账号对应一条 DouyinSession 记录。
    """

    SESSION_STATUS = [
        ('idle', '空闲'),
        ('running', '运行中'),
        ('paused', '已暂停'),
        ('error', '异常'),
        ('stopped', '已停止'),
    ]

    account = models.OneToOneField(
        to='core.DouyinAccount',
        on_delete=models.CASCADE,
        db_constraint=False,
        related_name='session',
        help_text="绑定的抖音账号",
        db_index=True,
    )

    worker_id = models.CharField(
        max_length=64,
        db_index=True,
        help_text="所属 worker 进程标识（hostname:pid 或 k8s pod 名）",
    )

    context_id = models.CharField(
        max_length=64,
        help_text="会话运行时标识（纯 HTTP 协议下为 ctx_<account 前缀>，保留字段兼容历史）",
    )

    status = models.CharField(
        max_length=16,
        choices=SESSION_STATUS,
        default='idle',
        db_index=True,
        help_text="会话状态",
    )

    started_at = models.DateTimeField(null=True, blank=True, help_text="启动时间")
    last_heartbeat = models.DateTimeField(null=True, blank=True, help_text="最近心跳", db_index=True)
    last_message_at = models.DateTimeField(null=True, blank=True, help_text="最近处理消息时间")

    messages_today = models.IntegerField(default=0, help_text="今日处理消息数")
    replies_today = models.IntegerField(default=0, help_text="今日自动回复数")
    errors_today = models.IntegerField(default=0, help_text="今日错误数")

    cpu_percent = models.FloatField(default=0, help_text="CPU 占用 %")
    memory_mb = models.FloatField(default=0, help_text="内存占用 MB")

    proxy_url = models.CharField(
        max_length=255, null=True, blank=True,
        help_text="会话实际使用的代理地址（snapshot）",
    )

    error_message = models.TextField(null=True, blank=True, help_text="最近一次错误描述")

    class Meta:
        db_table = 'core_douyin_session'
        verbose_name = '抖音并发会话'
        verbose_name_plural = verbose_name
        ordering = ('-last_heartbeat',)
        indexes = [
            models.Index(fields=['worker_id', 'status']),
            models.Index(fields=['status', 'last_heartbeat']),
        ]

    def __str__(self):
        return f"Session[{self.account_id}] {self.status}"

    def is_alive(self, timeout_seconds: int = 60) -> bool:
        """判断会话是否在 timeout 秒内有心跳"""
        from django.utils import timezone
        from datetime import timedelta
        if not self.last_heartbeat:
            return False
        return (timezone.now() - self.last_heartbeat) < timedelta(seconds=timeout_seconds)
