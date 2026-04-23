#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: role_api.py
@Desc: Role API - 角色管理接口 - 提供角色的 CRUD 操作和高级功能
"""
"""
Role API - 角色管理接口
提供角色的 CRUD 操作和高级功能
"""
from typing import List

from django.db.models import Q, Count
from django.shortcuts import get_object_or_404
from ninja import Router, Query
from ninja.errors import HttpError
from ninja.pagination import paginate

from common.fu_crud import create, retrieve, delete
from common.fu_pagination import MyPagination
from common.fu_schema import response_success
from core.role.role_model import Role
from core.role.role_schema import (
    RoleSchemaOut,
    RoleSchemaIn,
    RoleSchemaPatch,
    RoleFilters,
    RoleSchemaDetail,
    RoleBatchDeleteIn,
    RoleBatchDeleteOut,
    RoleUserSchema,
    RoleUserFilter,
    RoleUserIn,
    RoleBatchUpdateStatusIn,
    RoleBatchUpdateStatusOut,
    RoleSimpleOut,
    RolePermissionUpdateIn,
    RoleMenuPermissionUpdateIn,
    RoleMenuListOut,
    MenuPermissionsOut,
)

router = Router()


@router.post("/role", response=RoleSchemaOut, summary="创建角色")
def create_role(request, data: RoleSchemaIn):
    """
    创建新角色
    
    改进点：
    - 添加角色编码唯一性检查
    - 分离多对多关系的处理
    """
    # 检查角色编码是否已存在
    if Role.objects.filter(code=data.code).exists():
        raise HttpError(400, f"角色编码已存在: {data.code}")

    dict_data = data.dict()
    # 提取多对多关系字段
    menu_ids = dict_data.pop("menu", [])
    permission_ids = dict_data.pop("permission", [])
    dept_ids = dict_data.pop("dept", [])

    # 创建角色
    role = create(request, dict_data, Role)

    # 设置多对多关系
    if menu_ids:
        role.menu.set(menu_ids)
    if permission_ids:
        role.permission.set(permission_ids)
    if dept_ids:
        role.dept.set(dept_ids)

    return role


@router.delete("/role/{role_id}", response=RoleSchemaOut, summary="删除角色")
def delete_role(request, role_id: str):
    """
    删除角色
    
    改进点：
    - 检查是否为系统角色
    - 检查是否有用户使用该角色
    """
    role = get_object_or_404(Role, id=role_id)

    # 系统角色不能删除
    if role.is_system_role():
        raise HttpError(400, "系统角色不能删除")

    # 检查是否有用户使用该角色
    user_count = role.core_users.count()
    if user_count > 0:
        raise HttpError(400, f"该角色下还有 {user_count} 个用户，无法删除")

    instance = delete(role_id, Role)
    return instance


@router.delete("/role/batch/delete", response=RoleBatchDeleteOut, summary="批量删除角色")
def delete_batch_role(request, data: RoleBatchDeleteIn):
    """
    批量删除角色
    
    改进点：
    - 跳过系统角色
    - 跳过有用户的角色
    - 返回删除失败的ID列表
    """
    failed_ids = []
    success_count = 0

    for role_id in data.ids:
        try:
            role = Role.objects.get(id=role_id)

            # 系统角色不能删除
            if role.is_system_role():
                failed_ids.append(role_id)
                continue

            # 有用户的角色不能删除
            if role.core_users.count() > 0:
                failed_ids.append(role_id)
                continue

            role.delete()
            success_count += 1
        except Role.DoesNotExist:
            failed_ids.append(role_id)

    return RoleBatchDeleteOut(count=success_count, failed_ids=failed_ids)


@router.put("/role/{role_id}", response=RoleSchemaOut, summary="更新角色（完全替换）")
def update_role(request, role_id: str, data: RoleSchemaIn):
    """
    更新角色（PUT - 完全替换）
    
    改进点：
    - 检查角色编码的唯一性（排除自身）
    - 系统角色不能修改某些字段
    - 分离多对多关系的处理
    """
    role = get_object_or_404(Role, id=role_id)

    # 检查角色编码是否已存在（排除自身）
    if Role.objects.filter(code=data.code).exclude(id=role_id).exists():
        raise HttpError(400, f"角色编码已存在: {data.code}")

    # 系统角色不能修改角色类型和编码
    if role.is_system_role():
        if data.role_type != 0:
            raise HttpError(400, "系统角色不能修改角色类型")
        if data.code != role.code:
            raise HttpError(400, "系统角色不能修改角色编码")

    # 更新角色基本信息
    permission_changed = False
    for attr, value in data.dict().items():
        if attr == "menu":
            role.menu.set(value)
        elif attr == "permission":
            role.permission.set(value)
            permission_changed = True
        elif attr == "dept":
            role.dept.set(value)
        else:
            setattr(role, attr, value)

    role.save()

    # 如果权限发生变更，清除缓存
    if permission_changed:
        from common.fu_cache import PermissionCacheManager
        PermissionCacheManager.invalidate_role_permissions(str(role_id))

    return role


@router.patch("/role/{role_id}", response=RoleSchemaOut, summary="部分更新角色")
def patch_role(request, role_id: str, data: RoleSchemaPatch):
    """
    部分更新角色（PATCH - 只更新提供的字段）
    
    优势：
    - 只需提供需要修改的字段
    - 更灵活，适合前端表单部分更新
    - 减少网络传输数据量
    
    改进点：
    - 检查角色编码的唯一性（排除自身）
    - 系统角色不能修改某些字段
    - 分离多对多关系的处理
    """
    role = get_object_or_404(Role, id=role_id)

    # 只更新提供的字段
    update_data = data.dict(exclude_unset=True)

    # 检查角色编码是否已存在（排除自身）
    if 'code' in update_data and update_data['code']:
        if Role.objects.filter(code=update_data['code']).exclude(id=role_id).exists():
            raise HttpError(400, f"角色编码已存在: {update_data['code']}")

    # 系统角色不能修改角色类型和编码
    if role.is_system_role():
        if 'role_type' in update_data and update_data['role_type'] != 0:
            raise HttpError(400, "系统角色不能修改角色类型")
        if 'code' in update_data and update_data['code'] != role.code:
            raise HttpError(400, "系统角色不能修改角色编码")

    # 更新字段
    permission_changed = False
    for attr, value in update_data.items():
        if attr == "menu":
            role.menu.set(value)
        elif attr == "permission":
            role.permission.set(value)
            permission_changed = True
        elif attr == "dept":
            role.dept.set(value)
        else:
            setattr(role, attr, value)

    role.save()

    # 如果权限发生变更，清除缓存
    if permission_changed:
        from common.fu_cache import PermissionCacheManager
        PermissionCacheManager.invalidate_role_permissions(str(role_id))

    return role


@router.get("/role", response=List[RoleSchemaOut], summary="获取角色列表（分页）")
@paginate(MyPagination)
def list_role(request, filters: RoleFilters = Query(...)):
    """
    获取角色列表（分页）
    
    改进点：
    - 使用 annotate 优化统计查询
    - 支持多种过滤条件
    """
    query_set = retrieve(request, Role, filters)
    # 优化查询：添加统计信息
    query_set = query_set.annotate(
        user_count=Count('core_users', distinct=True),
        menu_count=Count('menu', distinct=True),
        permission_count=Count('permission', distinct=True)
    )
    return query_set


@router.get("/role/all", response=List[RoleSimpleOut], summary="获取所有角色（简化版）")
def list_all_role(request):
    """
    获取所有可用角色（不分页，简化版）
    
    用于角色选择器等场景
    """
    query_set = Role.objects.filter(status=True).order_by('-priority', 'name')
    return query_set


@router.get("/role/by/ids", response=List[RoleSimpleOut], summary="根据ID列表获取角色")
def get_roles_by_ids(request, ids: str):
    """
    根据角色ID列表批量获取角色信息
    
    参数:
        ids: 逗号分隔的角色ID字符串，例如: "id1,id2,id3"
    
    返回:
        指定的角色列表
    """
    if not ids:
        return []

    # 解析ID列表
    role_ids = [id.strip() for id in ids.split(',') if id.strip()]
    if not role_ids:
        return []

    # 根据ID查询角色
    roles = Role.objects.filter(id__in=role_ids)
    return roles


@router.get("/role/{role_id}", response=RoleSchemaDetail, summary="获取角色详情")
def get_role(request, role_id: str):
    """
    获取单个角色的详细信息
    
    改进点：
    - 使用 prefetch_related 优化关联查询
    """
    role = get_object_or_404(
        Role.objects.prefetch_related('menu', 'permission', 'dept'),
        id=role_id
    )
    return role


@router.get("/role/users/by/role_id", response=List[RoleUserSchema], summary="获取角色下的用户列表")
@paginate(MyPagination)
def get_users_by_role(request, filters: RoleUserFilter = Query(...)):
    """
    获取指定角色下的所有用户
    
    改进点：
    - 支持用户名称搜索
    """
    role = get_object_or_404(Role, id=filters.role_id)
    users = role.core_users.all()

    # 如果提供了名称过滤
    if filters.name:
        users = users.filter(
            Q(name__icontains=filters.name) |
            Q(username__icontains=filters.name)
        )

    return users


@router.delete("/role/users/by/role_id", summary="从角色中移除用户")
def remove_user_from_role(request, data: RoleUserIn):
    """
    从角色中移除用户
    
    改进点：
    - 添加系统角色检查
    """
    role = get_object_or_404(Role, id=data.role_id)

    if not data.user_id:
        raise HttpError(400, "用户ID不能为空")

    # 导入放在这里避免循环依赖
    from core.user.user_model import User
    user = get_object_or_404(User, id=data.user_id)

    role.core_users.remove(user)
    return response_success("移除成功")


@router.post("/role/users/by/role_id", summary="为角色添加用户")
def add_user_to_role(request, data: RoleUserIn):
    """
    为角色添加用户
    
    改进点：
    - 批量添加用户
    - 检查用户是否已有该角色
    """
    role = get_object_or_404(Role, id=data.role_id)

    if not data.user_ids:
        raise HttpError(400, "用户ID列表不能为空")

    # 导入放在这里避免循环依赖
    from core.user.user_model import User

    added_count = 0
    for user_id in data.user_ids:
        user = get_object_or_404(User, id=user_id)

        # 检查用户是否已有该角色
        if role in user.core_roles.all():
            continue

        role.core_users.add(user)
        added_count += 1

    return response_success(f"成功添加 {added_count} 个用户")


@router.post("/role/batch/update-status", response=RoleBatchUpdateStatusOut, summary="批量更新角色状态")
def batch_update_role_status(request, data: RoleBatchUpdateStatusIn):
    """
    批量启用或禁用角色
    
    改进点：
    - 支持批量状态管理
    - 系统角色不能禁用
    """
    # 过滤掉系统角色
    roles = Role.objects.filter(id__in=data.ids, role_type=1)
    count = roles.update(status=data.status)

    return RoleBatchUpdateStatusOut(count=count)


@router.get("/role/search", response=List[RoleSchemaOut], summary="搜索角色")
@paginate(MyPagination)
def search_role(request, keyword: str = Query(None)):
    """
    搜索角色
    
    改进点：
    - 支持关键词搜索（名称、编码、描述）
    """
    query_set = Role.objects.all()

    if keyword:
        query_set = query_set.filter(
            Q(name__icontains=keyword) |
            Q(code__icontains=keyword) |
            Q(description__icontains=keyword)
        )

    query_set = query_set.annotate(
        user_count=Count('core_users', distinct=True),
        menu_count=Count('menu', distinct=True),
        permission_count=Count('permission', distinct=True)
    ).order_by('-priority', '-sys_update_datetime')

    return query_set


@router.get("/role/menu-permission-tree/{role_id}", summary="获取角色的菜单权限树")
def get_role_menu_permission_tree(request, role_id: str):
    """
    获取角色的菜单和权限树形结构
    
    用于角色权限配置界面
    """
    from core.menu.menu_model import Menu
    from core.permission.permission_model import Permission

    role = get_object_or_404(Role, id=role_id)

    # 获取所有菜单
    all_menus = Menu.objects.all().values('id', 'name', 'title', 'parent_id')

    # 获取该角色已分配的权限ID集合
    role_permission_ids = set(str(p.id) for p in role.permission.all())
    role_menu_ids = set(str(m.id) for m in role.menu.all())

    # 获取所有权限，按菜单分组
    all_permissions = Permission.objects.filter(is_active=True).values('id', 'name', 'code', 'menu_id',
                                                                       'permission_type')

    # 权限类型映射
    PERMISSION_TYPE_MAP = {
        0: '按钮权限',
        1: 'API权限',
        2: '数据权限',
        3: '其他权限',
    }

    permissions_by_menu = {}
    for perm in all_permissions:
        menu_id = str(perm['menu_id'])
        if menu_id not in permissions_by_menu:
            permissions_by_menu[menu_id] = []

        # 获取权限类型显示名称
        permission_type = perm.get('permission_type')
        if permission_type is None:
            permission_type = 3  # 默认为其他权限
        permission_type = int(permission_type)  # 确保是整数
        permission_type_display = PERMISSION_TYPE_MAP.get(permission_type, '其他权限')

        permissions_by_menu[menu_id].append({
            'id': str(perm['id']),
            'label': f"{perm['name']}",
            'name': perm['name'],
            'code': perm['code'],
            'permission_type': permission_type,
            'permission_type_display': permission_type_display,
            'checked': str(perm['id']) in role_permission_ids,
        })

    # 构建菜单树（包含权限）- 使用树形结构
    menu_map = {}
    root_menus = []

    # 第一步：创建所有菜单节点（包含权限作为 children 的一部分）
    for menu in all_menus:
        menu_id = str(menu['id'])
        parent_id = str(menu['parent_id']) if menu['parent_id'] else None

        # 初始化菜单节点，children 为空，稍后填充
        menu_node = {
            'id': menu_id,
            'label': menu['title'] or menu['name'],
            'name': menu['name'],
            'parent_id': parent_id,
            'checked': menu_id in role_menu_ids,
            'children': [],
        }
        menu_map[menu_id] = menu_node

    # 第二步：建立树形关系（子菜单作为 children）
    for menu in all_menus:
        menu_id = str(menu['id'])
        parent_id = str(menu['parent_id']) if menu['parent_id'] else None

        if parent_id and parent_id in menu_map:
            # 有父菜单，添加到父菜单的 children
            parent_node = menu_map[parent_id]
            parent_node['children'].append(menu_map[menu_id])
        else:
            # 根菜单
            root_menus.append(menu_map[menu_id])

    # 第三步：为叶子菜单（没有子菜单的菜单）添加权限
    for menu_id, menu_node in menu_map.items():
        # 如果菜单没有子菜单，添加该菜单的权限
        if not menu_node['children']:
            menu_node['children'] = permissions_by_menu.get(menu_id, [])

    return {
        'menu_tree': root_menus,
        'permission_tree': [],  # 保持兼容性
        'selected_menu_ids': list(role_menu_ids),
        'selected_permission_ids': list(role_permission_ids),
    }


@router.put("/role/{role_id}/permissions", summary="更新角色权限")
def update_role_permissions(request, role_id: str, data: RolePermissionUpdateIn):
    """
    更新角色关联的权限
    
    参数:
        permission_ids: 权限ID列表
    """
    from core.permission.permission_model import Permission

    role = get_object_or_404(Role, id=role_id)
    permission_ids = data.permission_ids

    # 验证权限是否存在
    valid_permissions = Permission.objects.filter(id__in=permission_ids)

    if len(valid_permissions) != len(permission_ids):
        raise HttpError(400, "存在无效的权限ID")

    # 更新权限关联
    role.permission.set(valid_permissions)

    # 清除角色权限缓存
    from common.fu_cache import PermissionCacheManager
    PermissionCacheManager.invalidate_role_permissions(str(role_id))

    return response_success(f"成功更新 {len(permission_ids)} 个权限")


@router.put("/role/{role_id}/menus-permissions", summary="更新角色菜单和权限")
def update_role_menus_permissions(request, role_id: str, data: RoleMenuPermissionUpdateIn):
    """
    同时更新角色关联的菜单和权限
    
    参数:
        menu_ids: 菜单ID列表
        permission_ids: 权限ID列表
    """
    from core.menu.menu_model import Menu
    from core.permission.permission_model import Permission

    role = get_object_or_404(Role, id=role_id)
    menu_ids = data.menu_ids
    permission_ids = data.permission_ids

    # 验证菜单是否存在
    valid_menus = Menu.objects.filter(id__in=menu_ids)
    if len(valid_menus) != len(menu_ids):
        raise HttpError(400, "存在无效的菜单ID")

    # 验证权限是否存在
    valid_permissions = Permission.objects.filter(id__in=permission_ids)
    if len(valid_permissions) != len(permission_ids):
        raise HttpError(400, "存在无效的权限ID")

    # 更新菜单和权限关联
    role.menu.set(valid_menus)
    role.permission.set(valid_permissions)

    # 清除角色权限缓存
    from common.fu_cache import PermissionCacheManager
    PermissionCacheManager.invalidate_role_permissions(str(role_id))

    return response_success(f"成功更新 {len(menu_ids)} 个菜单和 {len(permission_ids)} 个权限")


@router.get("/role/{role_id}/menus", response=RoleMenuListOut, summary="获取角色菜单列表")
def get_role_menus(request, role_id: str):
    """
    获取角色的菜单列表（不包含权限）
    
    返回:
        menu_tree: 菜单树结构
        selected_menu_ids: 已选中的菜单ID列表
    """
    from core.menu.menu_model import Menu
    from core.permission.permission_model import Permission

    role = get_object_or_404(Role, id=role_id)

    # 获取该角色已选中的菜单ID
    role_menu_ids = set(str(m.id) for m in role.menu.all())

    # 获取所有菜单
    all_menus = Menu.objects.all().values(
        'id', 'name', 'title', 'parent_id'
    )

    # 统计每个菜单的权限数量
    permission_counts = {}
    for menu in all_menus:
        menu_id = str(menu['id'])
        count = Permission.objects.filter(menu_id=menu_id, is_active=True).count()
        permission_counts[menu_id] = count

    # 构建菜单树
    menu_map = {}
    root_menus = []

    # 第一步：创建所有菜单节点
    for menu in all_menus:
        menu_id = str(menu['id'])
        parent_id = str(menu['parent_id']) if menu['parent_id'] else None

        menu_node = {
            'id': menu_id,
            'label': menu['title'] or menu['name'],
            'name': menu['name'],
            'parent_id': parent_id,
            'checked': menu_id in role_menu_ids,
            'permission_count': permission_counts.get(menu_id, 0),
            'children': [],
        }

        menu_map[menu_id] = menu_node

        if not parent_id:
            root_menus.append(menu_node)

    # 第二步：建立父子关系
    for menu_id, menu_node in menu_map.items():
        parent_id = menu_node['parent_id']
        if parent_id and parent_id in menu_map:
            menu_map[parent_id]['children'].append(menu_node)

    return {
        'menu_tree': root_menus,
        'selected_menu_ids': list(role_menu_ids),
    }


@router.get("/role/{role_id}/menu/{menu_id}/permissions", response=MenuPermissionsOut, summary="获取菜单的权限列表")
def get_menu_permissions(request, role_id: str, menu_id: str):
    """
    获取指定菜单的权限列表
    
    参数:
        role_id: 角色ID
        menu_id: 菜单ID
        
    返回:
        menu_id: 菜单ID
        permissions: 该菜单的权限列表
    """
    from core.permission.permission_model import Permission

    role = get_object_or_404(Role, id=role_id)

    # 获取该角色已选中的权限ID
    role_permission_ids = set(str(p.id) for p in role.permission.all())

    # 权限类型映射
    PERMISSION_TYPE_MAP = {
        0: '按钮权限',
        1: 'API权限',
        2: '数据权限',
        3: '其他权限',
    }

    # 获取该菜单的所有权限
    permissions = Permission.objects.filter(
        menu_id=menu_id,
        is_active=True
    ).values('id', 'name', 'code', 'permission_type')

    permission_list = []
    for perm in permissions:
        permission_type = perm.get('permission_type')
        if permission_type is None:
            permission_type = 3
        permission_type = int(permission_type)
        permission_type_display = PERMISSION_TYPE_MAP.get(permission_type, '其他权限')

        permission_list.append({
            'id': str(perm['id']),
            'label': perm['name'],
            'name': perm['name'],
            'code': perm['code'],
            'permission_type': permission_type,
            'permission_type_display': permission_type_display,
            'checked': str(perm['id']) in role_permission_ids,
        })

    return {
        'menu_id': menu_id,
        'permissions': permission_list,
    }


@router.post("/role/copy/{role_id}", response=RoleSchemaOut, summary="复制角色")
def copy_role(request, role_id: str, new_name: str, new_code: str):
    """
    复制现有角色
    
    改进点：
    - 支持角色复制功能
    - 自动复制菜单和权限配置
    """
    # 获取源角色
    source_role = get_object_or_404(
        Role.objects.prefetch_related('menu', 'permission', 'dept'),
        id=role_id
    )

    # 检查新编码是否已存在
    if Role.objects.filter(code=new_code).exists():
        raise HttpError(400, f"角色编码已存在: {new_code}")

    # 创建新角色
    new_role = Role.objects.create(
        name=new_name,
        code=new_code,
        role_type=1,  # 复制的角色都是自定义角色
        status=source_role.status,
        data_scope=source_role.data_scope,
        priority=source_role.priority,
        description=source_role.description,
        remark=f"复制自角色: {source_role.name}",
        sys_creator_id=request.auth.id
    )

    # 复制关联关系
    new_role.menu.set(source_role.menu.all())
    new_role.permission.set(source_role.permission.all())
    new_role.dept.set(source_role.dept.all())

    return new_role
