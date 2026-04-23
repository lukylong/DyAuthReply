#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: dept_model.py
@Desc: Dept Model - 部门模型 - 用于管理组织架构中的部门信息
"""
"""
Dept Model - 部门模型
用于管理组织架构中的部门信息
"""
from django.core.validators import EmailValidator, RegexValidator
from django.db import models

from common.fu_model import RootModel


class Dept(RootModel):
    """
    部门模型 - 用于组织架构管理
    
    改进点：
    1. 添加部门类型字段
    2. 添加部门描述字段
    3. 添加字段验证
    4. 优化索引
    5. 添加部门路径字段（便于查询）
    6. 添加排序权重字段
    """

    # 部门类型选择
    DEPT_TYPE_CHOICES = [
        ('company', '公司'),
        ('department', '部门'),
        ('team', '小组'),
        ('other', '其他'),
    ]

    # 部门名称
    name = models.CharField(
        max_length=64,
        help_text="部门名称",
        db_index=True,
    )

    # 部门类型
    dept_type = models.CharField(
        max_length=20,
        choices=DEPT_TYPE_CHOICES,
        default='department',
        help_text="部门类型",
        db_index=True,
    )

    # 部门电话
    phone = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        help_text="部门联系电话",
        validators=[
            RegexValidator(
                regex=r'^[\d\-\+\(\)\s]+$',
                message='电话号码格式不正确',
            )
        ]
    )

    # 部门邮箱
    email = models.EmailField(
        max_length=64,
        null=True,
        blank=True,
        help_text="部门邮箱",
        validators=[EmailValidator(message="请输入有效的邮箱地址")],
    )

    # 部门状态
    status = models.BooleanField(
        default=True,
        help_text="部门状态（启用/禁用）",
        db_index=True,
    )

    # 部门描述
    description = models.TextField(
        blank=True,
        null=True,
        help_text="部门描述",
    )

    # 部门编码
    code = models.CharField(
        max_length=32,
        unique=True,
        null=True,
        blank=True,
        help_text="部门编码",
        db_index=True,
    )

    # 父部门
    parent = models.ForeignKey(
        to='self',
        on_delete=models.CASCADE,
        db_constraint=False,
        null=True,
        blank=True,
        help_text="父部门",
        related_name="children",
    )

    # 部门领导（用户）
    lead = models.ForeignKey(
        to='core.User',
        on_delete=models.SET_NULL,
        db_constraint=False,
        null=True,
        blank=True,
        help_text="部门领导",
        related_name='leading_depts',
    )

    # 部门层级（自动计算）
    level = models.IntegerField(
        default=0,
        help_text="部门层级（0为顶层）",
        db_index=True,
    )

    # 部门路径（便于查询，格式：/1/2/3/）
    path = models.CharField(
        max_length=500,
        null=True,
        blank=True,
        help_text="部门路径",
        db_index=True,
    )

    class Meta:
        db_table = "core_dept"
        ordering = ("sort", "sys_create_datetime")
        verbose_name = "部门"
        verbose_name_plural = verbose_name
        indexes = [
            models.Index(fields=['parent', 'status']),
            models.Index(fields=['level', 'status']),
            models.Index(fields=['code']),
        ]

    def __str__(self):
        return f"{self.name} ({self.code or 'N/A'})"

    def save(self, *args, **kwargs):
        """保存时自动计算层级和路径"""
        if self.parent:
            self.level = self.parent.level + 1
            self.path = f"{self.parent.path}{self.parent.id}/"
        else:
            self.level = 0
            self.path = "/"
        super().save(*args, **kwargs)

    def get_dept_type_display_name(self):
        """获取部门类型的显示名称"""
        type_map = dict(self.DEPT_TYPE_CHOICES)
        return type_map.get(self.dept_type, 'UNKNOWN')

    def get_full_name(self):
        """获取部门全名（包含父部门）"""
        if self.parent:
            return f"{self.parent.get_full_name()} / {self.name}"
        return self.name

    def get_ancestors(self):
        """获取所有祖先部门"""
        ancestors = []
        current = self.parent
        while current:
            ancestors.append(current)
            current = current.parent
        return ancestors

    def get_descendants(self):
        """获取所有后代部门"""
        descendants = []

        def collect_children(dept):
            children = dept.children.all()
            for child in children:
                descendants.append(child)
                collect_children(child)

        collect_children(self)
        return descendants

    def get_child_count(self):
        """获取直接子部门数量"""
        return self.children.count()

    def get_user_count(self):
        """获取部门下的用户数量"""
        return self.core_users.count()

    def is_leaf(self):
        """判断是否为叶子节点（没有子部门）"""
        return self.get_child_count() == 0

    def is_root(self):
        """判断是否为根节点（没有父部门）"""
        return self.parent is None

    def can_delete(self):
        """判断是否可以删除（没有子部门和用户）"""
        return self.is_leaf() and self.get_user_count() == 0
