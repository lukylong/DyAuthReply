#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: auth_schema.py
@Desc: Auth Schema - 认证相关的数据模型
"""
"""
Auth Schema - 认证相关的数据模型
"""
from typing import Optional

from ninja import Field, Schema


class LoginIn(Schema):
    """登录请求 Schema"""
    username: Optional[str] = Field(None, description="用户名")
    mobile: Optional[str] = Field(None, description="手机号")
    password: str = Field(..., description="密码", min_length=6)


class LoginOut(Schema):
    """登录响应 Schema (V5版本)"""
    id: str = Field(..., description="用户ID")
    accessToken: str = Field(..., description="访问令牌")
    refreshToken: str = Field(..., description="刷新令牌")
    expireTime: int = Field(..., description="过期时间戳")
    username: str = Field(..., description="用户名")
    realName: str = Field(..., description="真实姓名")


class RefreshTokenIn(Schema):
    """刷新令牌请求 Schema"""
    refreshToken: str = Field(..., description="刷新令牌")


class UserInfoOut(Schema):
    """用户信息响应 Schema (V5版本)"""
    id: str = Field(..., description="用户ID")
    username: str = Field(..., description="用户名")
    realName: str = Field(..., description="真实姓名")
    avatar: Optional[str] = Field(None, description="头像")


class LogoutOut(Schema):
    """登出响应 Schema"""
    msg: str = Field(..., description="响应消息")


class PermissionCodeOut(Schema):
    """权限代码响应 Schema"""
    codes: list[str] = Field(..., description="权限代码列表")
