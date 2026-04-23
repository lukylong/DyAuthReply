#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: auth_service.py
@Desc: Auth Service - 认证业务逻辑层 - 处理用户认证、令牌生成、权限查询等业务逻辑
"""
"""
Auth Service - 认证业务逻辑层
处理用户认证、令牌生成、权限查询等业务逻辑
"""
import logging
from typing import List, Tuple

import jwt
from django.core.cache import cache
from django.utils import timezone

from application import settings
from common.fu_auth import (
    get_user_by_token,
    create_token,
    LoginAttemptProtection,
    TokenBlacklist
)
from common.fu_crud import get_or_none
from common.utils.device_util import extract_device_info
from core.login_log.login_log_service import LoginLogService
from core.user.user_model import User

logger = logging.getLogger(__name__)


class AuthService:
    """认证服务类 - 处理所有与认证相关的业务逻辑"""

    @staticmethod
    def authenticate_user(
            username: str,
            mobile: str,
            password: str,
            ip_address: str,
            user_agent: str = None
    ) -> User:
        """
        用户认证核心逻辑
        
        Args:
            username: 用户名
            mobile: 手机号
            password: 密码
            ip_address: IP地址
            user_agent: 用户代理字符串
        
        Returns:
            User: 认证成功的用户对象
        
        Raises:
            ValueError: 认证失败时抛出
        """
        # 确定登录标识
        identifier = mobile if mobile else username
        login_username = username or mobile

        # 检查登录尝试是否超过限制
        is_allowed, message = LoginAttemptProtection.check_login_attempt(identifier, ip_address)
        if not is_allowed:
            # 记录被限制的登录尝试
            try:
                LoginLogService.record_failed_login(
                    username=login_username,
                    login_ip=ip_address,
                    failure_reason=7,  # 其他错误
                    failure_message="登录尝试过于频繁，已被限制",
                    user_agent=user_agent,
                )
            except Exception as e:
                logger.warning(f"记录被限制登录失败: {str(e)}")
            raise ValueError(message)

        # 查找用户
        if mobile:
            user = get_or_none(User, mobile=mobile)
        else:
            user = get_or_none(User, username=username)

        # 用户不存在
        if user is None:
            LoginAttemptProtection.record_login_failure(identifier, ip_address)
            # 记录失败登录：用户不存在
            try:
                LoginLogService.record_failed_login(
                    username=login_username,
                    login_ip=ip_address,
                    failure_reason=1,  # 用户不存在
                    failure_message="用户不存在",
                    user_agent=user_agent,
                )
            except Exception as e:
                logger.warning(f"记录登录失败日志出错: {str(e)}")
            raise ValueError("用户名或密码错误")

        # 账户被禁用
        if not user.is_active:
            LoginAttemptProtection.record_login_failure(identifier, ip_address)
            # 记录失败登录：用户不激活
            try:
                LoginLogService.record_failed_login(
                    username=login_username,
                    login_ip=ip_address,
                    failure_reason=5,  # 用户不激活
                    failure_message="账户已被禁用",
                    user_agent=user_agent,
                )
            except Exception as e:
                logger.warning(f"记录登录失败日志出错: {str(e)}")
            raise ValueError("账户已被禁用")

        # 检查用户状态（禁用、锁定）
        if user.user_status == 0:  # 禁用
            LoginAttemptProtection.record_login_failure(identifier, ip_address)
            # 记录失败登录：用户已禁用
            try:
                LoginLogService.record_failed_login(
                    username=login_username,
                    login_ip=ip_address,
                    failure_reason=3,  # 用户已禁用
                    failure_message="用户已禁用",
                    user_agent=user_agent,
                )
            except Exception as e:
                logger.warning(f"记录登录失败日志出错: {str(e)}")
            raise ValueError("账户已被禁用")

        if user.user_status == 2:  # 锁定
            LoginAttemptProtection.record_login_failure(identifier, ip_address)
            # 记录失败登录：用户已锁定
            try:
                LoginLogService.record_failed_login(
                    username=login_username,
                    login_ip=ip_address,
                    failure_reason=4,  # 用户已锁定
                    failure_message="用户已锁定",
                    user_agent=user_agent,
                )
            except Exception as e:
                logger.warning(f"记录登录失败日志出错: {str(e)}")
            raise ValueError("账户已被锁定，请联系管理员")

        # 密码错误
        if not user.check_password(password):
            LoginAttemptProtection.record_login_failure(identifier, ip_address)
            # 记录失败登录：密码错误
            try:
                LoginLogService.record_failed_login(
                    username=login_username,
                    login_ip=ip_address,
                    failure_reason=2,  # 密码错误
                    failure_message="密码验证失败",
                    user_agent=user_agent,
                )

                # 检查是否应该锁定账户（防止暴力破解）
                if LoginLogService.check_user_locked(
                        username=login_username,
                        failed_threshold=5,
                        hours=1
                ):
                    user.user_status = 2  # 锁定用户
                    user.save(update_fields=['user_status'])
                    logger.warning(f"用户 {login_username} 因多次失败登录已被锁定")
            except Exception as e:
                logger.warning(f"记录登录失败日志出错: {str(e)}")
            raise ValueError("用户名或密码错误")

        # 认证成功，清除失败记录
        LoginAttemptProtection.record_login_success(identifier)

        return user

    @staticmethod
    def create_token_response(user: User) -> Tuple[str, str, int]:
        """
        创建令牌响应
        
        Args:
            user: 用户对象
        
        Returns:
            Tuple: (access_token, refresh_token, expire_time)
        """
        jwt_data = {
            "id": str(user.pk),  # 转换 UUID 为字符串
            "username": user.username,
            "email": user.email,
            "name": user.name,
        }
        access_token, refresh_token, access_token_expire = create_token(data=jwt_data)
        return access_token, refresh_token, access_token_expire

    @staticmethod
    def record_login_session(
            user: User,
            username: str,
            ip_address: str,
            user_agent: str,
            login_type: str = 'password'
    ) -> None:
        """
        记录登录会话信息
        
        Args:
            user: 用户对象
            username: 用户名
            ip_address: IP地址
            user_agent: 用户代理字符串
            login_type: 登录方式 (password/code/qrcode/gitee/github/qq/google/wechat/microsoft)
        """
        try:
            # 提取设备信息
            browser_type, os_type, device_type = extract_device_info(user_agent)

            # 记录成功登录到登录日志系统
            LoginLogService.record_success_login(
                username=username,
                user_id=str(user.id),
                login_ip=ip_address,
                user_agent=user_agent,
                browser_type=browser_type,
                os_type=os_type,
                device_type=device_type,
                login_type=login_type,  # 传递登录方式
            )

            # 更新用户最后登录时间、IP 和登录方式
            user.last_login = timezone.now()
            user.last_login_ip = ip_address
            user.last_login_type = login_type
            user.save(update_fields=['last_login', 'last_login_ip', 'last_login_type'])
        except Exception as e:
            logger.warning(f"记录登录会话失败: {str(e)}")

    @staticmethod
    def refresh_access_token(request) -> Tuple[User, str, str, int]:
        """
        刷新访问令牌
        
        Args:
            request: 请求对象
        
        Returns:
            Tuple: (new_access_token, refresh_token, expire_time)
        
        Raises:
            ValueError: 刷新失败时抛出
        """
        # 验证 refresh token
        user = get_user_by_token(request, "refresh")
        if not user:
            raise ValueError("无效的令牌")

        # 检查用户状态
        if not user.is_active:
            raise ValueError("账户已被禁用")

        # 获取 refresh token
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if not auth_header:
            raise ValueError("缺少授权头")

        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != 'bearer':
            raise ValueError("无效的授权头格式")

        refresh_token_str = parts[1]

        # 检查 refresh token 是否在黑名单中
        if TokenBlacklist.is_blacklisted(refresh_token_str, user.id):
            raise ValueError("刷新令牌已被撤销")

        # 刷新频率限制（5分钟内最多刷新50次）
        refresh_limit_key = f"refresh_limit:{user.id}"
        refresh_count = cache.get(refresh_limit_key, 0)
        if refresh_count >= 50:
            logger.warning(f"用户 {user.id} 超过刷新令牌限制")
            raise ValueError("刷新请求过于频繁，请稍后再试")

        # 增加刷新次数计数
        cache.set(refresh_limit_key, refresh_count + 1, 300)  # 5分钟过期

        # 生成新的 access token
        access_token, refresh_token, access_token_expire = AuthService.create_token_response(user)

        return user, access_token, refresh_token, access_token_expire

    @staticmethod
    def logout_user(request) -> None:
        """
        用户登出 - 将令牌加入黑名单

        Args:
            request: 请求对象
        """

        user_info = request.auth
        if not user_info:
            return

        user_id = user_info.id

        # 从请求头获取 token
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if auth_header.startswith('Bearer '):
            token = auth_header[7:]

            try:
                # 解码 token 获取过期时间
                payload = jwt.decode(
                    token,
                    settings.JWT_ACCESS_SECRET_KEY,
                    algorithms=[settings.JWT_ALGORITHM]
                )
                exp_time = payload.get('exp', 0)

                # 将 token 加入黑名单
                TokenBlacklist.add_to_blacklist(token, user_id, exp_time)
                logger.info(f"用户 {user_id} 的令牌已加入黑名单")
            except jwt.ExpiredSignatureError:
                logger.info(f"用户 {user_id} 的令牌已过期")
            except jwt.InvalidTokenError as e:
                logger.warning(f"无效的令牌: {str(e)}")

    @staticmethod
    def get_user_permission_codes(user: User) -> List[str]:
        """
        获取用户的权限代码列表

        Args:
            user: 用户对象

        Returns:
            List[str]: 权限代码列表
        """
        # 尝试从缓存获取
        # cache_key = f"user_perm_codes:{user.id}"
        # cached_codes = cache.get(cache_key)
        # if cached_codes is not None:
        #     return cached_codes
        #
        # if user.is_superuser:
        #     # 超级管理员获取所有按钮权限
        #     buttons = Button.objects.filter(status=True).values_list('code', flat=True)
        # else:
        #     # 普通用户获取其角色关联的按钮权限
        #     button_ids = user.role.values_list("button__id", flat=True)
        #     buttons = Button.objects.filter(
        #         id__in=button_ids,
        #         status=True
        #     ).values_list('code', flat=True)
        #
        # # 转为列表并去重
        # code_list = list(set(buttons))
        #
        # # 缓存结果（10分钟）
        # cache.set(cache_key, code_list, 600)
        #
        # return code_list
        pass
