#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: core/social/quick_reply_model.py
@Desc: Quick Reply - 跨平台快捷回复

人工客服介入时可一键发送的短语片段，与自动回复规则解耦。
platform 为空表示通用，所有平台均可用。
"""
from django.db import models

from common.fu_model import RootModel
from core.social.constants import PLATFORM_CHOICES


class QuickReply(RootModel):
    """跨平台快捷回复（人工介入用）"""

    platform = models.CharField(
        max_length=16,
        choices=PLATFORM_CHOICES,
        null=True,
        blank=True,
        db_index=True,
        help_text="归属平台；为空表示通用（所有平台可用）",
    )

    shortcut = models.CharField(
        max_length=32,
        help_text="快捷键/速记（如 /hi、/price）",
        db_index=True,
    )

    title = models.CharField(max_length=64, help_text="片段标题")

    content = models.TextField(help_text="回复内容")

    category = models.ForeignKey(
        to='core.ReplyTemplateCategory',
        on_delete=models.SET_NULL,
        db_constraint=False,
        null=True, blank=True,
        related_name='quick_replies',
        help_text="所属分类（复用模板分类）",
    )

    is_shared = models.BooleanField(default=True, help_text="是否团队共享")

    owner = models.ForeignKey(
        to='core.User',
        on_delete=models.SET_NULL,
        db_constraint=False,
        null=True, blank=True,
        related_name='social_quick_replies',
        help_text="创建者",
    )

    use_count = models.IntegerField(default=0, help_text="使用次数")

    status = models.BooleanField(default=True, help_text="是否启用", db_index=True)

    class Meta:
        db_table = 'core_social_quick_reply'
        verbose_name = '快捷回复'
        verbose_name_plural = verbose_name
        ordering = ('-sort', '-sys_create_datetime')
        indexes = [
            models.Index(fields=['platform', 'status']),
        ]

    def __str__(self):
        return f"{self.shortcut} {self.title}"
