#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: core/social/__init__.py
@Desc: Social - 跨平台社媒托管共用层

抖音、快手等平台共享的"回复内容策略层"：模板分类、模板、快捷回复、回复规则、黑名单。

设计要点：
  - 通过 platform 字段区分归属平台（'douyin' / 'kuaishou' ...）。
  - 账号/分组采用「软引用」（account_id / group_id 存平台账号表的 UUID 字符串，不建数据库外键），
    因为不同平台的账号分别落在各自独立的表里，无法用单一 Django 外键约束。
  - 规则/快捷回复引用的模板、分类属于本共用层，使用真实外键。
"""
