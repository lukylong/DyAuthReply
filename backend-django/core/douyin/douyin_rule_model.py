#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: douyin_rule_model.py
@Desc: Douyin Rule Model - 抖音回复规则模型
        关键词/正则/兜底三类匹配方式，命中后发送文本与多条链接。
"""
from django.db import models

from common.fu_model import RootModel


class DouyinRule(RootModel):
    """
    抖音自动回复规则

    一条规则包含匹配方式、关键词/正则模式、回复文本、链接列表与发送模式。
    规则按 priority 从大到小命中，仅首个命中规则生效。
    """

    MATCH_TYPE_CHOICES = [
        ('contains', '包含关键词（任一命中）'),
        ('regex', '正则匹配'),
        ('default', '兜底（无命中时）'),
    ]

    SEND_MODE_CHOICES = [
        ('multi_message', '文本 + 每条链接单独一条消息'),
        ('merged', '文本与链接合并为一条消息'),
        ('card_fallback', '尝试卡片降级到纯文本'),
    ]

    CHANNEL_CHOICES = [
        ('dm', '私信'),
        ('comment', '评论'),
        ('all', '全部渠道'),
    ]

    account = models.ForeignKey(
        to='core.DouyinAccount',
        on_delete=models.CASCADE,
        db_constraint=False,
        null=True,
        blank=True,
        related_name='rules',
        help_text="[已弃用] 旧单账号绑定字段；保留兼容，实际绑定见 account_ids",
        db_index=True,
    )

    account_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="显式绑定的账号 ID 列表；为空表示全局规则，对所有未绑定账号生效。一个账号只能属于一条规则",
    )

    name = models.CharField(
        max_length=64,
        help_text="规则名称",
    )

    match_type = models.CharField(
        max_length=16,
        choices=MATCH_TYPE_CHOICES,
        default='contains',
        help_text="匹配方式",
        db_index=True,
    )

    keywords = models.JSONField(
        default=list,
        blank=True,
        help_text="关键词列表，match_type=contains 时使用，任意命中即可",
    )

    regex_pattern = models.CharField(
        max_length=512,
        null=True,
        blank=True,
        help_text="正则表达式，match_type=regex 时使用",
    )

    reply_text = models.TextField(
        blank=True,
        default='',
        help_text="回复文本（可含换行）",
    )

    links = models.JSONField(
        default=list,
        blank=True,
        help_text="链接列表，发送时根据 send_mode 处理",
    )

    card_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="关联的伪装卡片 ID 列表；命中后按顺序各发一条卡片落地页 URL",
    )

    send_mode = models.CharField(
        max_length=16,
        choices=SEND_MODE_CHOICES,
        default='multi_message',
        help_text="发送模式",
    )

    priority = models.IntegerField(
        default=0,
        help_text="优先级（越大越先命中）",
        db_index=True,
    )

    status = models.BooleanField(
        default=True,
        help_text="是否启用",
        db_index=True,
    )

    cooldown_seconds = models.IntegerField(
        default=3600,
        help_text="同一会话命中本规则的冷却时间（秒），防止重复骚扰",
    )

    hit_count = models.IntegerField(
        default=0,
        help_text="累计命中次数",
    )

    remark = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="备注",
    )

    # ---------- 增强字段 ----------
    template = models.ForeignKey(
        to='core.DouyinTemplate',
        on_delete=models.SET_NULL,
        db_constraint=False,
        null=True, blank=True,
        related_name='rules',
        help_text="引用的回复模板（优先级高于 reply_text/links）",
        db_index=True,
    )

    time_window_start = models.TimeField(
        null=True, blank=True,
        help_text="规则生效时段起点（为空则全天）",
    )
    time_window_end = models.TimeField(
        null=True, blank=True,
        help_text="规则生效时段终点",
    )

    weekday_mask = models.CharField(
        max_length=8,
        default='1111111',
        help_text="周一到周日是否生效（7 位 0/1 字符串，默认 1111111 全周）",
    )

    channel = models.CharField(
        max_length=8,
        choices=CHANNEL_CHOICES,
        default='dm',
        help_text="适用渠道",
        db_index=True,
    )

    class Meta:
        db_table = 'core_douyin_rule'
        verbose_name = '抖音回复规则'
        verbose_name_plural = verbose_name
        ordering = ('-priority', '-sys_create_datetime')
        indexes = [
            models.Index(fields=['account', 'status', 'priority']),
            models.Index(fields=['match_type', 'status']),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_match_type_display()})"

    def is_default(self) -> bool:
        """是否兜底规则"""
        return self.match_type == 'default'
