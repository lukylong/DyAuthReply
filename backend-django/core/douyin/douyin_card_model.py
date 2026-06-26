#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: douyin_card_model.py
@Desc: Douyin Card - 抖音伪装卡片
        卡片是可复用的"封面图 + 标题 + 描述 + 跳转链接"内容单元，规则可多选关联。
        worker 自动回复时发送卡片的落地页 URL（/c/<card_id>），抖音抓取该落地页的
        og 元信息（og:image/og:title/og:description）自动渲染成卡片气泡，
        用户点击后落地页 302 跳转到真实目标链接 target_url。
"""
from django.db import models

from common.fu_model import RootModel


class DouyinCard(RootModel):
    """
    抖音伪装卡片

    字段映射到落地页 og：
    - title       -> og:title       卡片标题
    - description -> og:description  卡片描述/副标题
    - cover_file_id -> og:image      封面图（file_manager 文件 UUID 软引用）
    - target_url  -> 落地页 302 跳转目标（真实链接）
    """

    title = models.CharField(
        max_length=200,
        help_text="卡片标题（→ og:title）",
        db_index=True,
    )

    description = models.CharField(
        max_length=500,
        blank=True,
        default='',
        help_text="卡片描述/副标题（→ og:description）",
    )

    cover_file_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text="封面图，file_manager 文件 UUID 软引用（→ og:image）",
    )

    target_url = models.CharField(
        max_length=1000,
        help_text="真实跳转链接，落地页 302 跳转目标（仅 http/https）",
    )

    remark = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="备注（后台展示用）",
    )

    status = models.BooleanField(default=True, help_text="是否启用", db_index=True)

    is_shared = models.BooleanField(
        default=False,
        help_text="是否跨用户共享（预留，沿用模板范式）",
    )

    # ---- 客户端同步来源（阶段②：客户端为真源，推送到公网托管落地页/封面）----
    source_device_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text="同步来源客户端设备指纹/ClientDevice id（公网侧记录；客户端本地为空）",
        db_index=True,
    )

    source_activation_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text="同步来源 license 激活 id（公网侧记录）",
    )

    sync_state = models.CharField(
        max_length=16,
        default='local',
        help_text="同步状态：local(本地无需同步)/pending/synced/failed（客户端侧使用）",
    )

    owner = models.ForeignKey(
        to='core.User',
        on_delete=models.SET_NULL,
        db_constraint=False,
        null=True,
        blank=True,
        related_name='douyin_cards',
        help_text="创建者",
    )

    class Meta:
        db_table = 'core_douyin_card'
        verbose_name = '抖音伪装卡片'
        verbose_name_plural = verbose_name
        ordering = ('-sort', '-sys_create_datetime')
        indexes = [
            models.Index(fields=['owner', 'status']),
        ]

    def __str__(self):
        return self.title
