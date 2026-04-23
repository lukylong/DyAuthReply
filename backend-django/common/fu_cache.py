#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: fu_cache.py
@Desc: 缓存工具类和管理工具 - 
"""
"""
缓存工具类和管理工具

统一管理系统缓存策略，支持多种缓存场景：
1. 数据缓存 - 频繁读取的数据
2. 会话缓存 - 用户会话和权限
3. 频率限制 - 登录、API 调用限流
4. 锁定机制 - 防暴力破解
5. 临时数据 - 验证码、临时令牌
"""
import logging
from functools import wraps
from typing import Any, Callable

from django.core.cache import cache

logger = logging.getLogger(__name__)


# ===============================================================
# 缓存策略常量定义
# ===============================================================

class CacheStrategy:
    """缓存策略：超时时间定义"""

    # 数据缓存 - 基础数据（低变频数据）
    DATA_CACHE_SHORT = 300  # 5分钟
    DATA_CACHE_MEDIUM = 1800  # 30分钟
    DATA_CACHE_LONG = 3600  # 1小时
    DATA_CACHE_VERY_LONG = 86400  # 1天

    # 会话和权限缓存
    SESSION_CACHE = 1800  # 30分钟（与session超时对齐）
    PERMISSION_CACHE = 300  # 5分钟
    ROLE_CACHE = 3600  # 1小时
    USER_CACHE = 1800  # 30分钟

    # 临时数据缓存
    TEMP_CODE_CACHE = 600  # 10分钟（验证码）
    TEMP_TOKEN_CACHE = 1800  # 30分钟
    TEMP_DATA_CACHE = 300  # 5分钟

    # 频率限制和安全
    RATE_LIMIT_CACHE = 60  # 1分钟（短期）
    LOGIN_ATTEMPT_CACHE = 300  # 5分钟
    IP_LOCKOUT_CACHE = 900  # 15分钟

    # 系统配置缓存
    CONFIG_CACHE = 86400  # 1天
    DICT_CACHE = 86400  # 1天（字典数据很少变动）
    MENU_CACHE = 3600  # 1小时


class CacheKeyPrefix:
    """缓存键前缀定义，便于管理和查询"""

    # 数据缓存
    DICT = "cache:dict"  # 字典数据
    DICT_ITEMS = "cache:dict_items"  # 字典项
    MENU = "cache:menu"  # 菜单
    DEPT = "cache:dept"  # 部门
    ROLE = "cache:role"  # 角色
    PERMISSION = "cache:permission"  # 权限

    # 会话和认证
    USER = "cache:user"  # 用户信息
    USER_PERMISSION = "cache:user_permission"  # 用户权限
    USER_ROLES = "cache:user_roles"  # 用户角色
    USER_MENUS = "cache:user_menus"  # 用户菜单
    SESSION = "cache:session"  # 会话
    TOKEN_BLACKLIST = "cache:token_blacklist"  # token黑名单

    # 临时数据
    SMS_CODE = "cache:sms_code"  # 短信验证码
    EMAIL_CODE = "cache:email_code"  # 邮箱验证码
    TEMP_TOKEN = "cache:temp_token"  # 临时令牌

    # 频率限制
    LOGIN_ATTEMPT = "cache:login_attempt"  # 登录尝试
    IP_LOCKOUT = "cache:ip_lockout"  # IP锁定
    API_RATE_LIMIT = "cache:api_rate_limit"  # API限流

    # 系统配置
    SYSTEM_CONFIG = "cache:system_config"  # 系统配置
    WHITE_API_LIST = "cache:white_api_list"  # 白名单API


# ===============================================================
# 缓存装饰器
# ===============================================================

def cache_result(timeout: int = 300, key_prefix: str = "cache:default"):
    """
    方法结果缓存装饰器
    
    :param timeout: 缓存超时时间（秒）
    :param key_prefix: 缓存键前缀
    
    使用示例：
        @cache_result(timeout=3600, key_prefix="cache:user")
        def get_user_info(user_id: str):
            return User.objects.get(id=user_id)
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            # 生成缓存键
            cache_key = f"{key_prefix}:{func.__name__}"

            # 将参数添加到缓存键
            if args:
                cache_key += f":{'_'.join(str(arg) for arg in args)}"
            if kwargs:
                cache_key += f":{'_'.join(f'{k}_{v}' for k, v in sorted(kwargs.items()))}"

            # 尝试从缓存获取
            result = cache.get(cache_key)
            if result is not None:
                logger.debug(f"缓存命中: {cache_key}")
                return result

            # 执行函数并缓存结果
            result = func(*args, **kwargs)
            cache.set(cache_key, result, timeout)
            logger.debug(f"缓存设置: {cache_key} (超时: {timeout}s)")

            return result

        return wrapper

    return decorator


def cache_list(timeout: int = 300, key_prefix: str = "cache:list"):
    """
    列表查询缓存装饰器
    
    :param timeout: 缓存超时时间（秒）
    :param key_prefix: 缓存键前缀
    
    使用示例：
        @cache_list(timeout=3600, key_prefix="cache:dict_list")
        def get_all_dicts():
            return Dict.objects.filter(status=True)
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            # 生成缓存键
            cache_key = f"{key_prefix}:{func.__name__}"
            if kwargs:
                cache_key += f":{'_'.join(f'{k}_{v}' for k, v in sorted(kwargs.items()))}"

            # 尝试从缓存获取
            result = cache.get(cache_key)
            if result is not None:
                logger.debug(f"缓存命中: {cache_key}")
                return result

            # 执行函数并缓存结果
            result = list(func(*args, **kwargs))
            cache.set(cache_key, result, timeout)
            logger.debug(f"缓存设置: {cache_key} (超时: {timeout}s)")

            return result

        return wrapper

    return decorator


# ===============================================================
# 缓存管理工具类
# ===============================================================

class CacheManager:
    """缓存管理工具类，提供统一的缓存操作接口"""

    @staticmethod
    def get(key: str, default: Any = None) -> Any:
        """获取缓存值"""
        value = cache.get(key, default)
        if value is not None:
            logger.debug(f"缓存命中: {key}")
        else:
            logger.debug(f"缓存未命中: {key}")
        return value

    @staticmethod
    def set(key: str, value: Any, timeout: int = 300) -> None:
        """设置缓存值"""
        cache.set(key, value, timeout)
        logger.debug(f"缓存设置: {key} (超时: {timeout}s)")

    @staticmethod
    def delete(key: str) -> None:
        """删除缓存"""
        cache.delete(key)
        logger.debug(f"缓存删除: {key}")

    @staticmethod
    def clear_by_prefix(prefix: str) -> int:
        """
        清除指定前缀的所有缓存
        
        :param prefix: 缓存键前缀
        :return: 删除的缓存项数
        """
        # 使用 django-redis 的 delete_pattern 方法，它会自动处理 key 前缀
        from django_redis import get_redis_connection

        try:
            # 方法1: 使用 cache.delete_pattern (推荐，自动处理前缀)
            count = cache.delete_pattern(f"{prefix}*")
            if count:
                logger.info(f"清除缓存前缀: {prefix} (删除 {count} 项)")
            return count or 0
        except AttributeError:
            # 方法2: 如果 delete_pattern 不可用，手动处理前缀
            redis_conn = get_redis_connection('default')
            # Django Redis 默认 key 前缀格式: :1:key_name
            # 需要添加前缀来匹配实际的 Redis key
            keys = redis_conn.keys(f":1:{prefix}*")
            count = len(keys)

            if keys:
                redis_conn.delete(*keys)
                logger.info(f"清除缓存前缀: {prefix} (删除 {count} 项)")

            return count

    @staticmethod
    def clear_all() -> None:
        """清除所有缓存"""
        cache.clear()
        logger.info("所有缓存已清除")

    @staticmethod
    def exists(key: str) -> bool:
        """检查缓存是否存在"""
        from django_redis import get_redis_connection
        redis_conn = get_redis_connection('default')
        return redis_conn.exists(key) > 0

    @staticmethod
    def get_stats() -> dict:
        """获取缓存统计信息"""
        from django_redis import get_redis_connection

        try:
            redis_conn = get_redis_connection('default')
            info = redis_conn.info()

            return {
                "used_memory": info.get('used_memory_human', 'N/A'),
                "used_memory_bytes": info.get('used_memory', 0),
                "connected_clients": info.get('connected_clients', 0),
                "total_commands_processed": info.get('total_commands_processed', 0),
                "expired_keys": info.get('expired_keys', 0),
                "evicted_keys": info.get('evicted_keys', 0),
            }
        except Exception as e:
            logger.error(f"获取缓存统计信息失败: {e}")
            return {}


# ===============================================================
# 字典缓存管理类
# ===============================================================

class DictCacheManager:
    """字典缓存管理，专门处理字典数据的缓存"""

    @staticmethod
    def get_dict_cache_key(dict_id: str = None, dict_code: str = None, suffix: str = "") -> str:
        """
        生成字典缓存键
        
        :param dict_id: 字典ID
        :param dict_code: 字典编码
        :param suffix: 后缀（可选）
        :return: 缓存键
        """
        if dict_id:
            return f"{CacheKeyPrefix.DICT}:id:{dict_id}{':' + suffix if suffix else ''}"
        elif dict_code:
            return f"{CacheKeyPrefix.DICT}:code:{dict_code}{':' + suffix if suffix else ''}"
        else:
            return f"{CacheKeyPrefix.DICT}:all{':' + suffix if suffix else ''}"

    @staticmethod
    def get_dict_items_cache_key(dict_id: str = None, dict_code: str = None) -> str:
        """
        生成字典项缓存键
        
        :param dict_id: 字典ID
        :param dict_code: 字典编码
        :return: 缓存键
        """
        if dict_id:
            return f"{CacheKeyPrefix.DICT_ITEMS}:dict_id:{dict_id}"
        elif dict_code:
            return f"{CacheKeyPrefix.DICT_ITEMS}:dict_code:{dict_code}"
        else:
            return f"{CacheKeyPrefix.DICT_ITEMS}:all"

    @staticmethod
    def get_dict(dict_id: str = None, dict_code: str = None):
        """
        获取缓存的字典
        
        :param dict_id: 字典ID
        :param dict_code: 字典编码
        :return: 字典对象或 None
        """
        cache_key = DictCacheManager.get_dict_cache_key(dict_id=dict_id, dict_code=dict_code)
        return CacheManager.get(cache_key)

    @staticmethod
    def set_dict(dict_obj, dict_id: str = None, dict_code: str = None) -> None:
        """
        缓存字典
        
        :param dict_obj: 字典对象
        :param dict_id: 字典ID
        :param dict_code: 字典编码
        """
        # 缓存字典ID索引
        if dict_id:
            cache_key = DictCacheManager.get_dict_cache_key(dict_id=dict_id)
            CacheManager.set(cache_key, dict_obj, CacheStrategy.DICT_CACHE)

        # 缓存字典编码索引
        if dict_code:
            cache_key = DictCacheManager.get_dict_cache_key(dict_code=dict_code)
            CacheManager.set(cache_key, dict_obj, CacheStrategy.DICT_CACHE)

    @staticmethod
    def get_dict_items(dict_id: str = None, dict_code: str = None):
        """
        获取缓存的字典项列表
        
        :param dict_id: 字典ID
        :param dict_code: 字典编码
        :return: 字典项列表或 None
        """
        cache_key = DictCacheManager.get_dict_items_cache_key(dict_id=dict_id, dict_code=dict_code)
        return CacheManager.get(cache_key)

    @staticmethod
    def set_dict_items(items_list, dict_id: str = None, dict_code: str = None) -> None:
        """
        缓存字典项列表
        
        :param items_list: 字典项列表
        :param dict_id: 字典ID
        :param dict_code: 字典编码
        """
        # 缓存字典ID索引
        if dict_id:
            cache_key = DictCacheManager.get_dict_items_cache_key(dict_id=dict_id)
            CacheManager.set(cache_key, items_list, CacheStrategy.DICT_CACHE)

        # 缓存字典编码索引
        if dict_code:
            cache_key = DictCacheManager.get_dict_items_cache_key(dict_code=dict_code)
            CacheManager.set(cache_key, items_list, CacheStrategy.DICT_CACHE)

    @staticmethod
    def invalidate_dict(dict_id: str = None, dict_code: str = None) -> None:
        """
        清除字典缓存
        
        :param dict_id: 字典ID
        :param dict_code: 字典编码
        """
        # 清除字典缓存
        if dict_id:
            cache_key = DictCacheManager.get_dict_cache_key(dict_id=dict_id)
            CacheManager.delete(cache_key)

        if dict_code:
            cache_key = DictCacheManager.get_dict_cache_key(dict_code=dict_code)
            CacheManager.delete(cache_key)

        # 清除字典项缓存
        if dict_id:
            cache_key = DictCacheManager.get_dict_items_cache_key(dict_id=dict_id)
            CacheManager.delete(cache_key)

        if dict_code:
            cache_key = DictCacheManager.get_dict_items_cache_key(dict_code=dict_code)
            CacheManager.delete(cache_key)

        logger.info(f"字典缓存已清除: dict_id={dict_id}, dict_code={dict_code}")

    @staticmethod
    def invalidate_all() -> None:
        """清除所有字典缓存"""
        CacheManager.clear_by_prefix(CacheKeyPrefix.DICT)
        CacheManager.clear_by_prefix(CacheKeyPrefix.DICT_ITEMS)
        logger.info("所有字典缓存已清除")


# ===============================================================
# 用户权限缓存管理
# ===============================================================

class UserCacheManager:
    """用户相关缓存管理"""

    @staticmethod
    def get_user_permissions(user_id: str):
        """获取缓存的用户权限"""
        cache_key = f"{CacheKeyPrefix.USER_PERMISSION}:{user_id}"
        return CacheManager.get(cache_key)

    @staticmethod
    def set_user_permissions(user_id: str, permissions) -> None:
        """缓存用户权限"""
        cache_key = f"{CacheKeyPrefix.USER_PERMISSION}:{user_id}"
        CacheManager.set(cache_key, permissions, CacheStrategy.PERMISSION_CACHE)

    @staticmethod
    def invalidate_user_cache(user_id: str) -> None:
        """清除用户相关缓存"""
        keys_to_delete = [
            f"{CacheKeyPrefix.USER}:{user_id}",
            f"{CacheKeyPrefix.USER_PERMISSION}:{user_id}",
            f"{CacheKeyPrefix.USER_ROLES}:{user_id}",
            f"{CacheKeyPrefix.USER_MENUS}:{user_id}",
        ]

        for key in keys_to_delete:
            CacheManager.delete(key)

        logger.info(f"用户缓存已清除: {user_id}")


# ===============================================================
# 速率限制缓存
# ===============================================================

class RateLimitManager:
    """API 速率限制管理"""

    @staticmethod
    def check_rate_limit(key: str, limit: int, window: int) -> tuple[bool, dict]:
        """
        检查速率限制
        
        :param key: 限制键（如用户ID、IP等）
        :param limit: 时间窗口内允许的请求数
        :param window: 时间窗口（秒）
        :return: (是否允许, 限制信息)
        """
        cache_key = f"{CacheKeyPrefix.API_RATE_LIMIT}:{key}"

        current = cache.get(cache_key, 0)

        if current >= limit:
            return False, {
                "limit": limit,
                "current": current,
                "window": window,
                "message": f"请求过于频繁，请在 {window} 秒后重试"
            }

        # 增加计数
        cache.set(cache_key, current + 1, window)

        return True, {
            "limit": limit,
            "current": current + 1,
            "remaining": limit - (current + 1),
            "window": window
        }


# ===============================================================
# 菜单缓存管理类
# ===============================================================

class MenuCacheManager:
    """菜单缓存管理，专门处理菜单树和用户菜单的缓存"""

    @staticmethod
    def get_all_menus():
        """获取缓存的所有菜单"""
        cache_key = f"{CacheKeyPrefix.MENU}:all"
        return CacheManager.get(cache_key)

    @staticmethod
    def set_all_menus(menus):
        """缓存所有菜单"""
        cache_key = f"{CacheKeyPrefix.MENU}:all"
        CacheManager.set(cache_key, menus, CacheStrategy.MENU_CACHE)
        logger.debug(f"菜单列表已缓存: {len(menus)} 个菜单")

    @staticmethod
    def get_menu_tree():
        """获取缓存的菜单树"""
        cache_key = f"{CacheKeyPrefix.MENU}:tree"
        return CacheManager.get(cache_key)

    @staticmethod
    def set_menu_tree(tree):
        """缓存菜单树"""
        cache_key = f"{CacheKeyPrefix.MENU}:tree"
        CacheManager.set(cache_key, tree, CacheStrategy.MENU_CACHE)
        logger.debug("菜单树已缓存")

    @staticmethod
    def get_root_menus():
        """获取缓存的根菜单列表"""
        cache_key = f"{CacheKeyPrefix.MENU}:root"
        return CacheManager.get(cache_key)

    @staticmethod
    def set_root_menus(menus):
        """缓存根菜单"""
        cache_key = f"{CacheKeyPrefix.MENU}:root"
        CacheManager.set(cache_key, menus, CacheStrategy.MENU_CACHE)
        logger.debug(f"根菜单已缓存: {len(menus)} 个")

    @staticmethod
    def get_user_menus(user_id: str):
        """获取缓存的用户可访问菜单"""
        cache_key = f"{CacheKeyPrefix.USER_MENUS}:{user_id}"
        return CacheManager.get(cache_key)

    @staticmethod
    def set_user_menus(user_id: str, menus):
        """缓存用户可访问的菜单"""
        cache_key = f"{CacheKeyPrefix.USER_MENUS}:{user_id}"
        # 用户菜单缓存时间较短（权限可能变更）
        CacheManager.set(cache_key, menus, CacheStrategy.PERMISSION_CACHE)
        logger.debug(f"用户菜单已缓存: {user_id} ({len(menus)} 个)")

    @staticmethod
    def get_user_menu_route(user_id: str):
        """获取缓存的用户菜单路由"""
        cache_key = f"{CacheKeyPrefix.MENU}:route:{user_id}"
        return CacheManager.get(cache_key)

    @staticmethod
    def set_user_menu_route(user_id: str, route):
        """缓存用户菜单路由"""
        cache_key = f"{CacheKeyPrefix.MENU}:route:{user_id}"
        CacheManager.set(cache_key, route, CacheStrategy.PERMISSION_CACHE)
        logger.debug(f"用户菜单路由已缓存: {user_id}")

    @staticmethod
    def invalidate_menu_cache() -> None:
        """清除所有菜单相关缓存"""
        # 清除菜单缓存
        CacheManager.delete(f"{CacheKeyPrefix.MENU}:all")
        CacheManager.delete(f"{CacheKeyPrefix.MENU}:tree")
        CacheManager.delete(f"{CacheKeyPrefix.MENU}:root")

        # 清除所有用户菜单缓存
        CacheManager.clear_by_prefix(f"{CacheKeyPrefix.USER_MENUS}")
        CacheManager.clear_by_prefix(f"{CacheKeyPrefix.MENU}:route")

        logger.info("所有菜单缓存已清除")

    @staticmethod
    def invalidate_user_menu_cache(user_id: str) -> None:
        """清除特定用户的菜单缓存"""
        keys_to_delete = [
            f"{CacheKeyPrefix.USER_MENUS}:{user_id}",
            f"{CacheKeyPrefix.MENU}:route:{user_id}",
        ]

        for key in keys_to_delete:
            CacheManager.delete(key)

        logger.info(f"用户菜单缓存已清除: {user_id}")


# ===============================================================
# 权限缓存管理类
# ===============================================================

class PermissionCacheManager:
    """
    权限缓存管理，专门处理权限和用户权限的缓存
    包括权限数据缓存和版本号管理
    """

    # 版本号管理相关常量
    USER_VERSION_KEY = "user_permission_version:{}"
    ROLE_VERSION_KEY = "role_permission_version:{}"
    GLOBAL_VERSION_KEY = "global_permission_version"
    VERSION_EXPIRE_TIME = 86400  # 24小时

    # ===============================================================
    # 权限版本号管理（用于即时失效）
    # ===============================================================

    @staticmethod
    def get_user_version(user_id: str) -> int:
        """
        获取用户权限版本号
        
        :param user_id: 用户ID
        :return: 版本号
        """
        key = PermissionCacheManager.USER_VERSION_KEY.format(user_id)
        version = cache.get(key, 0)
        return version

    @staticmethod
    def invalidate_user_permissions(user_id: str) -> None:
        """
        使用户所有权限缓存失效
        当用户角色变更时调用
        
        :param user_id: 用户ID
        """
        key = PermissionCacheManager.USER_VERSION_KEY.format(user_id)
        current_version = cache.get(key, 0)
        cache.set(key, current_version + 1, PermissionCacheManager.VERSION_EXPIRE_TIME)
        logger.info(f"已清除用户 {user_id} 的权限缓存，版本号: {current_version + 1}")

    @staticmethod
    def invalidate_global_permissions() -> None:
        """
        使所有权限缓存失效
        当权限规则全局变更时调用
        """
        current_version = cache.get(PermissionCacheManager.GLOBAL_VERSION_KEY, 0)
        cache.set(PermissionCacheManager.GLOBAL_VERSION_KEY, current_version + 1,
                  PermissionCacheManager.VERSION_EXPIRE_TIME)
        logger.info(f"已清除全局权限缓存，版本号: {current_version + 1}")

    @staticmethod
    def get_cache_version_key(user_id: str) -> str:
        """
        获取用户的综合版本号（用户版本 + 全局版本）
        
        :param user_id: 用户ID
        :return: 版本号字符串
        """
        user_version = PermissionCacheManager.get_user_version(user_id)
        global_version = cache.get(PermissionCacheManager.GLOBAL_VERSION_KEY, 0)
        return f"v{user_version}_{global_version}"

    # ===============================================================
    # 权限数据缓存
    # ===============================================================

    @staticmethod
    def get_all_permissions():
        """获取缓存的所有权限"""
        cache_key = f"{CacheKeyPrefix.PERMISSION}:all"
        return CacheManager.get(cache_key)

    @staticmethod
    def set_all_permissions(permissions):
        """缓存所有权限"""
        cache_key = f"{CacheKeyPrefix.PERMISSION}:all"
        CacheManager.set(cache_key, permissions, CacheStrategy.ROLE_CACHE)
        logger.debug(f"权限列表已缓存: {len(permissions)} 个权限")

    @staticmethod
    def get_permission(permission_id: str):
        """获取缓存的单个权限"""
        cache_key = f"{CacheKeyPrefix.PERMISSION}:{permission_id}"
        return CacheManager.get(cache_key)

    @staticmethod
    def set_permission(permission_id: str, permission):
        """缓存单个权限"""
        cache_key = f"{CacheKeyPrefix.PERMISSION}:{permission_id}"
        CacheManager.set(cache_key, permission, CacheStrategy.ROLE_CACHE)
        logger.debug(f"权限已缓存: {permission_id}")

    @staticmethod
    def get_role_permissions(role_id: str):
        """获取缓存的角色权限"""
        cache_key = f"{CacheKeyPrefix.PERMISSION}:role:{role_id}"
        return CacheManager.get(cache_key)

    @staticmethod
    def set_role_permissions(role_id: str, permissions):
        """缓存角色权限"""
        cache_key = f"{CacheKeyPrefix.PERMISSION}:role:{role_id}"
        CacheManager.set(cache_key, permissions, CacheStrategy.ROLE_CACHE)
        logger.debug(f"角色权限已缓存: {role_id} ({len(permissions)} 个)")

    @staticmethod
    def get_menu_permissions(menu_id: str):
        """获取缓存的菜单权限"""
        cache_key = f"{CacheKeyPrefix.PERMISSION}:menu:{menu_id}"
        return CacheManager.get(cache_key)

    @staticmethod
    def set_menu_permissions(menu_id: str, permissions):
        """缓存菜单权限"""
        cache_key = f"{CacheKeyPrefix.PERMISSION}:menu:{menu_id}"
        CacheManager.set(cache_key, permissions, CacheStrategy.ROLE_CACHE)
        logger.debug(f"菜单权限已缓存: {menu_id} ({len(permissions)} 个)")

    @staticmethod
    def invalidate_permission_cache() -> None:
        """清除所有权限相关缓存"""
        # 清除权限缓存
        CacheManager.delete(f"{CacheKeyPrefix.PERMISSION}:all")

        # 清除特定权限、角色权限和菜单权限缓存
        CacheManager.clear_by_prefix(f"{CacheKeyPrefix.PERMISSION}")

        # 清除用户权限缓存（因为权限变更了）
        CacheManager.clear_by_prefix(f"{CacheKeyPrefix.USER_PERMISSION}")

        logger.info("所有权限缓存已清除")

    @staticmethod
    def invalidate_role_permissions(role_id: str) -> None:
        """
        清除特定角色的权限缓存
        当角色权限变更时调用
        
        :param role_id: 角色ID
        """
        # 清除角色权限数据缓存
        cache_key = f"{CacheKeyPrefix.PERMISSION}:role:{role_id}"
        CacheManager.delete(cache_key)

        # 通过全局版本号使所有用户权限缓存失效
        PermissionCacheManager.invalidate_global_permissions()

        logger.info(f"角色权限缓存已清除: {role_id}")

    @staticmethod
    def invalidate_menu_permissions(menu_id: str) -> None:
        """清除特定菜单的权限缓存"""
        cache_key = f"{CacheKeyPrefix.PERMISSION}:menu:{menu_id}"
        CacheManager.delete(cache_key)
        logger.info(f"菜单权限缓存已清除: {menu_id}")


# ===============================================================
# 缓存预热
# ===============================================================

class CacheWarmer:
    """缓存预热工具，在应用启动时加载关键数据"""

    @staticmethod
    def warm_dict_cache() -> None:
        """预热字典缓存"""
        try:
            from core.dict.dict_model import Dict
            from core.dict_item.dict_item_model import DictItem

            # 加载所有启用的字典
            dicts = Dict.objects.filter(status=True)
            for dict_obj in dicts:
                DictCacheManager.set_dict(dict_obj, dict_id=str(dict_obj.id), dict_code=dict_obj.code)

                # 加载字典项
                items = list(DictItem.objects.filter(dict=dict_obj, status=True))
                DictCacheManager.set_dict_items(items, dict_id=str(dict_obj.id), dict_code=dict_obj.code)

            logger.info(f"字典缓存预热完成: {dicts.count()} 个字典")

        except Exception as e:
            logger.error(f"字典缓存预热失败: {e}")

    @staticmethod
    def warm_menu_cache() -> None:
        """预热菜单缓存"""
        try:
            from core.menu.menu_model import Menu

            # 加载根菜单
            root_menus = Menu.objects.filter(parent__isnull=True, hideInMenu=False)
            cache_key = f"{CacheKeyPrefix.MENU}:root"
            CacheManager.set(cache_key, list(root_menus), CacheStrategy.MENU_CACHE)

            logger.info(f"菜单缓存预热完成")

        except Exception as e:
            logger.error(f"菜单缓存预热失败: {e}")

    @staticmethod
    def warm_all_cache() -> None:
        """预热所有关键缓存"""
        logger.info("开始预热所有缓存...")
        CacheWarmer.warm_dict_cache()
        CacheWarmer.warm_menu_cache()
        logger.info("缓存预热完成")
