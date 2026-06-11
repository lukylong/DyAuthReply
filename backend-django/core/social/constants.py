#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: core/social/constants.py
@Desc: 共用层平台维度常量
"""

# 平台标识
PLATFORM_DOUYIN = 'douyin'
PLATFORM_KUAISHOU = 'kuaishou'

PLATFORM_CHOICES = [
    (PLATFORM_DOUYIN, '抖音'),
    (PLATFORM_KUAISHOU, '快手'),
]

# 合法平台集合（供 api/schema 校验）
PLATFORM_VALUES = {value for value, _ in PLATFORM_CHOICES}
