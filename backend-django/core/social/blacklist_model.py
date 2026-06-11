#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: core/social/blacklist_model.py
@Desc: Blacklist - 跨平台黑名单

命中黑名单的消息会被 worker 直接跳过，不触发回复。

平台维度：
  - platform 必填。
  - scope=account/group 时，account_id / group_id 软引用对应平台账号/分组表的 UUID。
"""
from django.db import models

from common.fu_model import RootModel
from core.social.constants import PLATFORM_CHOICES


class Blacklist(RootModel):
    """跨平台黑名单

    三种维度：
    - user: 指定用户唯一标识（sec_uid / 快手 userId 等，精确屏蔽某用户）
    - nickname_keyword: 昵称包含关键词（批量屏蔽营销号）
    - content_keyword: 消息内容包含关键词（广告/敏感词）
    """

    TYPE_CHOICES = [
        ('user', '用户（唯一标识）'),
        ('nickname_keyword', '昵称关键词'),
        ('content_keyword', '内容关键词'),
    ]

    SCOPE_CHOICES = [
        ('global', '全局生效'),
        ('account', '仅指定账号生效'),
        ('group', '仅指定分组生效'),
    ]

    platform = models.CharField(
        max_length=16,
        choices=PLATFORM_CHOICES,
        db_index=True,
        help_text="归属平台（douyin/kuaishou）",
    )

    blacklist_type = models.CharField(
        max_length=24,
        choices=TYPE_CHOICES,
        db_index=True,
        help_text="黑名单类型",
    )

    value = models.CharField(
        max_length=255,
        help_text="具体值（用户唯一标识 / 关键词）",
        db_index=True,
    )

    scope = models.CharField(
        max_length=16,
        choices=SCOPE_CHOICES,
        default='global',
        help_text="作用范围",
    )

    account_id = models.CharField(
        max_length=36,
        null=True, blank=True,
        db_index=True,
        help_text="scope=account 时绑定的账号ID（软引用）",
    )

    group_id = models.CharField(
        max_length=36,
        null=True, blank=True,
        db_index=True,
        help_text="scope=group 时绑定的分组ID（软引用）",
    )

    reason = models.CharField(max_length=255, null=True, blank=True, help_text="加入黑名单原因")

    hit_count = models.IntegerField(default=0, help_text="累计命中次数")

    status = models.BooleanField(default=True, help_text="是否启用", db_index=True)

    class Meta:
        db_table = 'core_social_blacklist'
        verbose_name = '黑名单'
        verbose_name_plural = verbose_name
        ordering = ('-sys_create_datetime',)
        indexes = [
            models.Index(fields=['platform', 'blacklist_type', 'status']),
            models.Index(fields=['platform', 'scope', 'status']),
        ]

    def __str__(self):
        return f"[{self.get_blacklist_type_display()}] {self.value}"
