#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: permission_model.py
@Desc: Permission Model - 权限模型 - 用于管理系统中的操作权限（如按钮权限、接口权限等）
"""
"""
Permission Model - 权限模型
用于管理系统中的操作权限（如按钮权限、接口权限等）
"""
from django.core.validators import RegexValidator
from django.db import models

from common.fu_model import RootModel


class Permission(RootModel):
    """
    权限模型 - 用于细粒度的权限控制
    
    改进点：
    1. 更好的字段命名和注释
    2. 添加权限类型字段，支持多种权限类型
    3. 添加描述字段，便于权限管理
    4. 添加字段验证
    5. 添加索引优化查询性能
    """

    # 权限类型选择
    PERMISSION_TYPE_CHOICES = [
        (0, '按钮权限'),
        (1, 'API权限'),
        (2, '数据权限'),
        (3, '其他权限'),
    ]

    # HTTP 方法选择
    HTTP_METHOD_CHOICES = [
        (0, 'GET'),
        (1, 'POST'),
        (2, 'PUT'),
        (3, 'DELETE'),
        (4, 'PATCH'),
        (5, 'ALL'),
    ]

    # 关联的菜单
    menu = models.ForeignKey(
        to="core.Menu",
        db_constraint=False,
        on_delete=models.CASCADE,
        help_text="关联菜单",
        db_index=True,  # 添加索引
    )

    # 权限名称
    name = models.CharField(
        max_length=64,
        help_text="权限名称",
        db_index=True,
    )

    # 权限编码（唯一标识）
    code = models.CharField(
        max_length=64,
        help_text="权限编码",
        db_index=True,
        validators=[
            RegexValidator(
                regex=r'^[a-zA-Z0-9_:]+$',
                message='权限编码只能包含字母、数字、下划线和冒号',
            )
        ]
    )

    # 权限类型
    permission_type = models.IntegerField(
        choices=PERMISSION_TYPE_CHOICES,
        default=0,
        help_text="权限类型",
        db_index=True,
    )

    # API 路径
    api_path = models.CharField(
        max_length=200,
        help_text="API路径",
        blank=True,
        null=True,
    )

    # HTTP 方法
    http_method = models.IntegerField(
        choices=HTTP_METHOD_CHOICES,
        default=0,
        help_text="HTTP方法类型",
    )

    # 权限描述
    description = models.TextField(
        blank=True,
        null=True,
        help_text="权限描述",
    )

    # 是否启用
    is_active = models.BooleanField(
        default=True,
        help_text="是否启用",
        db_index=True,
    )

    class Meta:
        db_table = "core_permission"
        ordering = ("sort", "-sys_create_datetime")
        verbose_name = "权限"
        verbose_name_plural = verbose_name
        # 联合唯一约束：同一个菜单下的权限编码必须唯一
        unique_together = [['menu', 'code']]
        # 添加索引
        indexes = [
            models.Index(fields=['menu', 'is_active']),
            models.Index(fields=['permission_type', 'is_active']),
        ]

    def __str__(self):
        return f"{self.name} ({self.code})"

    def get_http_method_display_name(self):
        """获取 HTTP 方法的显示名称"""
        method_map = dict(self.HTTP_METHOD_CHOICES)
        return method_map.get(self.http_method, 'UNKNOWN')

    def get_permission_type_display_name(self):
        """获取权限类型的显示名称"""
        type_map = dict(self.PERMISSION_TYPE_CHOICES)
        return type_map.get(self.permission_type, 'UNKNOWN')
