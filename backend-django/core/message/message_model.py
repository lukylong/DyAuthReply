#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: message_model.py
@Desc: 消息模型
"""
"""
消息模型
"""
from django.db import models

from common.fu_model import RootModel


class Message(RootModel):
    """站内消息"""

    TYPE_CHOICES = [
        ('system', '系统通知'),
        ('workflow', '工作流通知'),
        ('todo', '待办提醒'),
        ('announcement', '公告'),
    ]

    STATUS_CHOICES = [
        ('unread', '未读'),
        ('read', '已读'),
    ]

    # 接收人
    recipient = models.ForeignKey(
        'core.User',
        on_delete=models.CASCADE,
        related_name='messages',
        help_text='接收人',
        db_constraint=False,
    )

    # 消息内容
    title = models.CharField(max_length=200, help_text='消息标题')
    content = models.TextField(help_text='消息内容')
    msg_type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default='system',
        help_text='消息类型',
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='unread',
        help_text='消息状态',
        db_index=True,
    )

    # 关联信息（可选，用于点击跳转）
    link_type = models.CharField(
        max_length=50,
        blank=True,
        default='',
        help_text='关联类型（如 workflow_task, workflow_instance）',
    )
    link_id = models.CharField(
        max_length=50,
        blank=True,
        default='',
        help_text='关联对象ID',
    )

    # 阅读时间
    read_at = models.DateTimeField(null=True, blank=True, help_text='阅读时间')

    # 发送人（可选，系统消息可为空）
    sender = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sent_messages',
        help_text='发送人',
        db_constraint=False,
    )

    class Meta:
        db_table = 'core_message'
        verbose_name = '站内消息'
        verbose_name_plural = verbose_name
        ordering = ['-sys_create_datetime']
        indexes = [
            models.Index(fields=['recipient', 'status']),
            models.Index(fields=['recipient', 'msg_type']),
        ]

    def __str__(self):
        return f'{self.title} -> {self.recipient}'

    def mark_as_read(self):
        """标记为已读"""
        from datetime import datetime
        if self.status == 'unread':
            self.status = 'read'
            self.read_at = datetime.now()
            self.save(update_fields=['status', 'read_at'])
