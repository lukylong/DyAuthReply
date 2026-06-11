#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: kuaishou_account_group_model.py
@Desc: Kuaishou Account Group - 快手账号分组
"""
from django.db import models

from common.fu_model import RootModel


class KuaishouAccountGroup(RootModel):
    """快手账号分组（按业务线/团队分组，统一下发默认策略）"""

    name = models.CharField(max_length=64, help_text="分组名称", db_index=True)
    color = models.CharField(max_length=16, default='#409EFF', help_text="分组颜色（用于前端标签展示）")

    owner = models.ForeignKey(
        to='core.User',
        on_delete=models.SET_NULL,
        db_constraint=False,
        null=True, blank=True,
        related_name='kuaishou_account_groups',
        help_text="负责人",
    )

    default_daily_reply_quota = models.IntegerField(default=200, help_text="分组默认日回复上限（新入组账号继承）")
    default_min_interval = models.IntegerField(default=8, help_text="分组默认最小回复间隔（秒）")
    default_max_interval = models.IntegerField(default=25, help_text="分组默认最大回复间隔（秒）")

    status = models.BooleanField(default=True, help_text="是否启用", db_index=True)
    remark = models.CharField(max_length=255, null=True, blank=True, help_text="备注")

    class Meta:
        db_table = 'core_kuaishou_account_group'
        verbose_name = '快手账号分组'
        verbose_name_plural = verbose_name
        ordering = ('-sort', '-sys_create_datetime')

    def __str__(self):
        return self.name
