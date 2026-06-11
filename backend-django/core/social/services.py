#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: core/social/services.py
@Desc: 共用层跨平台校验工具

由于账号/分组采用软引用（不同平台落在各自独立的表），这里按 platform 路由到对应平台的
账号/分组表做存在性校验。尚未实现的平台（账号表未建）默认放行，避免阻塞共用层先行落地。
"""
from __future__ import annotations

from core.social.constants import PLATFORM_DOUYIN, PLATFORM_VALUES


def is_valid_platform(platform: str | None) -> bool:
    return platform in PLATFORM_VALUES


def account_exists(platform: str, account_id: str | None) -> bool:
    """校验某平台下账号是否存在。account_id 为空视为合法（全局规则）。"""
    if not account_id:
        return True
    if platform == PLATFORM_DOUYIN:
        from core.douyin.douyin_account_model import DouyinAccount
        return DouyinAccount.objects.filter(id=account_id).exists()
    # 其它平台账号表尚未建立时跳过校验（软引用，弱约束）
    return True


def group_exists(platform: str, group_id: str | None) -> bool:
    """校验某平台下分组是否存在。group_id 为空视为合法。"""
    if not group_id:
        return True
    if platform == PLATFORM_DOUYIN:
        from core.douyin.douyin_account_group_model import DouyinAccountGroup
        return DouyinAccountGroup.objects.filter(id=group_id).exists()
    return True
