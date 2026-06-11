#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: core/social/template_category_model.py
@Desc: Reply Template Category - 跨平台回复模板分类

业务分类（如 "欢迎语 / 课程介绍 / 活动推广 / 兜底回复"）。
分类本身平台无关，可被任意平台的模板与快捷回复引用。
"""
from django.db import models

from common.fu_model import RootModel


class ReplyTemplateCategory(RootModel):
    """跨平台回复模板分类（平台无关，可两级）"""

    name = models.CharField(max_length=64, help_text="分类名称", db_index=True)
    icon = models.CharField(max_length=64, blank=True, default='', help_text="图标名（前端 Element Plus Icon）")
    color = models.CharField(max_length=16, default='#909399', help_text="标签颜色")
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_constraint=False,
        related_name='children',
        help_text="父分类（支持两级）",
    )
    remark = models.CharField(max_length=255, null=True, blank=True, help_text="备注")

    class Meta:
        db_table = 'core_social_template_category'
        verbose_name = '回复模板分类'
        verbose_name_plural = verbose_name
        ordering = ('-sort', '-sys_create_datetime')

    def __str__(self):
        return self.name
