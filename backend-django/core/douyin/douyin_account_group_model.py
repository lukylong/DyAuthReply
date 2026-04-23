#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: douyin_account_group_model.py
@Desc: Douyin Account Group - 抖音账号分组
        按业务线 / 运营团队对账号进行分组管理，支持批量配置与权限隔离。
"""
from django.db import models

from common.fu_model import RootModel


class DouyinAccountGroup(RootModel):
    """抖音账号分组

    应用场景：
    - 多条业务线并行托管（主号矩阵、测试号、达人协作号）
    - 不同运营负责人管理不同分组
    - 对整个分组下发统一策略（如改回复间隔、静默时段）
    """

    name = models.CharField(
        max_length=64,
        help_text="分组名称",
        db_index=True,
    )

    color = models.CharField(
        max_length=16,
        default='#409EFF',
        help_text="分组颜色（用于前端标签展示）",
    )

    owner = models.ForeignKey(
        to='core.User',
        on_delete=models.SET_NULL,
        db_constraint=False,
        null=True,
        blank=True,
        related_name='douyin_account_groups',
        help_text="负责人",
    )

    default_daily_reply_quota = models.IntegerField(
        default=200,
        help_text="分组默认日回复上限（新入组账号继承）",
    )

    default_min_interval = models.IntegerField(default=8, help_text="分组默认最小回复间隔（秒）")
    default_max_interval = models.IntegerField(default=25, help_text="分组默认最大回复间隔（秒）")

    status = models.BooleanField(default=True, help_text="是否启用", db_index=True)

    remark = models.CharField(max_length=255, null=True, blank=True, help_text="备注")

    class Meta:
        db_table = 'core_douyin_account_group'
        verbose_name = '抖音账号分组'
        verbose_name_plural = verbose_name
        ordering = ('-sort', '-sys_create_datetime')

    def __str__(self):
        return self.name
