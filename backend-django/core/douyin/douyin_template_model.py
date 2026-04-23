#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: douyin_template_model.py
@Desc: Douyin Reply Template - 抖音回复模板
        模板是可复用的"文本 + 链接 + 变量占位符"内容单元，规则 / 快捷回复均可引用。
        支持变量插值：{{nickname}}、{{time_greeting}}、{{product_name}} 等。
"""
from django.db import models

from common.fu_model import RootModel


class DouyinTemplate(RootModel):
    """
    抖音回复模板

    变量占位符示例（worker 在发送时按上下文填充）：
    - {{nickname}}      - 对方昵称
    - {{time_greeting}} - 早上好/下午好/晚上好
    - {{brand}}         - 品牌名
    - {{link_1}} {{link_2}} ... - 按序插入 links 中的链接
    """

    SEND_MODE_CHOICES = [
        ('multi_message', '文本 + 每条链接单独一条消息'),
        ('merged', '文本与链接合并为一条消息'),
        ('card_fallback', '尝试卡片降级到纯文本'),
    ]

    name = models.CharField(
        max_length=64,
        help_text="模板名称",
        db_index=True,
    )

    category = models.ForeignKey(
        to='core.DouyinTemplateCategory',
        on_delete=models.SET_NULL,
        db_constraint=False,
        null=True,
        blank=True,
        related_name='templates',
        help_text="所属分类",
        db_index=True,
    )

    content = models.TextField(
        help_text="模板文本内容，可含 {{变量}} 占位符与换行",
    )

    links = models.JSONField(
        default=list,
        blank=True,
        help_text="关联链接列表，元素格式 {title, url, note?}",
    )

    variables = models.JSONField(
        default=list,
        blank=True,
        help_text="模板使用到的变量列表（仅元信息，方便前端提示）",
    )

    send_mode = models.CharField(
        max_length=16,
        choices=SEND_MODE_CHOICES,
        default='multi_message',
        help_text="默认发送模式（规则引用模板时可覆盖）",
    )

    status = models.BooleanField(default=True, help_text="是否启用", db_index=True)

    is_shared = models.BooleanField(
        default=False,
        help_text="是否跨账号共享（false=仅模板归属用户可用）",
    )

    owner = models.ForeignKey(
        to='core.User',
        on_delete=models.SET_NULL,
        db_constraint=False,
        null=True,
        blank=True,
        related_name='douyin_templates',
        help_text="创建者",
    )

    use_count = models.IntegerField(default=0, help_text="累计引用次数")

    remark = models.CharField(max_length=255, null=True, blank=True, help_text="备注")

    class Meta:
        db_table = 'core_douyin_template'
        verbose_name = '抖音回复模板'
        verbose_name_plural = verbose_name
        ordering = ('-sort', '-sys_create_datetime')
        indexes = [
            models.Index(fields=['category', 'status']),
            models.Index(fields=['owner', 'is_shared']),
        ]

    def __str__(self):
        return self.name
