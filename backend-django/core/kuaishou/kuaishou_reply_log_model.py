#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: kuaishou_reply_log_model.py
@Desc: Kuaishou Reply Log Model - 快手回复日志模型
        记录每一次自动回复的命中规则、耗时、结果与发送内容。
        matched_rule 指向跨平台共用层 core.ReplyRule。
"""
from django.db import models

from common.fu_model import RootModel


class KuaishouReplyLog(RootModel):
    """快手自动回复日志（失败/成功都保留便于回溯与风控调整）"""

    RESULT_CHOICES = [
        ('success', '成功'),
        ('failed', '失败'),
        ('skipped', '已跳过'),
        ('cooldown', '冷却中'),
        ('quota_exceeded', '超出配额'),
        ('silent', '静默时段'),
    ]

    account = models.ForeignKey(
        to='core.KuaishouAccount',
        on_delete=models.CASCADE,
        db_constraint=False,
        related_name='reply_logs',
        help_text="账号",
        db_index=True,
    )

    conversation = models.ForeignKey(
        to='core.KuaishouConversation',
        on_delete=models.SET_NULL,
        db_constraint=False,
        null=True, blank=True,
        related_name='reply_logs',
        help_text="会话",
    )

    trigger_message = models.ForeignKey(
        to='core.KuaishouMessage',
        on_delete=models.SET_NULL,
        db_constraint=False,
        null=True, blank=True,
        related_name='reply_logs',
        help_text="触发本次回复的入向消息",
    )

    matched_rule = models.ForeignKey(
        to='core.ReplyRule',
        on_delete=models.SET_NULL,
        db_constraint=False,
        null=True, blank=True,
        related_name='kuaishou_reply_logs',
        help_text="命中的规则（跨平台共用层）",
    )

    reply_text = models.TextField(blank=True, default='', help_text="实际发送的文本")
    reply_links = models.JSONField(default=list, blank=True, help_text="实际发送的链接列表")

    result = models.CharField(
        max_length=20,
        choices=RESULT_CHOICES,
        default='success',
        help_text="本次回复结果",
        db_index=True,
    )

    error_message = models.CharField(max_length=512, null=True, blank=True, help_text="失败/跳过原因")
    duration_ms = models.IntegerField(default=0, help_text="本次回复耗时（毫秒）")
    sent_at = models.DateTimeField(null=True, blank=True, help_text="发送完成时间")

    class Meta:
        db_table = 'core_kuaishou_reply_log'
        verbose_name = '快手回复日志'
        verbose_name_plural = verbose_name
        ordering = ('-sys_create_datetime',)
        indexes = [
            models.Index(fields=['account', 'result', 'sys_create_datetime']),
            models.Index(fields=['conversation', 'sys_create_datetime']),
            models.Index(fields=['matched_rule', 'result']),
        ]

    def __str__(self):
        return f"{self.account_id} - {self.result} - {self.sys_create_datetime}"
