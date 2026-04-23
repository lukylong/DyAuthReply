#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: douyin_quick_reply_model.py
@Desc: Douyin Quick Reply - 抖音快捷回复
        人工客服介入时可一键发送的短语片段，与自动回复规则解耦。
"""
from django.db import models

from common.fu_model import RootModel


class DouyinQuickReply(RootModel):
    """抖音快捷回复（人工介入用）"""

    shortcut = models.CharField(
        max_length=32,
        help_text="快捷键/速记（如 /hi、/price）",
        db_index=True,
    )

    title = models.CharField(max_length=64, help_text="片段标题")

    content = models.TextField(help_text="回复内容")

    category = models.ForeignKey(
        to='core.DouyinTemplateCategory',
        on_delete=models.SET_NULL,
        db_constraint=False,
        null=True, blank=True,
        related_name='quick_replies',
        help_text="所属分类（复用模板分类）",
    )

    is_shared = models.BooleanField(
        default=True,
        help_text="是否团队共享",
    )

    owner = models.ForeignKey(
        to='core.User',
        on_delete=models.SET_NULL,
        db_constraint=False,
        null=True, blank=True,
        related_name='douyin_quick_replies',
        help_text="创建者",
    )

    use_count = models.IntegerField(default=0, help_text="使用次数")

    status = models.BooleanField(default=True, help_text="是否启用", db_index=True)

    class Meta:
        db_table = 'core_douyin_quick_reply'
        verbose_name = '抖音快捷回复'
        verbose_name_plural = verbose_name
        ordering = ('-sort', '-sys_create_datetime')

    def __str__(self):
        return f"{self.shortcut} {self.title}"
