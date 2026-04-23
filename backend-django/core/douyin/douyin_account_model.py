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

    WORK_MODE_CHOICES = [
        ('auto', '全自动回复'),
        ('manual', '仅人工介入'),
        ('hybrid', '混合（命中规则自动回、其它人工）'),
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

    # ---------- 多账号托管增强字段 ----------
    group = models.ForeignKey(
        to='core.DouyinAccountGroup',
        on_delete=models.SET_NULL,
        db_constraint=False,
        null=True, blank=True,
        related_name='accounts',
        help_text="所属分组",
        db_index=True,
    )

    tags = models.JSONField(
        default=list, blank=True,
        help_text="账号标签，字符串数组（如 ['主号','教育','测试']）",
    )

    work_mode = models.CharField(
        max_length=16,
        choices=WORK_MODE_CHOICES,
        default='auto',
        db_index=True,
        help_text="工作模式",
    )

    priority = models.IntegerField(
        default=0,
        help_text="并发调度优先级（越大越先分配 worker 资源）",
        db_index=True,
    )

    proxy_url = models.CharField(
        max_length=255, null=True, blank=True,
        help_text="代理地址（http://user:pass@host:port，避免多账号同 IP 被风控）",
    )

    user_agent = models.CharField(
        max_length=512, null=True, blank=True,
        help_text="浏览器 UA（为空则使用默认 Chromium UA）",
    )

    reply_today = models.IntegerField(default=0, help_text="今日已回复数（每日 0 点由调度器重置）")

    class Meta:
        db_table = 'core_douyin_account'
        verbose_name = '抖音账号'
        verbose_name_plural = verbose_name
        ordering = ('-sort', '-sys_create_datetime')
        indexes = [
            models.Index(fields=['owner', 'status']),
            models.Index(fields=['status', 'last_heartbeat']),
            models.Index(fields=['group', 'status']),
            models.Index(fields=['work_mode', 'status', 'priority']),
        ]

    def __str__(self):
        return f"{self.nickname} [{self.get_status_display()}]"

    def is_online(self) -> bool:
        """判断账号是否在线"""
        return self.status == 1

    def can_reply(self) -> bool:
        """判断账号当前是否处于可自动回复状态"""
        return self.status == 1
