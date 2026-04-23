# -*- coding:utf-8 -*-
"""
@Time : 2022/4/27 3:40 PM
@Author: binkuolo
@Des: JWT鉴权
"""
import logging
import re
from calendar import timegm
from datetime import timedelta, datetime

import jwt
from django.core.cache import cache
from ninja.errors import HttpError
from ninja.security import HttpBearer, APIKeyQuery
from system.models import User, Button, BasicConfig

from application import settings
from application.settings import API_WHITE_LIST
from env import IS_DEMO

logger = logging.getLogger(__name__)


# ===================== 登录防暴力破解 =====================
class LoginAttemptProtection:
    """
    登录尝试保护机制，防止暴力破解
    """
    FAILED_ATTEMPT_KEY = "login_attempt_{}"  # username 或 ip
    FAILED_ATTEMPT_LIMIT = 15  # 最多5次失败
    FAILED_ATTEMPT_TIMEOUT = 5 * 60  # 5分钟内
    IP_LOCKOUT_KEY = "login_lockout_ip_{}"
    IP_LOCKOUT_DURATION = 15 * 60  # IP锁定15分钟

    @classmethod
    def check_login_attempt(cls, username: str, ip_address: str) -> tuple[bool, str]:
        """
        检查登录尝试是否超过限制
        
        返回: (is_allowed, message)
        """
        # 检查IP是否被锁定
        ip_lockout_key = cls.IP_LOCKOUT_KEY.format(ip_address)
        if cache.get(ip_lockout_key):
            return False, "Too many failed login attempts. Please try again later."

        # 检查用户名失败次数
        attempt_key = cls.FAILED_ATTEMPT_KEY.format(f"user_{username}")
        attempts = cache.get(attempt_key, 0)

        if attempts >= cls.FAILED_ATTEMPT_LIMIT:
            # 锁定IP
            cache.set(ip_lockout_key, True, cls.IP_LOCKOUT_DURATION)
            return False, "Too many failed login attempts. Please try again later."

        return True, ""

    @classmethod
    def record_login_failure(cls, username: str, ip_address: str):
        """记录登录失败"""
        attempt_key = cls.FAILED_ATTEMPT_KEY.format(f"user_{username}")
        attempts = cache.get(attempt_key, 0)
        cache.set(attempt_key, attempts + 1, cls.FAILED_ATTEMPT_TIMEOUT)

    @classmethod
    def record_login_success(cls, username: str):
        """记录登录成功，清除失败计数"""
        attempt_key = cls.FAILED_ATTEMPT_KEY.format(f"user_{username}")
        cache.delete(attempt_key)


# ===================== Token 黑名单管理 =====================
class TokenBlacklist:
    """
    Token 黑名单管理，用于登出和密码修改后撤销 token
    """
    BLACKLIST_KEY = "token_blacklist_{}"

    @classmethod
    def add_to_blacklist(cls, token: str, user_id: str, exp_time: int):
        """
        将 token 加入黑名单
        
        :param token: JWT token
        :param user_id: 用户ID
        :param exp_time: token过期时间戳
        """
        key = cls.BLACKLIST_KEY.format(user_id)
        blacklist = cache.get(key, {})

        # 计算剩余有效期
        remaining_time = exp_time - int(datetime.utcnow().timestamp())
        if remaining_time > 0:
            blacklist[token] = exp_time
            cache.set(key, blacklist, remaining_time)

    @classmethod
    def is_blacklisted(cls, token: str, user_id: str) -> bool:
        """检查 token 是否在黑名单中"""
        key = cls.BLACKLIST_KEY.format(user_id)
        blacklist = cache.get(key, {})
        return token in blacklist

    @classmethod
    def revoke_user_tokens(cls, user_id: str):
        """撤销用户的所有 token"""
        key = cls.BLACKLIST_KEY.format(user_id)
        cache.delete(key)


METHOD = {
    'GET': 0,
    'POST': 1,
    'PUT': 2,
    'DELETE': 3,
}


def is_in_white_list(path, white_apis):
    """
    检查路径是否在白名单中，支持通配符*

    参数:
        path: 需要检查的路径
        white_apis: 包含通配符的白名单列表

    返回:
        如果路径匹配白名单中的任何模式，返回True，否则返回False
    """
    for api in white_apis:
        # 处理不带通配符的精确匹配
        if '*' not in api:
            if path == api:
                return True
        else:
            # 处理带通配符的情况
            # 分割通配符前后部分
            parts = api.split('*')
            # 检查路径是否以通配符前半部分开始，并且以通配符后半部分结束
            if len(parts) == 2:
                prefix, suffix = parts
                if path.startswith(prefix) and path.endswith(suffix):
                    return True
            # 处理只有前缀+*的情况，如/api/system/user/*
            elif len(parts) == 1 and api.endswith('*'):
                prefix = parts[0]
                if path.startswith(prefix):
                    return True
    return False


class ApiKey(APIKeyQuery):
    param_name = "token"

    def authenticate(self, request, key):
        verify_token(key)
        path = request.path
        method = request.method
        if path.startswith('/api/system/file_manager/stream/'):
            return key
        else:
            pass


class BearerAuth(HttpBearer):
    def authenticate(self, request, token):
        """
        验证token
        :param request:
        :param token:
        :return: user object or raise HttpError
        """
        try:
            path = request.path
            method = request.method

            #  改进：使用改进的 verify_token，已包含黑名单检查
            payload = verify_token(token, token_type="access")
            if not payload:
                raise HttpError(401, "Invalid or expired token")

            # 获取用户
            user_id = payload.get('id')
            if not user_id:
                raise HttpError(401, "Invalid token payload")

            try:
                user = User.objects.get(id=user_id)
            except User.DoesNotExist:
                raise HttpError(401, "User not found")

            #  检查用户是否被禁用或已删除
            if not user.is_active:
                raise HttpError(403, "User account is disabled or deleted")

            # 4. 演示环境保护（全局检查，在CRM检查之前）
            if IS_DEMO and method != 'GET':
                raise HttpError(403, "Modifications are not allowed in demo environment")

            # 5. 超级管理员直接通过
            if user.is_superuser:
                return user

            # 6. 检查白名单 API
            white_apis = API_WHITE_LIST if cache.get('white_apis') is None else [*cache.get('white_apis'),
                                                                                 *API_WHITE_LIST]
            if is_in_white_list(path, white_apis):
                return user

            # 7. CRM 模块权限检查（修复：不再跳过权限检查）
            if path.startswith('/api/crm'):
                # CRM 模块需要完整的权限检查
                button_ids = user.role.values_list('button__id', flat=True)
                if not button_ids.exists():
                    raise HttpError(403, "No permission to access CRM module")

            # 8. 按钮级权限检查
            # 正则表达式，捕获UUID前的字符串和UUID本身
            pattern = r'(.*?)([a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12})'
            # 使用lambda函数来构建新的字符串，保留前缀并替换UUID为:id
            path_with_placeholder = re.sub(pattern, lambda m: m.group(1) + ':id', path)

            button_ids = user.role.values_list('button__id', flat=True)
            queryset = Button.objects.filter(id__in=button_ids, api=path_with_placeholder, method=METHOD[method])
            if queryset.exists():
                return user
            else:
                raise HttpError(403, "No Permission")

        except HttpError:
            raise
        except Exception as e:
            logger.error(f"Error in BearerAuth.authenticate: {str(e)}")
            raise HttpError(401, "Authentication failed")


def create_token(data: dict):
    """
    创建token
    :param data: 加密数据
    :return: (access_token, refresh_token, access_token_exp_timestamp)
    """
    # 获取超时时间
    access_token_timeout = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    refresh_token_timeout = settings.JWT_REFRESH_TOKEN_EXPIRE_MINUTES

    # 如果没有配置，尝试从数据库获取
    if access_token_timeout is None:
        try:
            instance = BasicConfig.objects.first()
            if instance:
                access_token_timeout = instance.access_token_timeout
        except:
            pass
    if access_token_timeout is None:
        access_token_timeout = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES

    if refresh_token_timeout is None:
        try:
            instance = BasicConfig.objects.first()
            if instance:
                refresh_token_timeout = instance.refresh_token_timeout
        except:
            pass
    if refresh_token_timeout is None:
        refresh_token_timeout = settings.JWT_REFRESH_TOKEN_EXPIRE_MINUTES

    # 计算过期时间
    access_token_expire = datetime.utcnow() + timedelta(minutes=access_token_timeout)
    refresh_token_expire = datetime.utcnow() + timedelta(minutes=refresh_token_timeout)

    # 生成 access token 数据
    access_token_data = data.copy()
    access_token_data.update({
        "exp": access_token_expire,
        "iat": datetime.utcnow(),
        "type": "access",  # 新增：token 类型标识
    })

    # 生成 refresh token 数据（包含更少信息）
    refresh_token_data = {
        "id": data.get("id"),
        "username": data.get("username"),
        "exp": refresh_token_expire,
        "iat": datetime.utcnow(),
        "type": "refresh",  # 新增：token 类型标识
    }

    # 编码 token
    access_token = jwt.encode(access_token_data, settings.JWT_ACCESS_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    refresh_token = jwt.encode(refresh_token_data, settings.JWT_REFRESH_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    return access_token, refresh_token, timegm(access_token_expire.utctimetuple())


def get_user_by_token(request, token_type="access"):
    """
    通过token获取用户
    :param request: request对象
    :return: 用户对象 或 None
    """
    try:
        #  改进：更好地提取 Authorization header
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if not auth_header:
            logger.error("No authorization header found")
            return None

        # 确保格式正确
        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != 'bearer':
            logger.error(f"Invalid authorization header format: {parts[0] if parts else 'empty'}")
            return None

        token = parts[1]

        # 验证 access token
        payload = verify_token(token, token_type)
        if not payload:
            logger.warning("Token verification failed")
            return None

        # 获取用户
        user_id = payload.get('id')
        if not user_id:
            logger.error("No user id in token payload")
            return None

        try:
            from system.user.user_model import User
            user = User.objects.get(id=user_id)

            #  检查用户是否禁用（Django AbstractUser 的 is_active 字段）
            if not user.is_active:
                logger.warning(f"User {user_id} is not active")
                return None

            return user
        except Exception as e:
            logger.error(f"Error getting user by id {user_id}: {str(e)}")
            return None

    except Exception as e:
        logger.error(f"Error in get_user_by_token: {str(e)}")
        return None


def verify_token(token, token_type="access"):
    """
    验证token
    :param token: token string
    :param token_type: token类型 (access 或 refresh)
    :return: 解密后的token数据 或 None
    """
    try:
        #  改进：从 HTTP_AUTHORIZATION 头正确提取 token
        if not token:
            logger.error("Token is empty")
            return None

        # 选择对应的密钥和过期时间
        if token_type == "refresh":
            secret_key = settings.JWT_REFRESH_SECRET_KEY
        else:
            secret_key = settings.JWT_ACCESS_SECRET_KEY

        # 解码 token
        payload = jwt.decode(token, secret_key, algorithms=[settings.JWT_ALGORITHM])

        #  验证 token 类型
        if payload.get("type") != token_type:
            logger.error(f"Invalid token type: expected {token_type}, got {payload.get('type')}")
            return None

        #  检查黑名单（仅对 access token，需要提取 user_id）
        if token_type == "access":
            user_id = payload.get('id')
            if user_id and TokenBlacklist.is_blacklisted(token, user_id):
                logger.warning(f"Token is blacklisted for user {user_id}")
                return None

        return payload
    except jwt.ExpiredSignatureError:
        logger.error("Token has expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.error(f"Invalid token: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Error verifying token: {str(e)}")
        return None
