#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: auth_api.py
@Desc: Auth API - 认证相关接口 - 提供登录、登出、令牌刷新、用户信息等功能
"""
"""
Auth API - 认证相关接口
提供登录、登出、令牌刷新、用户信息等功能
"""
import logging
from typing import List

from django.shortcuts import get_object_or_404
from ninja import Router
from ninja.errors import HttpError

from common.utils.request_util import get_request_ip
from core.auth.auth_schema import (
    LoginIn,
    LoginOut,
    UserInfoOut,
    LogoutOut,
)
from core.auth.auth_service import AuthService
from core.user.user_model import User

logger = logging.getLogger(__name__)

router = Router()


@router.post("/login", response=LoginOut, auth=None, summary="用户登录")
def login(request, data: LoginIn):
    """
    用户登录接口
    
    支持用户名或手机号登录
    
    改进点：
    - 使用新的登录日志系统记录登录操作
    - 记录更详细的设备和浏览器信息
    - 自动防止暴力破解
    """
    ip_address = get_request_ip(request)
    user_agent = request.META.get('HTTP_USER_AGENT', '')

    try:
        # 使用认证服务进行用户认证
        user = AuthService.authenticate_user(
            data.username,
            data.mobile,
            data.password,
            ip_address,
            user_agent=user_agent
        )

        # 生成令牌
        access_token, refresh_token, expire_time = AuthService.create_token_response(user)

        # 记录登录会话到新的登录日志系统
        login_username = data.username or data.mobile
        AuthService.record_login_session(user, login_username, ip_address, user_agent)

        return LoginOut(
            id=str(user.pk),
            accessToken=access_token,
            username=user.username,
            realName=user.name,
            refreshToken=refresh_token,
            expireTime=expire_time,
        )
    except ValueError as e:
        # 根据错误信息返回对应的HTTP状态码
        error_msg = str(e)
        if "频繁" in error_msg:
            raise HttpError(status_code=429, message=error_msg)
        elif "禁用" in error_msg or "锁定" in error_msg:
            raise HttpError(status_code=403, message=error_msg)
        else:
            raise HttpError(status_code=401, message=error_msg)


@router.post("/refresh_token", response=LoginOut, auth=None, summary="刷新访问令牌")
def refresh_token(request):
    """
    刷新访问令牌
    
    优化点：
    - 添加刷新频率限制
    - 支持令牌黑名单检查
    """
    try:
        user, access_token, refresh_token, access_token_expire = AuthService.refresh_access_token(request)

        return LoginOut(
            id=str(user.pk),
            accessToken=access_token,
            username=user.username,
            realName=user.name,
            refreshToken=refresh_token,
            expireTime=access_token_expire,
        )
    except ValueError as e:
        error_msg = str(e)
        if "频繁" in error_msg:
            raise HttpError(message=error_msg, status_code=429)
        elif "禁用" in error_msg:
            raise HttpError(message=error_msg, status_code=403)
        else:
            raise HttpError(message=error_msg, status_code=401)
    except Exception as e:
        logger.error(f"刷新令牌错误: {str(e)}", exc_info=True)
        raise HttpError(message="内部服务器错误", status_code=500)


@router.get("/logout", response=LogoutOut, summary="用户登出")
def logout(request):
    """
    用户登出接口
    
    将当前访问令牌和刷新令牌加入黑名单
    """
    AuthService.logout_user(request)
    return LogoutOut(msg="登出成功")


@router.get("/userinfo", response=UserInfoOut, summary="获取用户信息")
def get_userinfo(request):
    """
    获取当前登录用户的信息
    """
    user_info = request.auth
    if not user_info:
        raise HttpError(message="未授权", status_code=401)

    user = get_object_or_404(User, id=user_info.id)

    return UserInfoOut(
        id=str(user.pk),
        username=user.username,
        realName=user.name,
        avatar=user.avatar,
    )


@router.get("/permCode", response=List[str], summary="获取用户权限代码")
def get_permission_codes(request):
    """
    获取当前用户的按钮权限代码列表
    
    优化点：
    - 使用集合去重
    - 优化数据库查询
    - 添加缓存支持
    """
    user_info = request.auth
    if not user_info:
        raise HttpError(message="未授权", status_code=401)

    try:
        user = User.objects.get(id=user_info.id)
    except User.DoesNotExist:
        raise HttpError(message="用户不存在", status_code=404)

    return AuthService.get_user_permission_codes(user)
