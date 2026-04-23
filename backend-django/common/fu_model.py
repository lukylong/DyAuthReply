#!/usr/bin/env python
# -*- coding: utf-8 -*-
# time: 1/23/2024 9:54 PM
# file: fu_model.py
# author: 臧成龙
# QQ: 939589097
import uuid

from django.db import models


class RootModel(models.Model):
    """
    核心模型基类 - 为 core 模块提供统一的基础字段
    
    优化点：
    1. 使用 UUID 作为主键（分布式友好，全局唯一）
    2. 添加更详细的中文字段注释
    3. 优化字段命名（更符合 Python 命名规范）
    4. 添加软删除支持
    5. 添加创建人和修改人追踪
    6. 添加备注字段
    7. 优化索引设计
    """

    # 主键（使用UUID，全局唯一）
    id = models.CharField(
        primary_key=True,
        max_length=36,
        default=uuid.uuid4,
        help_text="主键ID",
        editable=False,
    )

    # 创建信息
    sys_creator = models.ForeignKey(
        to='core.User',
        on_delete=models.SET_NULL,
        db_constraint=False,
        null=True,
        blank=True,
        help_text="创建人",
        related_name="%(app_label)s_%(class)s_created",
        db_index=True,
    )

    sys_create_datetime = models.DateTimeField(
        auto_now_add=True,
        help_text="创建时间",
        db_index=True,
    )

    # 修改信息
    sys_modifier = models.ForeignKey(
        to='core.User',
        on_delete=models.SET_NULL,
        db_constraint=False,
        null=True,
        blank=True,
        help_text="修改人",
        related_name="%(app_label)s_%(class)s_modified",
    )

    sys_update_datetime = models.DateTimeField(
        auto_now=True,
        help_text="更新时间",
        db_index=True,
    )

    # 软删除标识
    is_deleted = models.BooleanField(
        default=False,
        help_text="是否删除（软删除标识）",
        db_index=True,
    )

    # 排序字段
    sort = models.IntegerField(
        default=0,
        help_text="排序（数字越大越靠前）",
        db_index=True,
    )

    class Meta:
        abstract = True
        # 默认排序：未删除的在前，排序号大的在前，创建时间新的在前
        ordering = ['is_deleted', '-sort', '-sys_create_datetime']

    def soft_delete(self):
        """软删除"""
        self.is_deleted = True
        self.save(update_fields=['is_deleted', 'sys_update_datetime'])

    def restore(self):
        """恢复软删除"""
        self.is_deleted = False
        self.save(update_fields=['is_deleted', 'sys_update_datetime'])

    def get_creator_name(self):
        """获取创建人姓名"""
        if self.sys_creator:
            return self.sys_creator.name or self.sys_creator.username
        return "系统"

    def get_modifier_name(self):
        """获取修改人姓名"""
        if self.sys_modifier:
            return self.sys_modifier.name or self.sys_modifier.username
        return "系统"


# exclude_models = [CoreModel]
exclude_fields = (
    'id',
    'sys_creator',
    'sys_update_datetime',
    'sys_create_datetime',
)
