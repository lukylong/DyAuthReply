#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: kuaishou_message_model.py
@Desc: Kuaishou Message Model - 快手消息流水模型
        按 (conversation, external_msg_id) 唯一，用于去重防止重复回复。
"""
from django.db import models

from common.fu_model import RootModel


class KuaishouMessage(RootModel):
    """快手私信消息流水（direction=in 的未处理消息触发回复引擎）"""

    DIRECTION_CHOICES = [
        ('in', '对方发来'),
        ('out', '本方发出'),
    ]

    CONTENT_TYPE_CHOICES = [
        ('text', '文本'),
        ('image', '图片'),
        ('video', '视频'),
        ('card', '卡片'),
        ('other', '其他'),
    ]

    conversation = models.ForeignKey(
        to='core.KuaishouConversation',
        on_delete=models.CASCADE,
        db_constraint=False,
        related_name='messages',
        help_text="所属会话",
        db_index=True,
    )

    external_msg_id = models.CharField(
        max_length=128,
        help_text="快手端消息ID / 指纹哈希（用于去重）",
        db_index=True,
    )

    direction = models.CharField(
        max_length=8,
        choices=DIRECTION_CHOICES,
        default='in',
        help_text="消息方向",
        db_index=True,
    )

    content_type = models.CharField(
        max_length=16,
        choices=CONTENT_TYPE_CHOICES,
        default='text',
        help_text="内容类型",
    )

    content = models.TextField(blank=True, default='', help_text="消息正文（非文本类型为描述或URL）")
    raw_payload = models.JSONField(default=dict, blank=True, help_text="原始抓取数据（便于追溯）")
    received_at = models.DateTimeField(help_text="消息时间（快手端时间）", db_index=True)
    processed = models.BooleanField(default=False, help_text="是否已被回复引擎处理", db_index=True)

    class Meta:
        db_table = 'core_kuaishou_message'
        verbose_name = '快手消息流水'
        verbose_name_plural = verbose_name
        ordering = ('-received_at',)
        constraints = [
            models.UniqueConstraint(
                fields=['conversation', 'external_msg_id'],
                name='uniq_kuaishou_msg_conv_extid',
            ),
        ]
        indexes = [
            models.Index(fields=['conversation', 'direction', 'processed']),
            models.Index(fields=['direction', 'received_at']),
        ]

    def __str__(self):
        return f"[{self.direction}] {self.content[:30]}"
