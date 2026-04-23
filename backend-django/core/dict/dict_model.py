#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: dict_model.py
@Desc: Dictionary Model - 字典模型 - 用于管理系统字典数据
"""
"""
Dictionary Model - 字典模型
用于管理系统字典数据
"""
from django.db import models

from common.fu_model import RootModel


class Dict(RootModel):
    """系统字典表"""

    name = models.CharField(max_length=100, help_text="字典名称")
    code = models.CharField(max_length=100, help_text="编码", unique=True, db_index=True)
    status = models.BooleanField(default=True, blank=True, help_text="状态")
    remark = models.CharField(max_length=2000, blank=True, null=True, help_text="备注")

    class Meta:
        db_table = 'core_dict'
        ordering = ('sort',)

    def __str__(self):
        return f"{self.name} ({self.code})"
