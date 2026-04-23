#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: permission_api.py
@Desc: Permission API - 权限管理接口 - 提供权限的 CRUD 操作和高级功能
"""
"""
Permission API - 权限管理接口
提供权限的 CRUD 操作和高级功能
集成缓存机制，优化权限查询性能
"""
import logging
from typing import List

from django.db.models import Q
from django.shortcuts import get_object_or_404
from ninja import Router, Query
from ninja.errors import HttpError
from ninja.pagination import paginate

from common.fu_cache import PermissionCacheManager
from common.fu_crud import create, delete, update, retrieve, batch_delete
from common.fu_pagination import MyPagination
from common.fu_schema import response_success
from core.permission.permission_model import Permission
from core.permission.permission_service import PermissionGenerator

logger = logging.getLogger(__name__)
from core.permission.permission_schema import (
    PermissionSchemaOut,
    PermissionSchemaIn,
    PermissionSchemaPatch,
    PermissionFilters,
    PermissionSchemaDetail,
    PermissionBatchDeleteIn,
    PermissionBatchDeleteOut,
    PermissionBatchUpdateStatusIn,
    PermissionBatchUpdateStatusOut,
    PermissionBatchCreateFromRoutesIn,
    PermissionBatchCreateFromRoutesOut,
)

router = Router()


@router.post("/permission", response=PermissionSchemaOut, summary="创建权限")
def create_permission(request, data: PermissionSchemaIn):
    """
    创建新权限
    
    改进点：
    - 添加权限编码唯一性检查
    - 添加菜单存在性检查
    - 创建后清除权限缓存
    """
    # 检查同一菜单下是否已存在相同编码的权限
    exists = Permission.objects.filter(
        menu_id=data.menu_id,
        code=data.code
    ).exists()

    if exists:
        raise HttpError(400, f"该菜单下已存在权限编码: {data.code}")

    query_set = create(request, data, Permission)

    # 创建后清除权限缓存
    if query_set:
        PermissionCacheManager.invalidate_permission_cache()
        logger.info(f"权限已创建并清除缓存: {query_set.code}")

    return query_set


@router.delete("/permission/{permission_id}", response=PermissionSchemaOut, summary="删除权限")
def delete_permission(request, permission_id: str):
    """
    删除单个权限
    
    删除后清除权限缓存
    """
    instance = delete(permission_id, Permission)

    # 删除后清除权限缓存
    if instance:
        PermissionCacheManager.invalidate_permission_cache()
        logger.info(f"权限已删除并清除缓存: {instance.code}")

    return instance


@router.post("/permission/batch/delete", response=PermissionBatchDeleteOut, summary="批量删除权限")
def delete_batch_permission(request, data: PermissionBatchDeleteIn):
    """
    批量删除权限
    
    改进点：
    - 返回删除的数量
    - 删除后清除权限缓存
    """
    count = batch_delete(data.ids, Permission)

    # 批量删除后清除权限缓存
    if count > 0:
        PermissionCacheManager.invalidate_permission_cache()
        logger.info(f"已批量删除 {count} 个权限，清除缓存")

    return PermissionBatchDeleteOut(count=count)


@router.put("/permission/{permission_id}", response=PermissionSchemaOut, summary="更新权限（完全替换）")
def update_permission(request, permission_id: str, data: PermissionSchemaIn):
    """
    更新权限（PUT - 完全替换）
    
    改进点：
    - 检查权限编码的唯一性（排除自身）
    - 更新后清除权限缓存
    """
    # 检查同一菜单下是否已存在相同编码的权限（排除自身）
    exists = Permission.objects.filter(
        menu_id=data.menu_id,
        code=data.code
    ).exclude(id=permission_id).exists()

    if exists:
        raise HttpError(400, f"该菜单下已存在权限编码: {data.code}")

    instance = update(request, permission_id, data, Permission)

    # 更新后清除权限缓存
    if instance:
        PermissionCacheManager.invalidate_permission_cache()
        logger.info(f"权限已更新并清除缓存: {instance.code}")

    return instance


@router.patch("/permission/{permission_id}", response=PermissionSchemaOut, summary="部分更新权限")
def patch_permission(request, permission_id: str, data: PermissionSchemaPatch):
    """
    部分更新权限（PATCH - 只更新提供的字段）
    
    优势：
    - 只需提供需要修改的字段
    - 更灵活，适合前端表单部分更新
    - 减少网络传输数据量
    
    改进点：
    - 检查权限编码的唯一性（排除自身）
    """
    permission = get_object_or_404(Permission, id=permission_id)

    # 只更新提供的字段
    update_data = data.dict(exclude_unset=True)

    # 检查权限编码是否已存在（排除自身）
    if 'code' in update_data and 'menu_id' in update_data:
        exists = Permission.objects.filter(
            menu_id=update_data['menu_id'],
            code=update_data['code']
        ).exclude(id=permission_id).exists()

        if exists:
            raise HttpError(400, f"该菜单下已存在权限编码: {update_data['code']}")
    elif 'code' in update_data:
        # 只更新了 code，使用当前的 menu_id
        exists = Permission.objects.filter(
            menu_id=permission.menu_id,
            code=update_data['code']
        ).exclude(id=permission_id).exists()

        if exists:
            raise HttpError(400, f"该菜单下已存在权限编码: {update_data['code']}")

    # 更新字段
    for field, value in update_data.items():
        setattr(permission, field, value)

    permission.save()

    # 部分更新后清除权限缓存
    PermissionCacheManager.invalidate_permission_cache()
    logger.info(f"权限已部分更新并清除缓存: {permission.code}")

    return permission


@router.get("/permission", response=List[PermissionSchemaOut], summary="获取权限列表（分页）")
@paginate(MyPagination)
def list_permission(request, filters: PermissionFilters = Query(...)):
    """
    获取权限列表（分页）
    
    改进点：
    - 使用 select_related 优化查询
    - 支持多种过滤条件
    """
    query_set = retrieve(request, Permission, filters)

    return query_set


@router.get("/permission/all", response=List[PermissionSchemaOut], summary="获取所有权限（有缓存）")
def list_all_permission(request):
    """
    获取所有权限（不分页，有缓存）
    
    用于权限选择器等场景
    缓存时间: 30分钟（权限变更不频繁）
    """
    # 尝试从缓存获取
    cached_permissions = PermissionCacheManager.get_all_permissions()
    if cached_permissions is not None:
        logger.debug("从缓存返回所有权限")
        return cached_permissions

    # 从数据库查询
    query_set = list(Permission.objects.filter(is_active=True).select_related('menu'))

    # 缓存结果
    PermissionCacheManager.set_all_permissions(query_set)
    logger.debug(f"权限列表已缓存: {len(query_set)} 个权限")

    return query_set


@router.get("/permission/{permission_id}", response=PermissionSchemaDetail, summary="获取权限详情")
def get_permission(request, permission_id: str):
    """获取单个权限的详细信息"""
    permission = get_object_or_404(
        Permission.objects.select_related('menu'),
        id=permission_id
    )
    return permission


@router.get("/permission/by/menu/{menu_id}", response=List[PermissionSchemaOut], summary="根据菜单ID获取权限")
def get_permissions_by_menu(request, menu_id: str):
    """
    根据菜单ID获取该菜单下的所有权限
    
    改进点：
    - 支持菜单维度的权限查询
    - 按排序字段排序
    """
    permissions = Permission.objects.filter(
        menu_id=menu_id,
        is_active=True
    ).order_by('sort', 'sys_create_datetime')
    return permissions


@router.get("/permission/by/type/{permission_type}", response=List[PermissionSchemaOut], summary="根据类型获取权限")
def get_permissions_by_type(request, permission_type: int):
    """
    根据权限类型获取权限列表
    
    Args:
        permission_type: 0-按钮权限, 1-API权限, 2-数据权限, 3-其他权限
    """
    if permission_type not in [0, 1, 2, 3]:
        raise HttpError(400, "权限类型必须在 0-3 之间")

    permissions = Permission.objects.filter(
        permission_type=permission_type,
        is_active=True
    ).select_related('menu').order_by('menu_id', 'sort')
    return permissions


@router.post("/permission/batch/update-status", response=PermissionBatchUpdateStatusOut, summary="批量更新权限状态")
def batch_update_permission_status(request, data: PermissionBatchUpdateStatusIn):
    """
    批量启用或禁用权限
    
    改进点：
    - 支持批量状态管理
    """
    count = Permission.objects.filter(id__in=data.ids).update(is_active=data.is_active)
    return PermissionBatchUpdateStatusOut(count=count)


@router.get("/permission/search", response=List[PermissionSchemaOut], summary="搜索权限")
@paginate(MyPagination)
def search_permission(request, keyword: str = Query(None)):
    """
    搜索权限
    
    改进点：
    - 支持关键词搜索（名称、编码、描述）
    """
    query_set = Permission.objects.all()

    if keyword:
        query_set = query_set.filter(
            Q(name__icontains=keyword) |
            Q(code__icontains=keyword) |
            Q(description__icontains=keyword)
        )

    query_set = query_set.select_related('menu').order_by('sort', '-sys_create_datetime')
    return query_set


@router.get("/permission/export-template", summary="导出权限模板")
def export_permission_template(request):
    """
    导出权限Excel模板
    
    用于批量导入权限
    """
    # TODO: 实现导出功能
    return response_success("导出模板功能待实现")


@router.post("/permission/import", summary="导入权限")
def import_permission(request):
    """
    批量导入权限
    
    从Excel文件导入权限数据
    """
    # TODO: 实现导入功能
    return response_success("导入功能待实现")


@router.get("/permission/all/routes", summary="获取所有可用的 API 路由")
def get_all_routes(request):
    """
    获取所有已注册的 API 路由
    用于前端在创建权限时选择
    
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
    from application.main import api as ninja_api

    routes = PermissionGenerator.get_all_routes_from_ninja_api(ninja_api)
    return routes


@router.get("/permission/debug/routes", summary="调试：获取所有路由信息")
def debug_get_routes(request):
    """
    调试端点：获取所有已注册的路由信息
    用于诊断 auto_scan 功能
    """
    from application.main import api as ninja_api

    debug_info = {
        'api_instance_type': str(type(ninja_api)),
        'has_routers': hasattr(ninja_api, '_routers'),
        'has_operations': hasattr(ninja_api, '_operations'),
        'has_get_openapi_schema': hasattr(ninja_api, 'get_openapi_schema'),
        'attributes': [attr for attr in dir(ninja_api) if not attr.startswith('__')],
    }

    # 尝试获取路由
    routes = PermissionGenerator.get_all_routes_from_ninja_api(ninja_api)
    debug_info['routes_count'] = len(routes)
    debug_info['routes'] = routes[:10]  # 只返回前10个

    return debug_info


@router.post("/permission/batch/create-from-routes", response=PermissionBatchCreateFromRoutesOut,
             summary="从路由批量创建权限")
def batch_create_permissions_from_routes(request, data: PermissionBatchCreateFromRoutesIn):
    """
    从前端选择的路由批量创建权限
    
    前端会传递：
    - menu_id: 菜单ID
    - routes: 选中的路由列表（已编辑过的权限信息）
    
    后端流程：
    1. 验证菜单是否存在
    2. 逐个创建权限
    3. 检查权限编码唯一性
    4. 返回创建结果
    """
    from core.menu.menu_model import Menu

    # 验证菜单是否存在
    menu = Menu.objects.filter(id=data.menu_id).first()
    if not menu:
        return PermissionBatchCreateFromRoutesOut(
            created=0,
            skipped=0,
            failed=len(data.routes),
            errors=[f"菜单 {data.menu_id} 不存在"]
        )

    created_count = 0
    skipped_count = 0
    failed_count = 0
    errors = []

    for route in data.routes:
        try:
            # 检查权限是否已存在
            exists = Permission.objects.filter(
                menu_id=data.menu_id,
                code=route.code
            ).exists()

            if exists:
                skipped_count += 1
                continue

            # 创建权限
            Permission.objects.create(
                menu_id=data.menu_id,
                name=route.name,
                code=route.code,
                permission_type=route.permission_type,
                api_path=route.path,
                http_method=route.http_method,
                description=route.summary or f"{route.name}权限",
                is_active=route.is_active,
            )
            created_count += 1
            logger.info(f"创建权限: {route.code}")

        except Exception as e:
            failed_count += 1
            errors.append(f"创建权限 {route.code} 失败: {str(e)}")
            logger.error(f"创建权限 {route.code} 失败: {str(e)}")

    # 清除缓存
    if created_count > 0:
        PermissionCacheManager.invalidate_permission_cache()
        logger.info(f"批量创建 {created_count} 个权限，清除缓存")

    return PermissionBatchCreateFromRoutesOut(
        created=created_count,
        skipped=skipped_count,
        failed=failed_count,
        errors=errors
    )


@router.post("/permission/auto/scan", summary="从 Router 自动扫描并生成权限")
def auto_scan_and_generate_permissions(request, dry_run: bool = Query(False)):
    """
    从 Django Ninja Router 自动扫描所有 API 端点并生成权限
    
    参数：
    - dry_run: 如果为 true，只预览将要生成的权限，不实际创建
    
    返回：
    {
        'created': 10,
        'skipped': 5,
        'failed': 0,
        'permissions': [...]
    }
    
    工作流程：
    1. 扫描所有已注册的 API 路由
    2. 从路由路径和方法提取菜单编码和权限编码
    3. 查找对应的菜单
    4. 检查权限是否已存在
    5. 创建新权限并清除缓存
    """
    from application.main import api as ninja_api

    result = PermissionGenerator.auto_generate_permissions(ninja_api, dry_run=dry_run)
    return result
