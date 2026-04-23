#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: role_model.py
@Desc: Role Model - 角色模型 - 用于管理系统角色和权限分配
"""
"""
Role Model - 角色模型
用于管理系统角色和权限分配
"""
from django.db import models

from common.fu_model import RootModel


class Role(RootModel):
    """
    角色模型 - 用于用户角色管理和权限分配
    
    改进点：
    1. 添加更详细的字段注释
    2. 添加角色类型字段，支持系统角色和自定义角色
    3. 添加角色优先级字段
    4. 添加字段验证
    5. 优化索引
    6. 添加角色描述字段
    """

    # 角色类型选择
    ROLE_TYPE_CHOICES = [
        (0, '系统角色'),  # 系统内置角色，不可删除
        (1, '自定义角色'),  # 用户创建的角色，可以删除
    ]

    # 数据范围选择
    DATA_SCOPE_CHOICES = [
        (0, '仅本人数据'),
        (1, '本部门数据'),
        (2, '本部门及下级部门数据'),
        (3, '全部数据'),
        (4, '自定义数据'),
    ]

    # 角色名称
    name = models.CharField(
        max_length=64,
        help_text="角色名称",
        db_index=True,
    )

    # 角色编码（唯一标识）
    code = models.CharField(
        max_length=64,
        unique=True,
        help_text="角色编码",
        db_index=True,
    )

    # 角色类型
    role_type = models.IntegerField(
        choices=ROLE_TYPE_CHOICES,
        default=1,
        help_text="角色类型",
        db_index=True,
    )

    # 角色状态
    status = models.BooleanField(
        default=True,
        help_text="角色状态（启用/禁用）",
        db_index=True,
    )

    # 数据权限范围
    data_scope = models.IntegerField(
        choices=DATA_SCOPE_CHOICES,
        default=0,
        help_text="数据权限范围",
    )

    # 角色优先级（数字越大优先级越高）
    priority = models.IntegerField(
        default=0,
        help_text="角色优先级",
        db_index=True,
    )

    # 角色描述
    description = models.TextField(
        blank=True,
        null=True,
        help_text="角色描述",
    )

    # 备注
    remark = models.TextField(
        blank=True,
        null=True,
        help_text="备注信息",
    )

    # 关联的部门组
    dept = models.ManyToManyField(
        to="core.Dept",
        db_constraint=False,
        blank=True,
        help_text="关联的部门组",
        related_name="core_roles",
    )

    # 关联的菜单
    menu = models.ManyToManyField(
        to="core.Menu",
        db_constraint=False,
        blank=True,
        help_text="关联的菜单",
        related_name="core_roles",
    )

    # 关联的权限（原 button）
    permission = models.ManyToManyField(
        to="core.Permission",
        db_constraint=False,
        blank=True,
        help_text="关联的权限",
        related_name="roles",
    )

    class Meta:
        db_table = "core_role"
        ordering = ("-priority", "-sys_update_datetime")
        verbose_name = "角色"
        verbose_name_plural = verbose_name
        indexes = [
            models.Index(fields=['status', 'role_type']),
            models.Index(fields=['priority', 'status']),
        ]

    def __str__(self):
        return f"{self.name} ({self.code})"

    def is_system_role(self):
        """判断是否为系统角色"""
        return self.role_type == 0

    def can_delete(self):
        """判断是否可以删除（系统角色不可删除）"""
        return self.role_type != 0

    def get_role_type_display_name(self):
        """获取角色类型的显示名称"""
        type_map = dict(self.ROLE_TYPE_CHOICES)
        return type_map.get(self.role_type, 'UNKNOWN')

    def get_data_scope_display_name(self):
        """获取数据范围的显示名称"""
        scope_map = dict(self.DATA_SCOPE_CHOICES)
        return scope_map.get(self.data_scope, 'UNKNOWN')

    def get_user_count(self):
        """获取该角色的用户数量"""
        return self.core_users.count()

    def get_menu_count(self):
        """获取该角色关联的菜单数量"""
        return self.menu.count()

    def get_permission_count(self):
        """获取该角色关联的权限数量"""
        return self.permission.count()
