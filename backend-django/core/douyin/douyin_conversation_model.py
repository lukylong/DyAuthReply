#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: douyin_conversation_model.py
@Desc: Douyin Conversation Model - 抖音私信会话模型
        以 (account, peer_sec_uid) 唯一标识一条私信会话。
"""
from django.db import models

from common.fu_model import RootModel


class DouyinConversation(RootModel):
    """
    抖音私信会话

    一个抖音账号与一个抖音陌生/粉丝用户之间的私信会话。
    """

    account = models.ForeignKey(
        to='core.DouyinAccount',
        on_delete=models.CASCADE,
        db_constraint=False,
        related_name='conversations',
        help_text="本方抖音账号",
        db_index=True,
    )

    peer_sec_uid = models.CharField(
        max_length=128,
        help_text="对方抖音 sec_uid",
        db_index=True,
    )

    peer_nickname = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        help_text="对方昵称",
    )

    peer_avatar = models.CharField(
        max_length=512,
        null=True,
        blank=True,
        help_text="对方头像URL",
    )

    platform_conversation_id = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        help_text="抖音平台侧 conversation_id（HTTP 协议收发使用）",
        db_index=True,
    )

    last_message_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="最近一条消息时间（对方或本方）",
        db_index=True,
    )

    last_message_preview = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="最近消息预览（截断）",
    )

    unread_count = models.IntegerField(
        default=0,
        help_text="未读消息数",
    )

    is_blocked = models.BooleanField(
        default=False,
        help_text="是否拉黑（拉黑会话不再自动回复）",
    )

    class Meta:
        db_table = 'core_douyin_conversation'
        verbose_name = '抖音私信会话'
        verbose_name_plural = verbose_name
        ordering = ('-last_message_at', '-sys_create_datetime')
        constraints = [
            models.UniqueConstraint(
                fields=['account', 'peer_sec_uid'],
                name='uniq_douyin_conv_account_peer',
            ),
        ]
        indexes = [
            models.Index(fields=['account', 'last_message_at']),
        ]

    def __str__(self):
        return f"{self.peer_nickname or self.peer_sec_uid}"
