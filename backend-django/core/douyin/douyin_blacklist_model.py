#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: douyin_blacklist_model.py
@Desc: Douyin Blacklist - 抖音黑名单
        命中黑名单的消息会被 worker 直接跳过，不触发回复。
"""
from django.db import models

from common.fu_model import RootModel


class DouyinBlacklist(RootModel):
    """抖音黑名单

    三种维度：
    - user: 指定 sec_uid（精确屏蔽某用户）
    - nickname_keyword: 昵称包含关键词（批量屏蔽营销号）
    - content_keyword: 消息内容包含关键词（广告/敏感词）
    """

    TYPE_CHOICES = [
        ('user', '用户（sec_uid）'),
        ('nickname_keyword', '昵称关键词'),
        ('content_keyword', '内容关键词'),
    ]

    SCOPE_CHOICES = [
        ('global', '全局生效'),
        ('account', '仅指定账号生效'),
        ('group', '仅指定分组生效'),
    ]

    blacklist_type = models.CharField(
        max_length=24,
        choices=TYPE_CHOICES,
        db_index=True,
        help_text="黑名单类型",
    )

    value = models.CharField(
        max_length=255,
        help_text="具体值（sec_uid / 关键词）",
        db_index=True,
    )

    scope = models.CharField(
        max_length=16,
        choices=SCOPE_CHOICES,
        default='global',
        help_text="作用范围",
    )

    account = models.ForeignKey(
        to='core.DouyinAccount',
        on_delete=models.CASCADE,
        db_constraint=False,
        null=True, blank=True,
        related_name='blacklist_entries',
        help_text="scope=account 时绑定的账号",
    )

    group = models.ForeignKey(
        to='core.DouyinAccountGroup',
        on_delete=models.CASCADE,
        db_constraint=False,
        null=True, blank=True,
        related_name='blacklist_entries',
        help_text="scope=group 时绑定的分组",
    )

    reason = models.CharField(max_length=255, null=True, blank=True, help_text="加入黑名单原因")

    hit_count = models.IntegerField(default=0, help_text="累计命中次数")

    status = models.BooleanField(default=True, help_text="是否启用", db_index=True)

    class Meta:
        db_table = 'core_douyin_blacklist'
        verbose_name = '抖音黑名单'
        verbose_name_plural = verbose_name
        ordering = ('-sys_create_datetime',)
        indexes = [
            models.Index(fields=['blacklist_type', 'status']),
            models.Index(fields=['scope', 'status']),
        ]

    def __str__(self):
        return f"[{self.get_blacklist_type_display()}] {self.value}"
