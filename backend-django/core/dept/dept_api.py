#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: dept_api.py
@Desc: Dept API - 部门管理接口 - 提供部门的 CRUD 操作和树形结构查询
"""
"""
Dept API - 部门管理接口
提供部门的 CRUD 操作和树形结构查询
"""
from typing import List

from django.core.cache import cache
from django.db.models import Q
from django.shortcuts import get_object_or_404
from ninja import Router, Query
from ninja.errors import HttpError
from ninja.pagination import paginate

from common.fu_crud import create, delete, update
from common.fu_pagination import MyPagination
from common.fu_schema import response_success
from common.utils.list_to_tree import list_to_tree
from core.dept.dept_model import Dept
from core.dept.dept_schema import (
    DeptSchemaOut,
    DeptSchemaIn,
    DeptSchemaPatch,
    DeptFilters,
    DeptSchemaSimple,
    DeptSchemaBatchDeleteIn,
    DeptSchemaBatchDeleteOut,
    DeptBatchUpdateStatusIn,
    DeptBatchUpdateStatusOut,
    DeptPathOut,
    DeptUserSchema,
    DeptUserIn,
)

router = Router()

DEPT_CACHE_KEY = "core_dept_tree"
DEPT_CACHE_TIMEOUT = 3600  # 1小时


def remove_dept_cache():
    """清除部门缓存"""
    cache.delete(DEPT_CACHE_KEY)


@router.post("/dept", response=DeptSchemaOut, summary="创建部门")
def create_dept(request, data: DeptSchemaIn):
    """
    创建新部门
    
    改进点：
    - 检查部门编码唯一性
    - 检查父部门存在性
    - 自动计算层级和路径
    """
    # 检查部门编码是否已存在
    if data.code and Dept.objects.filter(code=data.code).exists():
        raise HttpError(400, f"部门编码已存在: {data.code}")

    # 检查父部门是否存在
    if data.parent_id:
        parent = get_object_or_404(Dept, id=data.parent_id)
        if not parent.status:
            raise HttpError(400, "父部门已被禁用，无法在其下创建子部门")

    query_set = create(request, data, Dept)
    remove_dept_cache()
    return query_set


@router.delete("/dept/{dept_id}", response=DeptSchemaOut, summary="删除部门")
def delete_dept(request, dept_id: str):
    """
    删除部门
    
    改进点：
    - 检查是否有子部门
    - 检查是否有用户
    """
    dept = get_object_or_404(Dept, id=dept_id)

    if not dept.can_delete():
        if not dept.is_leaf():
            raise HttpError(400, "该部门下还有子部门，无法删除")
        if dept.get_user_count() > 0:
            raise HttpError(400, f"该部门下还有 {dept.get_user_count()} 个用户，无法删除")

    instance = delete(dept_id, Dept)
    remove_dept_cache()
    return instance


@router.delete("/dept/batch/delete", response=DeptSchemaBatchDeleteOut, summary="批量删除部门")
def delete_batch_dept(request, data: DeptSchemaBatchDeleteIn):
    """
    批量删除部门
    
    改进点：
    - 跳过有子部门或用户的部门
    - 返回删除失败的ID列表
    """
    failed_ids = []
    success_count = 0

    for dept_id in data.ids:
        try:
            dept = Dept.objects.get(id=dept_id)

            if not dept.can_delete():
                failed_ids.append(dept_id)
                continue

            dept.delete()
            success_count += 1
        except Dept.DoesNotExist:
            failed_ids.append(dept_id)

    remove_dept_cache()
    return DeptSchemaBatchDeleteOut(count=success_count, failed_ids=failed_ids)


@router.put("/dept/{dept_id}", response=DeptSchemaOut, summary="更新部门（完全替换）")
def update_dept(request, dept_id: str, data: DeptSchemaIn):
    """
    更新部门信息（PUT - 完全替换）
    
    改进点：
    - 检查部门编码唯一性（排除自身）
    - 防止设置自己为父部门
    - 防止形成循环引用
    """
    dept = get_object_or_404(Dept, id=dept_id)

    # 检查部门编码是否已存在（排除自身）
    if data.code and Dept.objects.filter(code=data.code).exclude(id=dept_id).exists():
        raise HttpError(400, f"部门编码已存在: {data.code}")

    # 检查父部门
    if data.parent_id:
        if data.parent_id == dept_id:
            raise HttpError(400, "不能将自己设置为父部门")

        # 检查是否会形成循环引用
        parent = get_object_or_404(Dept, id=data.parent_id)
        if dept in parent.get_ancestors():
            raise HttpError(400, "不能将子部门设置为父部门，会形成循环引用")

    instance = update(request, dept_id, data, Dept)
    remove_dept_cache()
    return instance


@router.patch("/dept/{dept_id}", response=DeptSchemaOut, summary="部分更新部门")
def patch_dept(request, dept_id: str, data: DeptSchemaPatch):
    """
    部分更新部门信息（PATCH - 只更新提供的字段）
    
    优势：
    - 只需提供需要修改的字段
    - 更灵活，适合前端表单部分更新
    - 减少网络传输数据量
    
    改进点：
    - 检查部门编码唯一性（排除自身）
    - 防止设置自己为父部门
    - 防止形成循环引用
    """
    dept = get_object_or_404(Dept, id=dept_id)

    # 只更新提供的字段
    update_data = data.dict(exclude_unset=True)

    # 检查部门编码是否已存在（排除自身）
    if 'code' in update_data and update_data['code']:
        if Dept.objects.filter(code=update_data['code']).exclude(id=dept_id).exists():
            raise HttpError(400, f"部门编码已存在: {update_data['code']}")

    # 检查父部门
    if 'parent_id' in update_data and update_data['parent_id']:
        if update_data['parent_id'] == dept_id:
            raise HttpError(400, "不能将自己设置为父部门")

        # 检查是否会形成循环引用
        parent = get_object_or_404(Dept, id=update_data['parent_id'])
        if dept in parent.get_ancestors():
            raise HttpError(400, "不能将子部门设置为父部门，会形成循环引用")

    # 更新字段
    for field, value in update_data.items():
        setattr(dept, field, value)

    dept.save()
    remove_dept_cache()

    return dept


@router.get("/dept/tree", response=List[dict], summary="获取部门树")
def list_dept_tree(request, use_cache: bool = Query(True)):
    """
    获取部门树形结构
    
    改进点：
    - 支持缓存
    - 添加子部门数量和用户数量
    """
    # 尝试从缓存获取
    if use_cache:
        cached_tree = cache.get(DEPT_CACHE_KEY)
        if cached_tree:
            return cached_tree

    # 从数据库查询
    dept_queryset = Dept.objects.all().select_related('lead')

    # 构建部门字典列表
    dept_list = []
    for dept in dept_queryset:
        dept_dict = {
            'id': str(dept.id),
            'name': dept.name,
            'code': dept.code,
            'dept_type': dept.dept_type,
            'status': dept.status,
            'level': dept.level,
            'parent_id': str(dept.parent_id) if dept.parent_id else None,
            'lead_id': str(dept.lead_id) if dept.lead_id else None,
            'lead_name': dept.lead.name if dept.lead else None,
            'phone': dept.phone,
            'email': dept.email,
            'description': dept.description,
            'sort': dept.sort,
            'child_count': dept.get_child_count(),
            'user_count': dept.get_user_count(),
            'dept_type_display': dept.get_dept_type_display_name(),
        }
        dept_list.append(dept_dict)

    # 转换为树形结构
    dept_tree = list_to_tree(dept_list)

    # 缓存结果
    if use_cache:
        cache.set(DEPT_CACHE_KEY, dept_tree, DEPT_CACHE_TIMEOUT)

    return dept_tree


@router.get("/dept/list", response=List[DeptSchemaOut], summary="获取部门列表（分页）")
@paginate(MyPagination)
def list_dept(request, filters: DeptFilters = Query(...)):
    """
    获取部门列表（分页）
    
    改进点：
    - 支持多种过滤条件
    - 预加载关联数据
    """
    from common.fu_crud import retrieve
    query_set = retrieve(request, Dept, filters)
    query_set = query_set.select_related('parent', 'lead')
    return query_set


@router.get("/dept/all", response=List[DeptSchemaSimple], summary="获取所有部门（简化版）")
def list_all_dept(request):
    """
    获取所有启用的部门（不分页，简化版）
    
    用于部门选择器等场景
    """
    query_set = Dept.objects.filter(status=True).order_by('level', 'sort')
    return query_set


@router.get("/dept/{dept_id}", response=DeptSchemaOut, summary="获取部门详情")
def get_dept(request, dept_id: str):
    """获取单个部门的详细信息"""
    dept = get_object_or_404(
        Dept.objects.select_related('parent', 'lead'),
        id=dept_id
    )
    return dept


@router.get("/dept/by/parent/{parent_id}", response=List[dict], summary="根据父部门ID获取子部门")
def get_dept_by_parent(request, parent_id: str):
    """
    根据父部门ID获取直接子部门
    
    改进点：
    - 支持根部门查询（parent_id="null"）
    - 返回完整的部门信息（包含所有字段）
    """
    if parent_id == "null":
        parent_id = None

    query_set = Dept.objects.filter(parent_id=parent_id).select_related('lead')

    result = []
    for dept in query_set:
        dept_dict = {
            'id': str(dept.id),
            'name': dept.name,
            'code': dept.code,
            'dept_type': dept.dept_type,
            'dept_type_display': dept.get_dept_type_display_name(),
            'status': dept.status,
            'level': dept.level,
            'path': dept.path,
            'parent_id': str(dept.parent_id) if dept.parent_id else None,
            'lead_id': str(dept.lead_id) if dept.lead_id else None,
            'lead_name': dept.lead.name if dept.lead else None,
            'phone': dept.phone,
            'email': dept.email,
            'description': dept.description,
            'sort': dept.sort,
            'child_count': dept.get_child_count(),
            'user_count': dept.get_user_count(),
        }
        result.append(dept_dict)

    return result


@router.get("/dept/search", response=List[dict], summary="搜索部门")
def search_dept(request, keyword: str):
    """
    搜索部门（模糊匹配部门名称或编码）
    
    改进点：
    - 支持名称和编码搜索
    - 返回匹配部门及其完整的层级路径
    """
    if not keyword:
        return []

    # 搜索部门
    matched_depts = Dept.objects.filter(
        Q(name__icontains=keyword) | Q(code__icontains=keyword)
    )

    # 收集所有需要的部门ID（包括匹配部门和其所有祖先）
    dept_ids_to_include = set()

    for dept in matched_depts:
        dept_ids_to_include.add(dept.id)
        # 添加所有祖先
        for ancestor in dept.get_ancestors():
            dept_ids_to_include.add(ancestor.id)

    # 获取所有需要的部门
    all_depts = Dept.objects.filter(id__in=dept_ids_to_include).select_related('lead')

    # 构建部门字典
    dept_dict_map = {}
    for dept in all_depts:
        dept_dict = {
            'id': str(dept.id),
            'name': dept.name,
            'code': dept.code,
            'dept_type': dept.dept_type,
            'dept_type_display': dept.get_dept_type_display_name(),
            'status': dept.status,
            'level': dept.level,
            'path': dept.path,
            'parent_id': str(dept.parent_id) if dept.parent_id else None,
            'lead_id': str(dept.lead_id) if dept.lead_id else None,
            'lead_name': dept.lead.name if dept.lead else None,
            'phone': dept.phone,
            'email': dept.email,
            'description': dept.description,
            'sort': dept.sort,
            'child_count': Dept.objects.filter(
                parent_id=dept.id,
                id__in=dept_ids_to_include
            ).count(),
            'user_count': dept.get_user_count(),
        }
        dept_dict_map[str(dept.id)] = dept_dict

    # 构建树形结构
    roots = []
    for dept_id, dept in dept_dict_map.items():
        parent_id = dept['parent_id']
        if parent_id is None:
            roots.append(dept)
        elif parent_id in dept_dict_map:
            parent = dept_dict_map[parent_id]
            if 'children' not in parent:
                parent['children'] = []
            parent['children'].append(dept)

    return roots


@router.get("/dept/by/ids", response=List[dict], summary="根据ID列表获取部门")
def get_depts_by_ids(request, ids: str):
    """
    根据部门ID列表批量获取部门信息（包含完整的层级路径）
    
    改进点：
    - 返回树形结构，包含所有祖先
    """
    if not ids:
        return []

    dept_ids = [id.strip() for id in ids.split(',') if id.strip()]
    if not dept_ids:
        return []

    # 收集所有需要的部门ID
    dept_ids_to_include = set()
    target_depts = Dept.objects.filter(id__in=dept_ids)

    for dept in target_depts:
        dept_ids_to_include.add(str(dept.id))
        for ancestor in dept.get_ancestors():
            dept_ids_to_include.add(str(ancestor.id))

    # 获取所有需要的部门
    all_depts = Dept.objects.filter(id__in=dept_ids_to_include).select_related('lead')

    # 构建字典和树形结构（同 search_dept）
    dept_dict_map = {}
    for dept in all_depts:
        dept_dict = {
            'id': str(dept.id),
            'name': dept.name,
            'code': dept.code,
            'dept_type': dept.dept_type,
            'status': dept.status,
            'level': dept.level,
            'parent_id': str(dept.parent_id) if dept.parent_id else None,
            'child_count': Dept.objects.filter(
                parent_id=dept.id,
                id__in=dept_ids_to_include
            ).count(),
            'user_count': dept.get_user_count(),
        }
        dept_dict_map[str(dept.id)] = dept_dict

    roots = []
    for dept_id, dept in dept_dict_map.items():
        parent_id = dept['parent_id']
        if parent_id is None:
            roots.append(dept)
        elif parent_id in dept_dict_map:
            parent = dept_dict_map[parent_id]
            if 'children' not in parent:
                parent['children'] = []
            parent['children'].append(dept)

    return roots


@router.get("/dept/path/{dept_id}", response=DeptPathOut, summary="获取部门路径")
def get_dept_path(request, dept_id: str):
    """
    获取部门的完整路径（从根到当前部门）
    
    改进点：
    - 返回完整的路径信息
    """
    dept = get_object_or_404(Dept, id=dept_id)

    # 获取所有祖先
    path = []
    for ancestor in reversed(dept.get_ancestors()):
        path.append(DeptSchemaSimple(
            id=str(ancestor.id),
            name=ancestor.name,
            code=ancestor.code,
            parent_id=str(ancestor.parent_id) if ancestor.parent_id else None,
            level=ancestor.level,
            status=ancestor.status,
        ))

    # 添加当前部门
    path.append(DeptSchemaSimple(
        id=str(dept.id),
        name=dept.name,
        code=dept.code,
        parent_id=str(dept.parent_id) if dept.parent_id else None,
        level=dept.level,
        status=dept.status,
    ))

    return DeptPathOut(
        dept_id=str(dept.id),
        dept_name=dept.name,
        path=path
    )


@router.post("/dept/batch/update-status", response=DeptBatchUpdateStatusOut, summary="批量更新部门状态")
def batch_update_dept_status(request, data: DeptBatchUpdateStatusIn):
    """
    批量启用或禁用部门
    
    改进点：
    - 禁用部门时同时禁用所有子部门
    """
    count = 0
    for dept_id in data.ids:
        try:
            dept = Dept.objects.get(id=dept_id)
            dept.status = data.status
            dept.save()
            count += 1

            # 如果禁用，同时禁用所有子部门
            if not data.status:
                for descendant in dept.get_descendants():
                    descendant.status = False
                    descendant.save()
                    count += 1
        except Dept.DoesNotExist:
            continue

    remove_dept_cache()
    return DeptBatchUpdateStatusOut(count=count)


@router.get("/dept/users/{dept_id}", response=List[DeptUserSchema], summary="获取部门用户列表")
@paginate(MyPagination)
def get_dept_users(request, dept_id: str, include_children: bool = Query(False)):
    """
    获取部门下的用户列表
    
    改进点：
    - 支持包含子部门用户的选项
    """
    dept = get_object_or_404(Dept, id=dept_id)

    if include_children:
        # 获取部门及其所有子部门的用户
        dept_ids = [dept.id] + [d.id for d in dept.get_descendants()]
        from core.user.user_model import User
        users = User.objects.filter(dept_id__in=dept_ids, user_status=1)
    else:
        # 只获取当前部门的用户
        users = dept.core_users.filter(user_status=1)

    return users


@router.delete("/dept/users/{dept_id}", summary="从部门中移除用户")
def remove_user_from_dept(request, dept_id: str, data: DeptUserIn):
    """
    从部门中移除用户（支持批量删除）
    
    改进点：
    - 批量删除用户
    - 添加验证
    """
    dept = get_object_or_404(Dept, id=dept_id)

    # 优先使用 user_ids（批量），如果没有则使用 user_id（单个）
    user_ids_to_remove = data.user_ids if data.user_ids else ([data.user_id] if data.user_id else [])

    if not user_ids_to_remove:
        raise HttpError(400, "用户ID不能为空")

    from core.user.user_model import User

    removed_count = 0
    for user_id in user_ids_to_remove:
        user = get_object_or_404(User, id=user_id)

        # 检查用户所属部门是否是该部门
        if user.dept_id == dept.id:
            # 将用户的 dept_id 设置为 null（或其他处理方式）
            user.dept_id = None
            user.save()
            removed_count += 1

    return response_success(f"成功移除 {removed_count} 个用户")


@router.post("/dept/users/{dept_id}", summary="为部门添加用户")
def add_user_to_dept(request, dept_id: str, data: DeptUserIn):
    """
    将用户添加到部门
    
    改进点：
    - 批量添加用户
    - 检查用户是否已属于该部门
    """
    dept = get_object_or_404(Dept, id=dept_id)

    if not data.user_ids:
        raise HttpError(400, "用户ID列表不能为空")

    from core.user.user_model import User

    added_count = 0
    for user_id in data.user_ids:
        user = get_object_or_404(User, id=user_id)

        # 检查用户是否已属于该部门
        if user.dept_id == dept.id:
            continue

        user.dept_id = dept.id
        user.save()
        added_count += 1

    return response_success(f"成功添加 {added_count} 个用户")


@router.get("/dept/stats", summary="获取部门统计信息")
def get_dept_stats(request):
    """
    获取部门统计信息
    
    改进点：
    - 提供全局统计数据
    """
    total_count = Dept.objects.count()
    active_count = Dept.objects.filter(status=True).count()
    root_count = Dept.objects.filter(parent__isnull=True).count()

    # 按类型统计
    type_stats = {}
    for type_code, type_name in Dept.DEPT_TYPE_CHOICES:
        count = Dept.objects.filter(dept_type=type_code).count()
        type_stats[type_name] = count

    return {
        'total_count': total_count,
        'active_count': active_count,
        'inactive_count': total_count - active_count,
        'root_count': root_count,
        'type_stats': type_stats,
        'max_level': Dept.objects.aggregate(max_level=models.Max('level'))['max_level'] or 0,
    }


@router.post("/dept/move", summary="移动部门")
def move_dept(request, dept_id: str, new_parent_id: str = None):
    """
    移动部门到新的父部门下
    
    改进点：
    - 支持移动到根节点
    - 自动更新层级和路径
    """
    dept = get_object_or_404(Dept, id=dept_id)

    # 检查新父部门
    if new_parent_id and new_parent_id != "null":
        new_parent = get_object_or_404(Dept, id=new_parent_id)

        # 防止循环引用
        if dept in new_parent.get_ancestors() or dept.id == new_parent.id:
            raise HttpError(400, "不能移动到自己或子部门下")

        dept.parent = new_parent
    else:
        dept.parent = None

    dept.save()
    remove_dept_cache()

    return response_success("移动成功")
