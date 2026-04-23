#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: oauth_schema.py
@Desc: OAuth Schema - OAuth 数据模型 - 定义 OAuth 相关的请求和响应数据结构
"""
"""
OAuth Schema - OAuth 数据模型
定义 OAuth 相关的请求和响应数据结构
"""
from typing import Optional

from ninja import Schema


class OAuthCallbackSchema(Schema):
    """OAuth 回调请求参数 (通用)"""
    code: str
    state: Optional[str] = None


# 保留旧的 Schema 名称以兼容
GiteeCallbackSchema = OAuthCallbackSchema


class OAuthLoginResponseSchema(Schema):
    """OAuth 登录响应"""
    access_token: str
    refresh_token: str
    expire: int
    user_info: dict


class GiteeUserInfoSchema(Schema):
    """Gitee 用户信息"""
    id: int
    login: str
    name: str
    avatar_url: str
    email: Optional[str] = None
    bio: Optional[str] = None
    blog: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
