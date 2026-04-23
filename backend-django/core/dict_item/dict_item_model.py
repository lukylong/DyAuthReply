#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: dict_item_model.py
@Desc: Dictionary Item Model - 字典项模型 - 用于管理字典中的各个选项
"""
"""
Dictionary Item Model - 字典项模型
用于管理字典中的各个选项
"""
from django.db import models

from common.fu_model import RootModel


class DictItem(RootModel):
    """系统字典项表"""

    icon = models.CharField(max_length=100, blank=True, null=True, help_text="ICON")
    label = models.CharField(
        max_length=100, blank=True, null=True, help_text="显示名称"
    )
    value = models.CharField(max_length=100, blank=True, null=True, help_text="实际值")
    status = models.BooleanField(default=True, blank=True, help_text="状态")
    dict = models.ForeignKey(
        to="Dict", db_constraint=False, on_delete=models.CASCADE, help_text="字典"
    )
    remark = models.CharField(max_length=2000, blank=True, null=True, help_text="备注")

    class Meta:
        db_table = "core_dict_item"
        ordering = ("-sys_create_datetime",)

    def __str__(self):
        return f"{self.label} ({self.value})"
