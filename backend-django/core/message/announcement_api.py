#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: announcement_api.py
@Desc: 公告 API
"""
"""
公告 API
"""
from typing import List

from ninja import Router, Query
from ninja.errors import HttpError
from ninja.pagination import paginate

from common.fu_pagination import MyPagination
from .announcement_schema import (
    AnnouncementCreate,
    AnnouncementUpdate,
    AnnouncementOut,
    AnnouncementListOut,
    UserAnnouncementOut,
    ReadStatsOut,
)
from .announcement_service import AnnouncementService

router = Router(tags=['公告管理'])


# ============ 管理端 API ============

@router.get('/admin/list', response=List[AnnouncementListOut], summary='公告列表（管理）')
@paginate(MyPagination)
def list_announcements(
        request,
        status: str = Query(None, description='状态: draft/published/expired'),
        keyword: str = Query(None, description='关键词搜索'),
):
    """获取公告列表（管理端）"""
    items, total = AnnouncementService.get_list(
        page=getattr(request, 'page', 1),
        page_size=getattr(request, 'page_size', 20),
        status=status,
        keyword=keyword,
    )

    return [_build_list_out(item) for item in items]


@router.get('/admin/{announcement_id}', response=AnnouncementOut, summary='公告详情（管理）')
def get_announcement(request, announcement_id: str):
    """获取公告详情"""
    announcement = AnnouncementService.get_by_id(announcement_id)
    if not announcement:
        raise HttpError(404, "公告不存在")

    return _build_out(announcement)


@router.post('/admin', response=AnnouncementOut, summary='创建公告')
def create_announcement(request, data: AnnouncementCreate):
    """创建公告"""
    user = request.auth
    announcement = AnnouncementService.create(data.dict(), user)
    return _build_out(announcement)


@router.put('/admin/{announcement_id}', response=AnnouncementOut, summary='更新公告')
def update_announcement(request, announcement_id: str, data: AnnouncementUpdate):
    """更新公告"""
    user = request.auth
    announcement = AnnouncementService.update(
        announcement_id,
        data.dict(exclude_unset=True),
        user
    )
    if not announcement:
        raise HttpError(404, "公告不存在")

    return _build_out(announcement)


@router.delete('/admin/{announcement_id}', summary='删除公告')
def delete_announcement(request, announcement_id: str):
    """删除公告"""
    user = request.auth
    success = AnnouncementService.delete(announcement_id, user)
    if not success:
        raise HttpError(404, "公告不存在")

    return {'message': '删除成功'}


@router.post('/admin/{announcement_id}/publish', response=AnnouncementOut, summary='发布公告')
def publish_announcement(request, announcement_id: str):
    """发布公告"""
    user = request.auth
    try:
        announcement = AnnouncementService.publish(announcement_id, user)
        if not announcement:
            raise HttpError(404, "公告不存在")
        return _build_out(announcement)
    except ValueError as e:
        raise HttpError(400, str(e))


@router.get('/admin/{announcement_id}/stats', response=ReadStatsOut, summary='阅读统计')
def get_read_stats(request, announcement_id: str):
    """获取公告阅读统计"""
    stats = AnnouncementService.get_read_stats(announcement_id)
    if not stats:
        raise HttpError(404, "公告不存在")

    return stats


# ============ 用户端 API ============

@router.get('/user/list', response=List[UserAnnouncementOut], summary='我的公告')
@paginate(MyPagination)
def list_user_announcements(
        request,
        unread_only: bool = Query(False, description='只看未读'),
):
    """获取当前用户可见的公告列表"""
    user = request.auth
    items, total = AnnouncementService.get_user_announcements(
        user=user,
        page=getattr(request, 'page', 1),
        page_size=getattr(request, 'page_size', 20),
        unread_only=unread_only,
    )

    return [_build_user_out(item) for item in items]


@router.get('/user/unread-count', summary='未读公告数量')
def get_unread_count(request):
    """获取未读公告数量"""
    user = request.auth
    count = AnnouncementService.get_unread_count(user)
    return {'count': count}


@router.get('/user/{announcement_id}', response=UserAnnouncementOut, summary='公告详情')
def get_user_announcement(request, announcement_id: str):
    """获取公告详情并标记已读"""
    user = request.auth
    announcement = AnnouncementService.get_by_id(announcement_id)
    if not announcement:
        raise HttpError(404, "公告不存在")

    # 标记已读
    AnnouncementService.mark_as_read(announcement_id, user)

    # 检查是否已读
    from .announcement_model import AnnouncementRead
    is_read = AnnouncementRead.objects.filter(
        announcement=announcement,
        user=user
    ).exists()
    announcement.is_read = is_read

    return _build_user_out(announcement)


@router.post('/user/{announcement_id}/read', summary='标记已读')
def mark_as_read(request, announcement_id: str):
    """标记公告为已读"""
    user = request.auth
    success = AnnouncementService.mark_as_read(announcement_id, user)
    if not success:
        raise HttpError(404, "公告不存在")

    return {'message': '已标记为已读'}


# ============ 辅助函数 ============

def _build_out(announcement) -> dict:
    """构建公告输出"""
    return {
        'id': str(announcement.id),
        'title': announcement.title,
        'content': announcement.content,
        'summary': announcement.summary or '',
        'status': announcement.status,
        'priority': announcement.priority,
        'is_top': announcement.is_top,
        'target_type': announcement.target_type,
        'target_ids': announcement.target_ids or [],
        'publish_time': announcement.publish_time,
        'expire_time': announcement.expire_time,
        'publisher_id': str(announcement.publisher_id) if announcement.publisher_id else None,
        'publisher_name': announcement.publisher.name if announcement.publisher else '',
        'read_count': announcement.read_count,
        'created_at': announcement.sys_create_datetime,
    }


def _build_list_out(announcement) -> dict:
    """构建公告列表输出"""
    return {
        'id': str(announcement.id),
        'title': announcement.title,
        'summary': announcement.summary or '',
        'status': announcement.status,
        'priority': announcement.priority,
        'is_top': announcement.is_top,
        'target_type': announcement.target_type,
        'publisher_name': announcement.publisher.name if announcement.publisher else '',
        'read_count': announcement.read_count,
        'publish_time': announcement.publish_time,
        'created_at': announcement.sys_create_datetime,
    }


def _build_user_out(announcement) -> dict:
    """构建用户公告输出"""
    return {
        'id': str(announcement.id),
        'title': announcement.title,
        'summary': announcement.summary or '',
        'content': announcement.content,
        'priority': announcement.priority,
        'is_top': announcement.is_top,
        'is_read': getattr(announcement, 'is_read', False),
        'publisher_name': announcement.publisher.name if announcement.publisher else '',
        'publish_time': announcement.publish_time,
    }
