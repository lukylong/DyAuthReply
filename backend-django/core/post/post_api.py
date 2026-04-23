#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: post_api.py
@Desc: Post API - 岗位管理接口 - 提供岗位的 CRUD 操作和用户管理
"""
"""
Post API - 岗位管理接口
提供岗位的 CRUD 操作和用户管理
"""
from typing import List

from django.db.models import Q
from django.shortcuts import get_object_or_404
from ninja import Router, Query
from ninja.errors import HttpError
from ninja.pagination import paginate

from common.fu_crud import create, retrieve, delete, update, export_data, import_data, ImportSchema
from common.fu_pagination import MyPagination
from common.fu_schema import response_success
from core.post.post_model import Post
from core.post.post_schema import (
    PostSchemaOut,
    PostSchemaIn,
    PostSchemaPatch,
    PostFilters,
    PostSchemaSimple,
    PostSchemaBatchDeleteIn,
    PostSchemaBatchDeleteOut,
    PostBatchUpdateStatusIn,
    PostBatchUpdateStatusOut,
    PostUserSchema,
    PostUserIn,
    PostUserFilter,
    PostStatsOut,
)

router = Router()


@router.post("/post", response=PostSchemaOut, summary="创建岗位")
def create_post(request, data: PostSchemaIn):
    """
    创建新岗位
    
    改进点：
    - 检查岗位编码唯一性
    - 验证薪资范围
    """
    # 检查岗位编码是否已存在
    if Post.objects.filter(code=data.code).exists():
        raise HttpError(400, f"岗位编码已存在: {data.code}")

    query_set = create(request, data, Post)
    return query_set


@router.delete("/post/{post_id}", response=PostSchemaOut, summary="删除岗位")
def delete_post(request, post_id: str):
    """
    删除岗位
    
    改进点：
    - 检查是否有用户使用该岗位
    """
    post = get_object_or_404(Post, id=post_id)

    if not post.can_delete():
        user_count = post.get_user_count()
        raise HttpError(400, f"该岗位下还有 {user_count} 个用户，无法删除")

    instance = delete(post_id, Post)
    return instance


@router.delete("/post/batch/delete", response=PostSchemaBatchDeleteOut, summary="批量删除岗位")
def delete_batch_post(request, data: PostSchemaBatchDeleteIn):
    """
    批量删除岗位
    
    改进点：
    - 跳过有用户的岗位
    - 返回删除失败的ID列表
    """
    failed_ids = []
    success_count = 0

    for post_id in data.ids:
        try:
            post = Post.objects.get(id=post_id)

            if not post.can_delete():
                failed_ids.append(post_id)
                continue

            post.delete()
            success_count += 1
        except Post.DoesNotExist:
            failed_ids.append(post_id)

    return PostSchemaBatchDeleteOut(count=success_count, failed_ids=failed_ids)


@router.put("/post/{post_id}", response=PostSchemaOut, summary="更新岗位（完全替换）")
def update_post(request, post_id: str, data: PostSchemaIn):
    """
    更新岗位信息（PUT - 完全替换）
    
    改进点：
    - 检查岗位编码唯一性（排除自身）
    - 验证薪资范围
    """
    # 检查岗位编码是否已存在（排除自身）
    if Post.objects.filter(code=data.code).exclude(id=post_id).exists():
        raise HttpError(400, f"岗位编码已存在: {data.code}")

    instance = update(request, post_id, data, Post)
    return instance


@router.patch("/post/{post_id}", response=PostSchemaOut, summary="部分更新岗位")
def patch_post(request, post_id: str, data: PostSchemaPatch):
    """
    部分更新岗位信息（PATCH - 只更新提供的字段）
    
    优势：
    - 只需提供需要修改的字段
    - 更灵活，适合前端表单部分更新
    - 减少网络传输数据量
    
    改进点：
    - 检查岗位编码唯一性（排除自身）
    """
    post = get_object_or_404(Post, id=post_id)

    # 只更新提供的字段
    update_data = data.dict(exclude_unset=True)

    # 检查岗位编码是否已存在（排除自身）
    if 'code' in update_data and update_data['code']:
        if Post.objects.filter(code=update_data['code']).exclude(id=post_id).exists():
            raise HttpError(400, f"岗位编码已存在: {update_data['code']}")

    # 更新字段
    for field, value in update_data.items():
        setattr(post, field, value)

    post.save()
    return post


@router.get("/post", response=List[PostSchemaOut], summary="获取岗位列表（分页）")
@paginate(MyPagination)
def list_post(request, filters: PostFilters = Query(...)):
    """
    获取岗位列表（分页）
    
    改进点：
    - 支持多种过滤条件
    - 预加载关联数据
    """
    query_set = retrieve(request, Post, filters)
    query_set = query_set.select_related('dept')
    return query_set


@router.get("/post/all", response=List[PostSchemaSimple], summary="获取所有岗位（简化版）")
def list_all_post(request):
    """
    获取所有启用的岗位（不分页，简化版）
    
    用于岗位选择器等场景
    """
    query_set = Post.objects.filter(status=True).order_by('post_level', 'name')
    return query_set


@router.get("/post/{post_id}", response=PostSchemaOut, summary="获取岗位详情")
def get_post(request, post_id: str):
    """获取单个岗位的详细信息"""
    post = get_object_or_404(
        Post.objects.select_related('dept'),
        id=post_id
    )
    return post


@router.get("/post/by/ids", response=List[PostSchemaOut], summary="根据ID列表获取岗位")
def get_posts_by_ids(request, ids: str):
    """
    根据岗位ID列表批量获取岗位信息
    
    参数:
        ids: 逗号分隔的岗位ID字符串，例如: "id1,id2,id3"
    
    返回:
        指定的岗位列表
    """
    if not ids:
        return []

    post_ids = [id.strip() for id in ids.split(',') if id.strip()]
    if not post_ids:
        return []

    posts = Post.objects.filter(id__in=post_ids).select_related('dept')
    return posts


@router.get("/post/by/dept/{dept_id}", response=List[PostSchemaSimple], summary="根据部门ID获取岗位")
def get_posts_by_dept(request, dept_id: str):
    """
    根据部门ID获取该部门的所有岗位
    
    改进点：
    - 支持部门维度的岗位查询
    """
    posts = Post.objects.filter(
        dept_id=dept_id,
        status=True
    ).order_by('post_level', 'name')
    return posts


@router.get("/post/search", response=List[PostSchemaOut], summary="搜索岗位")
@paginate(MyPagination)
def search_post(request, keyword: str = Query(None)):
    """
    搜索岗位
    
    改进点：
    - 支持关键词搜索（名称、编码、描述）
    """
    query_set = Post.objects.all()

    if keyword:
        query_set = query_set.filter(
            Q(name__icontains=keyword) |
            Q(code__icontains=keyword) |
            Q(description__icontains=keyword)
        )

    query_set = query_set.select_related('dept').order_by('-sys_create_datetime')
    return query_set


@router.post("/post/batch/update-status", response=PostBatchUpdateStatusOut, summary="批量更新岗位状态")
def batch_update_post_status(request, data: PostBatchUpdateStatusIn):
    """
    批量启用或禁用岗位
    
    改进点：
    - 支持批量状态管理
    """
    count = Post.objects.filter(id__in=data.ids).update(status=data.status)
    return PostBatchUpdateStatusOut(count=count)


@router.get("/post/users/by/post_id", response=List[PostUserSchema], summary="获取岗位的用户列表")
@paginate(MyPagination)
def get_users_by_post(request, filters: PostUserFilter = Query(...)):
    """
    获取指定岗位下的所有用户列表
    
    改进点：
    - 支持用户名称搜索
    """
    post = get_object_or_404(Post, id=filters.post_id)
    users = post.core_users.all()

    # 如果提供了名称过滤
    if filters.name:
        users = users.filter(
            Q(name__icontains=filters.name) |
            Q(username__icontains=filters.name)
        )

    return users


@router.delete("/post/users/by/post_id", summary="从岗位中移除用户")
def remove_user_from_post(request, data: PostUserIn):
    """
    从岗位中移除用户（支持批量删除）
    
    改进点：
    - 批量删除用户
    - 添加验证
    """
    post = get_object_or_404(Post, id=data.post_id)

    # 优先使用 user_ids（批量），如果没有则使用 user_id（单个）
    user_ids_to_remove = data.user_ids if data.user_ids else ([data.user_id] if data.user_id else [])

    if not user_ids_to_remove:
        raise HttpError(400, "用户ID不能为空")

    from core.user.user_model import User

    removed_count = 0
    for user_id in user_ids_to_remove:
        user = get_object_or_404(User, id=user_id)

        # 检查用户是否属于该岗位
        if post not in user.post.all():
            continue

        post.core_users.remove(user)
        removed_count += 1

    return response_success(f"成功移除 {removed_count} 个用户")


@router.post("/post/users/by/post_id", summary="为岗位添加用户")
def add_user_to_post(request, data: PostUserIn):
    """
    将用户添加到岗位
    
    改进点：
    - 批量添加用户
    - 检查用户是否已有该岗位
    """
    post = get_object_or_404(Post, id=data.post_id)

    if not data.user_ids:
        raise HttpError(400, "用户ID列表不能为空")

    from core.user.user_model import User

    added_count = 0
    for user_id in data.user_ids:
        user = get_object_or_404(User, id=user_id)

        # 检查用户是否已有该岗位
        if post in user.post.all():
            continue

        post.core_users.add(user)
        added_count += 1

    return response_success(f"成功添加 {added_count} 个用户")


@router.get("/post/stats", response=PostStatsOut, summary="获取岗位统计信息")
def get_post_stats(request):
    """
    获取岗位统计信息
    
    改进点：
    - 提供全局统计数据
    """
    total_count = Post.objects.count()
    active_count = Post.objects.filter(status=True).count()

    # 按类型统计
    type_stats = {}
    for type_code, type_name in Post.POST_TYPE_CHOICES:
        count = Post.objects.filter(post_type=type_code).count()
        type_stats[type_name] = count

    # 按级别统计
    level_stats = {}
    for level_code, level_name in Post.POST_LEVEL_CHOICES:
        count = Post.objects.filter(post_level=level_code).count()
        level_stats[level_name] = count

    return PostStatsOut(
        total_count=total_count,
        active_count=active_count,
        inactive_count=total_count - active_count,
        type_stats=type_stats,
        level_stats=level_stats,
    )


@router.get("/post/export", summary="导出岗位数据")
def export_post(request):
    """
    导出岗位数据为Excel
    
    用于数据备份和报表
    """
    export_fields = ["name", "code", "post_type", "post_level", "status", "sort"]
    return export_data(request, Post, PostSchemaOut, export_fields)


@router.post("/post/import", summary="导入岗位数据")
def import_post(request, data: ImportSchema):
    """
    批量导入岗位数据
    
    从Excel文件导入岗位数据
    """
    import_fields = ["name", "code", "post_type", "post_level", "sort"]
    return import_data(request, Post, PostSchemaIn, data, import_fields)


@router.get("/post/by/type/{post_type}", response=List[PostSchemaSimple], summary="根据类型获取岗位")
def get_posts_by_type(request, post_type: int):
    """
    根据岗位类型获取岗位列表
    
    改进点：
    - 支持岗位类型维度的查询
    """
    if post_type not in [0, 1, 2, 3, 4]:
        raise HttpError(400, "岗位类型必须在 0-4 之间")

    posts = Post.objects.filter(
        post_type=post_type,
        status=True
    ).order_by('post_level', 'name')
    return posts


@router.get("/post/by/level/{post_level}", response=List[PostSchemaSimple], summary="根据级别获取岗位")
def get_posts_by_level(request, post_level: int):
    """
    根据岗位级别获取岗位列表
    
    改进点：
    - 支持岗位级别维度的查询
    """
    if post_level not in [0, 1, 2, 3]:
        raise HttpError(400, "岗位级别必须在 0-3 之间")

    posts = Post.objects.filter(
        post_level=post_level,
        status=True
    ).order_by('name')
    return posts
