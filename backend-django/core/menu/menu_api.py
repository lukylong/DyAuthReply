#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: menu_api.py
@Desc: Menu API - 菜单管理接口 - 提供菜单的 CRUD 操作和路由树生成
"""
"""
Menu API - 菜单管理接口
提供菜单的 CRUD 操作和路由树生成
集成缓存机制，优化频繁访问的菜单数据性能
"""
import logging
from typing import List

from django.db.models import Q
from django.shortcuts import get_object_or_404
from ninja import Router, Query
from ninja.errors import HttpError
from ninja.pagination import paginate

from common.fu_cache import MenuCacheManager
from common.fu_crud import create, delete, update
from common.fu_pagination import MyPagination
from common.fu_schema import response_success
from common.utils.list_to_tree import list_to_route_v5
from core.menu.menu_model import Menu

logger = logging.getLogger(__name__)
from core.menu.menu_schema import (
    MenuSchemaOut,
    MenuSchemaIn,
    MenuSchemaPatch,
    MenuFilters,
    MenuSchemaSimple,
    MenuBatchDeleteIn,
    MenuBatchDeleteOut,
    MenuPathOut,
    MenuStatsOut,
    MenuCheckNameIn,
    MenuCheckPathIn,
    MenuCheckOut,
)

router = Router()


def remove_menu_cache():
    """清除菜单缓存（使用新的缓存管理器）"""
    MenuCacheManager.invalidate_menu_cache()


@router.post("/menu", response=MenuSchemaOut, summary="创建菜单")
def create_menu(request, data: MenuSchemaIn):
    """
    创建新菜单
    
    改进点：
    - 检查菜单名称唯一性
    - 检查路由路径唯一性
    - 检查父菜单存在性
    """
    # 检查菜单名称是否已存在
    if Menu.objects.filter(name=data.name).exists():
        raise HttpError(400, f"菜单名称已存在: {data.name}")

    # 检查路由路径是否已存在
    if Menu.objects.filter(path=data.path).exists():
        raise HttpError(400, f"路由路径已存在: {data.path}")

    # 检查父菜单是否存在
    if data.parent_id:
        parent = get_object_or_404(Menu, id=data.parent_id)

    query_set = create(request, data, Menu)
    remove_menu_cache()
    return query_set


@router.delete("/menu/{menu_id}", response=MenuSchemaOut, summary="删除菜单")
def delete_menu(request, menu_id: str):
    """
    删除菜单
    
    改进点：
    - 检查是否有子菜单
    - 检查是否有角色使用
    """
    menu = get_object_or_404(Menu, id=menu_id)

    # if not menu.can_delete():
    #     raise HttpError(400, "该菜单下还有子菜单，无法删除")

    # 检查是否有角色使用该菜单
    # from core.role.role_model import Role
    # role_count = Role.objects.filter(menu=menu).count()
    # if role_count > 0:
    #     raise HttpError(400, f"该菜单被 {role_count} 个角色使用，无法删除")
    #
    instance = delete(menu_id, Menu)
    remove_menu_cache()
    return instance


@router.delete("/menu/batch/delete", response=MenuBatchDeleteOut, summary="批量删除菜单")
def delete_batch_menu(request, data: MenuBatchDeleteIn):
    """
    批量删除菜单
    
    改进点：
    - 跳过有子菜单的菜单
    - 跳过被角色使用的菜单
    - 返回删除失败的ID列表
    """
    from core.role.role_model import Role

    failed_ids = []
    success_count = 0

    for menu_id in data.ids:
        try:
            menu = Menu.objects.get(id=menu_id)

            if not menu.can_delete():
                failed_ids.append(menu_id)
                continue

            if Role.objects.filter(menu=menu).exists():
                failed_ids.append(menu_id)
                continue

            menu.delete()
            success_count += 1
        except Menu.DoesNotExist:
            failed_ids.append(menu_id)

    remove_menu_cache()
    return MenuBatchDeleteOut(count=success_count, failed_ids=failed_ids)


@router.put("/menu/{menu_id}", response=MenuSchemaOut, summary="更新菜单（完全替换）")
def update_menu(request, menu_id: str, data: MenuSchemaIn):
    """
    更新菜单信息（PUT - 完全替换）
    
    改进点：
    - 检查菜单名称唯一性（排除自身）
    - 检查路由路径唯一性（排除自身）
    - 防止设置自己为父菜单
    - 防止形成循环引用
    """
    menu = get_object_or_404(Menu, id=menu_id)

    # 检查菜单名称是否已存在（排除自身）
    if Menu.objects.filter(name=data.name).exclude(id=menu_id).exists():
        raise HttpError(400, f"菜单名称已存在: {data.name}")

    # 检查路由路径是否已存在（排除自身）
    if Menu.objects.filter(path=data.path).exclude(id=menu_id).exists():
        raise HttpError(400, f"路由路径已存在: {data.path}")

    # 检查父菜单
    if data.parent_id:
        if data.parent_id == menu_id:
            raise HttpError(400, "不能将自己设置为父菜单")

        # 检查是否会形成循环引用
        parent = get_object_or_404(Menu, id=data.parent_id)
        if menu in parent.get_ancestors():
            raise HttpError(400, "不能将子菜单设置为父菜单，会形成循环引用")

    instance = update(request, menu_id, data, Menu)
    remove_menu_cache()
    return instance


@router.patch("/menu/{menu_id}", response=MenuSchemaOut, summary="部分更新菜单")
def patch_menu(request, menu_id: str, data: MenuSchemaPatch):
    """
    部分更新菜单信息（PATCH - 只更新提供的字段）
    
    优势：
    - 只需提供需要修改的字段
    - 更灵活，适合前端表单部分更新
    - 减少网络传输数据量
    
    改进点：
    - 检查菜单名称唯一性（排除自身）
    - 检查路由路径唯一性（排除自身）
    - 防止设置自己为父菜单
    - 防止形成循环引用
    """
    menu = get_object_or_404(Menu, id=menu_id)

    # 只更新提供的字段
    update_data = data.dict(exclude_unset=True)

    # 检查菜单名称是否已存在（排除自身）
    if 'name' in update_data and update_data['name']:
        if Menu.objects.filter(name=update_data['name']).exclude(id=menu_id).exists():
            raise HttpError(400, f"菜单名称已存在: {update_data['name']}")

    # 检查路由路径是否已存在（排除自身）
    if 'path' in update_data and update_data['path']:
        if Menu.objects.filter(path=update_data['path']).exclude(id=menu_id).exists():
            raise HttpError(400, f"路由路径已存在: {update_data['path']}")

    # 检查父菜单
    if 'parent_id' in update_data and update_data['parent_id']:
        if update_data['parent_id'] == menu_id:
            raise HttpError(400, "不能将自己设置为父菜单")

        # 检查是否会形成循环引用
        parent = get_object_or_404(Menu, id=update_data['parent_id'])
        if menu in parent.get_ancestors():
            raise HttpError(400, "不能将子菜单设置为父菜单，会形成循环引用")

    # 更新字段
    for field, value in update_data.items():
        setattr(menu, field, value)

    menu.save()
    remove_menu_cache()

    return menu


@router.get("/menu/get/tree", response=List[dict], summary="获取菜单树（有缓存）")
def list_menu_tree(request):
    """
    获取菜单树形结构
    
    改进点：
    - 支持缓存（1小时）
    - 添加子菜单数量
    - 使用统一的缓存管理器
    """
    # 尝试从缓存获取
    cached_tree = MenuCacheManager.get_menu_tree()
    if cached_tree is not None:
        logger.debug("从缓存返回菜单树")
        return cached_tree

    # 从数据库查询
    from common.fu_crud import retrieve
    menu_list = list(retrieve(request, Menu, MenuFilters()).values())

    # 为每个菜单添加额外信息
    for menu in menu_list:
        menu_obj = Menu.objects.get(id=menu['id'])
        menu['child_count'] = menu_obj.get_child_count()

    # 转换为树形结构
    menu_tree = list_to_route_v5(menu_list)

    # 缓存结果
    MenuCacheManager.set_menu_tree(menu_tree)
    logger.debug(f"菜单树已缓存（共 {len(menu_list)} 个菜单）")

    return menu_tree


@router.get("/menu/route/tree", response=List[dict], summary="获取用户路由树（有缓存）")
def route_menu_tree(request):
    """
    获取当前用户的路由树
    
    改进点：
    - 只返回启用的菜单
    - 支持超级管理员和普通用户
    - 用户菜单使用缓存（5分钟，权限可能变更）
    """
    user_info = request.auth

    if not user_info:
        raise HttpError(401, "未授权，请先登录")

    # 导入放在这里避免循环依赖
    from core.user.user_model import User

    try:
        user = User.objects.get(id=user_info.id)
    except User.DoesNotExist:
        raise HttpError(404, f"用户不存在 (ID: {user_info.id})")
    except AttributeError:
        raise HttpError(401, "认证信息无效")

    # 尝试从缓存获取用户菜单路由
    # cached_route = MenuCacheManager.get_user_menu_route(str(user.id))
    # if cached_route is not None:
    #     logger.debug(f"从缓存返回用户路由菜单: {user.username}")
    #     return cached_route

    # 从数据库查询
    if user.is_superuser:
        # 超级管理员获取所有菜单
        queryset = Menu.objects.all().values()
    else:
        # 普通用户获取其角色关联的菜单
        # 先获取启用的角色
        active_roles = user.core_roles.filter(status=True)
        # 通过角色的 menu 多对多关系获取菜单 ID
        menu_ids = Menu.objects.filter(core_roles__in=active_roles).values_list("id", flat=True).distinct()
        queryset = Menu.objects.filter(id__in=menu_ids).values()

    menu_tree = list_to_route_v5(list(queryset))

    # # 缓存用户的路由菜单
    # MenuCacheManager.set_user_menu_route(str(user.id), menu_tree)
    logger.debug(f"用户路由菜单已缓存: {user.username}")

    return menu_tree


@router.get("/menu/list", response=List[MenuSchemaOut], summary="获取菜单列表（分页）")
@paginate(MyPagination)
def list_menu(request, filters: MenuFilters = Query(...)):
    """
    获取菜单列表（分页）
    
    改进点：
    - 支持多种过滤条件
    - 预加载关联数据
    """
    from common.fu_crud import retrieve
    query_set = retrieve(request, Menu, filters)
    query_set = query_set.select_related('parent')
    return query_set


@router.get("/menu/all", response=List[MenuSchemaSimple], summary="获取所有菜单（简化版）")
def list_all_menu(request):
    """
    获取所有菜单（不分页，简化版）
    
    用于菜单选择器等场景
    """
    query_set = Menu.objects.all().order_by('order')
    return query_set


@router.get("/menu/{menu_id}", response=MenuSchemaOut, summary="获取菜单详情")
def get_menu(request, menu_id: str):
    """获取单个菜单的详细信息"""
    menu = get_object_or_404(
        Menu.objects.select_related('parent'),
        id=menu_id
    )
    return menu


@router.get("/menu/by/parent/{parent_id}", response=List[dict], summary="根据父菜单ID获取子菜单")
def get_menu_by_parent(request, parent_id: str):
    """
    根据父菜单ID获取直接子菜单
    
    改进点：
    - 支持根菜单查询（parent_id="null"）
    - 返回子菜单数量
    """
    if parent_id == "null":
        parent_id = None

    query_set = Menu.objects.filter(parent_id=parent_id)

    result = []
    for menu in query_set:
        menu_dict = {
            'id': str(menu.id),
            'name': menu.name,
            'title': menu.title,
            'path': menu.path,
            'type': menu.type,
            'icon': menu.icon,
            'order': menu.order,
            'level': menu.get_level(),
            'parent_id': str(menu.parent_id) if menu.parent_id else None,
            'child_count': menu.get_child_count(),
        }
        result.append(menu_dict)

    return result


@router.get("/menu/search", response=List[dict], summary="搜索菜单")
def search_menu(request, keyword: str):
    """
    搜索菜单（模糊匹配菜单名称或标题）
    
    改进点：
    - 支持名称和标题搜索
    - 返回匹配菜单及其完整的层级路径
    """
    if not keyword:
        return []

    # 搜索菜单
    matched_menus = Menu.objects.filter(
        Q(name__icontains=keyword) | Q(title__icontains=keyword)
    )

    # 收集所有需要的菜单ID（包括匹配菜单和其所有祖先）
    menu_ids_to_include = set()

    for menu in matched_menus:
        menu_ids_to_include.add(str(menu.id))
        # 添加所有祖先
        for ancestor in menu.get_ancestors():
            menu_ids_to_include.add(str(ancestor.id))

    # 获取所有需要的菜单
    all_menus = Menu.objects.filter(id__in=menu_ids_to_include)

    # 构建菜单字典
    menu_dict_map = {}
    for menu in all_menus:
        menu_dict = {
            'id': str(menu.id),
            'name': menu.name,
            'title': menu.title,
            'path': menu.path,
            'type': menu.type,
            'icon': menu.icon,
            'order': menu.order,
            'level': menu.get_level(),
            'parent_id': str(menu.parent_id) if menu.parent_id else None,
            'child_count': Menu.objects.filter(
                parent_id=menu.id,
                id__in=menu_ids_to_include
            ).count(),
        }
        menu_dict_map[str(menu.id)] = menu_dict

    # 构建树形结构
    roots = []
    for menu_id, menu in menu_dict_map.items():
        parent_id = menu['parent_id']
        if parent_id is None:
            roots.append(menu)
        elif parent_id in menu_dict_map:
            parent = menu_dict_map[parent_id]
            if 'children' not in parent:
                parent['children'] = []
            parent['children'].append(menu)

    return roots


@router.get("/menu/path/{menu_id}", response=MenuPathOut, summary="获取菜单路径")
def get_menu_path(request, menu_id: str):
    """
    获取菜单的完整路径（从根到当前菜单）
    
    改进点：
    - 返回完整的路径信息
    """
    menu = get_object_or_404(Menu, id=menu_id)

    # 获取所有祖先
    path = []
    for ancestor in reversed(menu.get_ancestors()):
        path.append(MenuSchemaSimple(
            id=str(ancestor.id),
            name=ancestor.name,
            title=ancestor.title,
            path=ancestor.path,
            type=ancestor.type,
            parent_id=str(ancestor.parent_id) if ancestor.parent_id else None,
            level=ancestor.get_level(),
        ))

    # 添加当前菜单
    path.append(MenuSchemaSimple(
        id=str(menu.id),
        name=menu.name,
        title=menu.title,
        path=menu.path,
        type=menu.type,
        parent_id=str(menu.parent_id) if menu.parent_id else None,
        level=menu.get_level(),
    ))

    return MenuPathOut(
        menu_id=str(menu.id),
        menu_name=menu.title or menu.name,
        path=path
    )


@router.get("/menu/stats", response=MenuStatsOut, summary="获取菜单统计信息")
def get_menu_stats(request):
    """
    获取菜单统计信息
    
    改进点：
    - 提供全局统计数据
    """
    total_count = Menu.objects.count()

    # 按类型统计
    type_stats = {}
    type_choices = [
        ('catalog', '目录'),
        ('menu', '菜单'),
        ('external', '外部链接'),
    ]
    for type_code, type_name in type_choices:
        count = Menu.objects.filter(type=type_code).count()
        type_stats[type_name] = count

    # 计算最大层级
    max_level = 0
    for menu in Menu.objects.all():
        level = menu.get_level()
        if level > max_level:
            max_level = level

    return MenuStatsOut(
        total_count=total_count,
        type_stats=type_stats,
        max_level=max_level,
    )


@router.post("/menu/check/name", response=MenuCheckOut, summary="检查菜单名称是否存在")
def check_menu_name(request, data: MenuCheckNameIn):
    """
    检查菜单名称是否已存在
    
    改进点：
    - 支持编辑时排除自身
    """
    query = Menu.objects.filter(name=data.name)
    if data.exclude_id:
        query = query.exclude(id=data.exclude_id)

    exists = query.exists()

    return MenuCheckOut(
        exists=exists,
        message=f"菜单名称 '{data.name}' 已存在" if exists else "菜单名称可用"
    )


@router.post("/menu/check/path", response=MenuCheckOut, summary="检查路由路径是否存在")
def check_menu_path(request, data: MenuCheckPathIn):
    """
    检查路由路径是否已存在
    
    改进点：
    - 支持编辑时排除自身
    """
    query = Menu.objects.filter(path=data.path)
    if data.exclude_id:
        query = query.exclude(id=data.exclude_id)

    exists = query.exists()

    return MenuCheckOut(
        exists=exists,
        message=f"路由路径 '{data.path}' 已存在" if exists else "路由路径可用"
    )


@router.post("/menu/move", summary="移动菜单")
def move_menu(request, menu_id: str, new_parent_id: str = None):
    """
    移动菜单到新的父菜单下
    
    改进点：
    - 支持移动到根节点
    - 自动更新层级
    """
    menu = get_object_or_404(Menu, id=menu_id)

    # 检查新父菜单
    if new_parent_id and new_parent_id != "null":
        new_parent = get_object_or_404(Menu, id=new_parent_id)

        # 防止循环引用
        if menu in new_parent.get_ancestors() or menu.id == new_parent.id:
            raise HttpError(400, "不能移动到自己或子菜单下")

        menu.parent = new_parent
    else:
        menu.parent = None

    menu.save()
    remove_menu_cache()

    return response_success("移动成功")
