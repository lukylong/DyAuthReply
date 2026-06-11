#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: core/kuaishou/__init__.py
@Desc: Kuaishou - 快手账号托管自动回复模块

与抖音模块（core.douyin）平行的平台运行时层：账号、分组、会话、事件、会话/消息/回复日志、每日统计。
回复内容策略（规则 / 模板 / 快捷回复 / 黑名单）复用跨平台共用层 core.social，按 platform='kuaishou' 归属。

传输层（runtime/transport）针对快手私信 HTTP 协议，与抖音完全独立；协议逆向完成前以接口骨架占位。
"""
