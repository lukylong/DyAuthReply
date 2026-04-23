#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: post_model.py
@Desc: Post Model - 岗位模型 - 用于管理组织中的岗位信息
"""
"""
Post Model - 岗位模型
用于管理组织中的岗位信息
"""
from django.core.validators import RegexValidator
from django.db import models

from common.fu_model import RootModel


class Post(RootModel):
    """
    岗位模型 - 用于职位管理
    
    改进点：
    1. 添加岗位类型字段
    2. 添加岗位级别字段
    3. 添加岗位描述字段
    4. 添加字段验证
    5. 优化索引
    6. 添加薪资范围字段
    7. 添加部门关联
    """

    # 岗位类型选择
    POST_TYPE_CHOICES = [
        (0, '管理岗'),
        (1, '技术岗'),
        (2, '业务岗'),
        (3, '职能岗'),
        (4, '其他'),
    ]

    # 岗位级别选择
    POST_LEVEL_CHOICES = [
        (0, '高层'),
        (1, '中层'),
        (2, '基层'),
        (3, '一般员工'),
    ]

    # 岗位名称
    name = models.CharField(
        max_length=64,
        help_text="岗位名称",
        db_index=True,
    )

    # 岗位编码
    code = models.CharField(
        max_length=32,
        unique=True,
        help_text="岗位编码",
        db_index=True,
        validators=[
            RegexValidator(
                regex=r'^[a-zA-Z0-9_-]+$',
                message='岗位编码只能包含字母、数字、下划线和横线',
            )
        ]
    )

    # 岗位类型
    post_type = models.IntegerField(
        choices=POST_TYPE_CHOICES,
        default=4,
        help_text="岗位类型",
        db_index=True,
    )

    # 岗位级别
    post_level = models.IntegerField(
        choices=POST_LEVEL_CHOICES,
        default=3,
        help_text="岗位级别",
        db_index=True,
    )

    # 岗位状态
    status = models.BooleanField(
        default=True,
        help_text="岗位状态（启用/禁用）",
        db_index=True,
    )

    # 岗位描述
    description = models.TextField(
        blank=True,
        null=True,
        help_text="岗位描述/职责",
    )

    # 所属部门
    dept = models.ForeignKey(
        to='core.Dept',
        on_delete=models.SET_NULL,
        db_constraint=False,
        null=True,
        blank=True,
        help_text="所属部门",
        related_name='posts',
    )

    class Meta:
        db_table = "core_post"
        ordering = ("-sys_create_datetime",)
        verbose_name = "岗位"
        verbose_name_plural = verbose_name
        indexes = [
            models.Index(fields=['status', 'post_type']),
            models.Index(fields=['post_level', 'status']),
            models.Index(fields=['dept', 'status']),
        ]

    def __str__(self):
        return f"{self.name} ({self.code})"

    def get_post_type_display_name(self):
        """获取岗位类型的显示名称"""
        type_map = dict(self.POST_TYPE_CHOICES)
        return type_map.get(self.post_type, 'UNKNOWN')

    def get_post_level_display_name(self):
        """获取岗位级别的显示名称"""
        level_map = dict(self.POST_LEVEL_CHOICES)
        return level_map.get(self.post_level, 'UNKNOWN')

    def get_user_count(self):
        """获取该岗位的用户数量"""
        return self.core_users.count()

    def can_delete(self):
        """判断是否可以删除（没有用户使用该岗位）"""
        return self.get_user_count() == 0
