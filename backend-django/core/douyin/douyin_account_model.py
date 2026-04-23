#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: douyin_account_model.py
@Desc: Douyin Account Model - 抖音账号模型
        管理绑定到当前平台用户的抖音账号（创作者中心）登录态与运行参数。
"""
from django.db import models

from common.fu_model import RootModel


class DouyinAccount(RootModel):
    """
    抖音账号模型

    绑定到系统用户的抖音创作者账号，承载 Playwright 登录态文件路径、运行参数与风控策略。
    storage_state_path 所指向的文件使用 Fernet 对称加密存储，密钥配置在 DOUYIN_STORAGE_ENCRYPTION_KEY。
    """

    STATUS_CHOICES = [
        (0, '未登录'),
        (1, '在线'),
        (2, '登录失效'),
        (3, '已禁用'),
    ]

    nickname = models.CharField(
        max_length=64,
        help_text="抖音昵称",
        db_index=True,
    )

    sec_uid = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        unique=True,
        help_text="抖音用户唯一标识 sec_uid",
    )

    avatar = models.CharField(
        max_length=512,
        null=True,
        blank=True,
        help_text="抖音头像URL",
    )

    status = models.IntegerField(
        choices=STATUS_CHOICES,
        default=0,
        help_text="账号状态（0未登录/1在线/2登录失效/3已禁用）",
        db_index=True,
    )

    storage_state_path = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Playwright 登录态文件相对路径（加密存储）",
    )

    owner = models.ForeignKey(
        to='core.User',
        on_delete=models.CASCADE,
        db_constraint=False,
        related_name='douyin_accounts',
        help_text="所属平台用户",
        db_index=True,
    )

    daily_reply_quota = models.IntegerField(
        default=200,
        help_text="单日回复上限（风控）",
    )

    min_interval_seconds = models.IntegerField(
        default=8,
        help_text="两次回复最小间隔（秒）",
    )

    max_interval_seconds = models.IntegerField(
        default=25,
        help_text="两次回复最大间隔（秒）",
    )

    silent_start = models.TimeField(
        default='22:00',
        help_text="静默时段开始（不自动回复）",
    )

    silent_end = models.TimeField(
        default='08:00',
        help_text="静默时段结束",
    )

    last_heartbeat = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Worker 最近一次心跳时间",
    )

    last_login_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="最近登录成功时间",
    )

    remark = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="备注",
    )

    class Meta:
        db_table = 'core_douyin_account'
        verbose_name = '抖音账号'
        verbose_name_plural = verbose_name
        ordering = ('-sort', '-sys_create_datetime')
        indexes = [
            models.Index(fields=['owner', 'status']),
            models.Index(fields=['status', 'last_heartbeat']),
        ]

    def __str__(self):
        return f"{self.nickname} [{self.get_status_display()}]"

    def is_online(self) -> bool:
        """判断账号是否在线"""
        return self.status == 1

    def can_reply(self) -> bool:
        """判断账号当前是否处于可自动回复状态"""
        return self.status == 1
