#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: announcement_model.py
@Desc: 公告模型
"""
"""
公告模型
"""
from django.db import models

from common.fu_model import RootModel


class Announcement(RootModel):
    """公告"""

    STATUS_CHOICES = [
        ('draft', '草稿'),
        ('published', '已发布'),
        ('expired', '已过期'),
    ]

    PRIORITY_CHOICES = [
        (0, '普通'),
        (1, '重要'),
        (2, '紧急'),
    ]

    TARGET_TYPE_CHOICES = [
        ('all', '全员'),
        ('dept', '指定部门'),
        ('role', '指定角色'),
        ('user', '指定用户'),
    ]

    # 公告内容
    title = models.CharField(max_length=200, help_text='公告标题')
    content = models.TextField(help_text='公告内容')
    summary = models.CharField(max_length=500, blank=True, default='', help_text='摘要')

    # 状态与优先级
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='draft',
        help_text='状态',
        db_index=True,
    )
    priority = models.IntegerField(
        choices=PRIORITY_CHOICES,
        default=0,
        help_text='优先级',
        db_index=True,
    )
    is_top = models.BooleanField(default=False, help_text='是否置顶')

    # 接收范围
    target_type = models.CharField(
        max_length=20,
        choices=TARGET_TYPE_CHOICES,
        default='all',
        help_text='接收范围类型',
    )
    target_ids = models.JSONField(
        default=list,
        help_text='接收目标ID列表（部门/角色/用户ID）',
    )

    # 发布时间
    publish_time = models.DateTimeField(null=True, blank=True, help_text='发布时间')
    expire_time = models.DateTimeField(null=True, blank=True, help_text='过期时间')

    # 发布人
    publisher = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='published_announcements',
        help_text='发布人',
        db_constraint=False,
    )

    # 统计
    read_count = models.IntegerField(default=0, help_text='阅读次数')

    class Meta:
        db_table = 'core_announcement'
        verbose_name = '公告'
        verbose_name_plural = verbose_name
        ordering = ['-is_top', '-priority', '-publish_time']

    def __str__(self):
        return self.title


class AnnouncementRead(RootModel):
    """公告阅读记录"""

    announcement = models.ForeignKey(
        Announcement,
        on_delete=models.CASCADE,
        related_name='reads',
        help_text='公告',
        db_constraint=False,
    )
    user = models.ForeignKey(
        'core.User',
        on_delete=models.CASCADE,
        related_name='announcement_reads',
        help_text='用户',
        db_constraint=False,
    )
    read_at = models.DateTimeField(auto_now_add=True, help_text='阅读时间')

    class Meta:
        db_table = 'core_announcement_read'
        verbose_name = '公告阅读记录'
        verbose_name_plural = verbose_name
        unique_together = ['announcement', 'user']

    def __str__(self):
        return f'{self.user} - {self.announcement}'
