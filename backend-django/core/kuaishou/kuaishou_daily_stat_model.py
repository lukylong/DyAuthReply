#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: kuaishou_daily_stat_model.py
@Desc: Kuaishou Daily Stat - 快手每日统计聚合（按账号 × 日期）
"""
from django.db import models

from common.fu_model import RootModel


class KuaishouDailyStat(RootModel):
    """快手每日统计（APScheduler 定期聚合，避免看板扫 reply_log）"""

    account = models.ForeignKey(
        to='core.KuaishouAccount',
        on_delete=models.CASCADE,
        db_constraint=False,
        related_name='daily_stats',
        help_text="账号",
        db_index=True,
    )

    stat_date = models.DateField(help_text="统计日期", db_index=True)

    messages_received = models.IntegerField(default=0, help_text="收到消息数")
    replies_sent = models.IntegerField(default=0, help_text="成功回复数")
    replies_failed = models.IntegerField(default=0, help_text="回复失败数")
    replies_skipped = models.IntegerField(default=0, help_text="跳过数（黑名单/冷却/静默）")

    unique_peers = models.IntegerField(default=0, help_text="独立对话用户数")
    new_peers = models.IntegerField(default=0, help_text="新增对话用户数")

    avg_response_ms = models.IntegerField(default=0, help_text="平均响应毫秒")
    p95_response_ms = models.IntegerField(default=0, help_text="P95 响应毫秒")

    events_warning = models.IntegerField(default=0, help_text="警告事件数")
    events_error = models.IntegerField(default=0, help_text="错误事件数")

    rule_hit_map = models.JSONField(default=dict, blank=True, help_text="规则命中分布 {rule_id: hit_count}")

    class Meta:
        db_table = 'core_kuaishou_daily_stat'
        verbose_name = '快手每日统计'
        verbose_name_plural = verbose_name
        ordering = ('-stat_date',)
        unique_together = (('account', 'stat_date'),)
        indexes = [
            models.Index(fields=['stat_date', 'account']),
        ]

    def __str__(self):
        return f"{self.account_id} @ {self.stat_date}"
