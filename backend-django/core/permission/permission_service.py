#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: permission_service.py
@Desc: Permission Service - 权限生成服务 - 从 Django Ninja Router 自动扫描 API 并生成权限
"""
"""
Permission Service - 权限生成服务
从 Django Ninja Router 自动扫描 API 并生成权限
"""
import logging
from typing import List, Dict, Tuple

from common.fu_cache import PermissionCacheManager
from core.menu.menu_model import Menu
from core.permission.permission_model import Permission

logger = logging.getLogger(__name__)


class PermissionGenerator:
    """权限生成器 - 从 API 路由自动生成权限"""

    @staticmethod
    def extract_api_info(path: str, method: str) -> Tuple[str, str]:
        """
        从 API 路径提取菜单编码和权限编码
        
        示例：
        - /api/core/user -> menu_code: 'user', perm_code: 'user:read'
        - /api/core/user (POST) -> menu_code: 'user', perm_code: 'user:create'
        """
        # 移除前缀
        path = path.replace('/api/core/', '').replace('/api/system/', '')

        # 提取第一个路径段作为菜单编码
        parts = path.strip('/').split('/')
        menu_code = parts[0] if parts else 'unknown'

        # 根据 HTTP 方法映射权限操作
        method_map = {
            'GET': 'read',
            'POST': 'create',
            'PUT': 'update',
            'PATCH': 'update',
            'DELETE': 'delete',
        }

        operation = method_map.get(method.upper(), 'access')
        perm_code = f"{menu_code}:{operation}"

        return menu_code, perm_code

    @staticmethod
    def get_all_routes_from_ninja_api(api_instance) -> List[Dict]:
        """
        从 NinjaAPI 实例获取所有已注册的路由
        
        返回格式：
        [
            {
                'path': '/api/core/user',
                'method': 'GET',
                'operation_id': 'list_user',
                'summary': '获取用户列表'
            }
        ]
        """
        routes = []

        try:
            # 方法1：通过 get_openapi_schema 获取 OpenAPI 规范（最可靠）
            if hasattr(api_instance, 'get_openapi_schema'):
                try:
                    schema = api_instance.get_openapi_schema()
                    if schema and 'paths' in schema:
                        for path, path_item in schema['paths'].items():
                            for method, operation in path_item.items():
                                if method in ['get', 'post', 'put', 'delete', 'patch']:
                                    routes.append({
                                        'path': path,
                                        'method': method.upper(),
                                        'operation_id': operation.get('operationId', ''),
                                        'summary': operation.get('summary', ''),
                                    })
                    logger.info(f"通过 OpenAPI schema 获取到 {len(routes)} 个路由")
                    return routes
                except Exception as e:
                    logger.warning(f"通过 OpenAPI schema 获取路由失败: {str(e)}")

            # 方法2：访问 NinjaAPI 的 _routers 属性
            if hasattr(api_instance, '_routers'):
                for router in api_instance._routers:
                    if hasattr(router, 'path_operations'):
                        for path, operations in router.path_operations.items():
                            for operation in operations:
                                routes.append({
                                    'path': path,
                                    'method': operation.methods[0] if operation.methods else 'GET',
                                    'operation_id': getattr(operation, 'operation_id', ''),
                                    'summary': getattr(operation, 'summary', ''),
                                })
                if routes:
                    logger.info(f"通过 _routers 获取到 {len(routes)} 个路由")
                    return routes

            # 方法3：尝试访问 _operations 属性
            if hasattr(api_instance, '_operations'):
                for operation in api_instance._operations:
                    if hasattr(operation, 'path') and hasattr(operation, 'methods'):
                        routes.append({
                            'path': operation.path,
                            'method': operation.methods[0] if operation.methods else 'GET',
                            'operation_id': getattr(operation, 'operation_id', ''),
                            'summary': getattr(operation, 'summary', ''),
                        })
                if routes:
                    logger.info(f"通过 _operations 获取到 {len(routes)} 个路由")
                    return routes

            logger.warning("无法从 NinjaAPI 获取路由信息")

        except Exception as e:
            logger.error(f"获取路由失败: {str(e)}")

        return routes

    @staticmethod
    def auto_generate_permissions(api_instance, dry_run: bool = False) -> Dict:
        """
        自动生成权限
        
        Args:
            api_instance: NinjaAPI 实例
            dry_run: 如果为 True，只返回将要生成的权限，不实际创建
        
        Returns:
            {
                'created': 10,
                'skipped': 5,
                'failed': 0,
                'permissions': [...]
            }
        """
        routes = PermissionGenerator.get_all_routes_from_ninja_api(api_instance)

        created_count = 0
        skipped_count = 0
        failed_count = 0
        permissions_data = []

        for route in routes:
            try:
                menu_code, perm_code = PermissionGenerator.extract_api_info(
                    route['path'],
                    route['method']
                )

                # 查找对应的菜单
                menu = Menu.objects.filter(code=menu_code).first()
                if not menu:
                    logger.warning(f"菜单 {menu_code} 不存在，跳过权限 {perm_code}")
                    skipped_count += 1
                    continue

                # 检查权限是否已存在
                exists = Permission.objects.filter(
                    menu_id=menu.id,
                    code=perm_code
                ).exists()

                if exists:
                    skipped_count += 1
                    continue

                # 获取 HTTP 方法编码
                http_method_map = {
                    'GET': 0,
                    'POST': 1,
                    'PUT': 2,
                    'DELETE': 3,
                    'PATCH': 4,
                }
                http_method = http_method_map.get(route['method'].upper(), 0)

                # 获取权限类型名称
                operation = perm_code.split(':')[1]
                operation_names = {
                    'create': 'create',
                    'read': 'read',
                    'update': 'update',
                    'delete': 'delete',
                    'access': 'access',
                }
                operation_name = operation_names.get(operation, operation)

                perm_data = {
                    'menu_id': menu.id,
                    'name': f"{menu.name}{operation_name}",
                    'code': perm_code,
                    'permission_type': 1,  # API权限
                    'api_path': route['path'],
                    'http_method': http_method,
                    'description': route['summary'] or f"{menu.name}{operation_name}权限",
                    'is_active': True,
                }

                if not dry_run:
                    Permission.objects.create(**perm_data)
                    created_count += 1
                    logger.info(f"创建权限: {perm_code}")
                else:
                    created_count += 1

                permissions_data.append(perm_data)

            except Exception as e:
                failed_count += 1
                logger.error(f"生成权限失败: {str(e)}")

        # 清除缓存
        if created_count > 0 and not dry_run:
            PermissionCacheManager.invalidate_permission_cache()

        return {
            'created': created_count,
            'skipped': skipped_count,
            'failed': failed_count,
            'permissions': permissions_data,
        }
