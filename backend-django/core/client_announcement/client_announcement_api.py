# -*- coding: utf-8 -*-
"""客户端公告 API"""
import logging
from datetime import datetime
from typing import List

from django.db import transaction
from django.shortcuts import get_object_or_404
from ninja import Router

from common.fu_schema import response_success
from core.client_announcement.announcement_push import push_announcement_to_clients
from core.client_announcement.client_announcement_model import ClientAnnouncement
from core.client_announcement.client_announcement_schema import (
    ClientAnnouncementCreateIn,
    ClientAnnouncementUpdateIn,
    ClientAnnouncementOut,
    ClientAnnouncementListOut,
    ClientAnnouncementClientOut,
)

logger = logging.getLogger(__name__)
router = Router()


@router.get("", response=ClientAnnouncementListOut, summary="管理后台 - 公告列表")
def list_announcements(
    request,
    page: int = 1,
    page_size: int = 10,
    status: str = None,
    level: str = None,
):
    """
    获取客户端公告列表（管理后台）
    """
    queryset = ClientAnnouncement.objects.filter(is_deleted=False)

    if status:
        queryset = queryset.filter(status=status)
    if level:
        queryset = queryset.filter(level=level)

    total = queryset.count()
    items = queryset[(page - 1) * page_size : page * page_size]

    return {
        "items": [ClientAnnouncementOut.from_orm(item) for item in items],
        "total": total,
    }


@router.post("", response=ClientAnnouncementOut, summary="管理后台 - 创建公告")
@transaction.atomic
def create_announcement(request, payload: ClientAnnouncementCreateIn):
    """
    创建客户端公告
    """
    announcement = ClientAnnouncement.objects.create(
        title=payload.title,
        content=payload.content,
        level=payload.level,
        status=payload.status,
        publish_time=payload.publish_time,
        expire_time=payload.expire_time,
        target_version=payload.target_version,
        sys_creator=request.auth,
        sys_modifier=request.auth,
    )

    logger.info(f"Created announcement {announcement.id}: {announcement.title}")
    return ClientAnnouncementOut.from_orm(announcement)


@router.put("/{announcement_id}", response=ClientAnnouncementOut, summary="管理后台 - 更新公告")
@transaction.atomic
def update_announcement(request, announcement_id: str, payload: ClientAnnouncementUpdateIn):
    """
    更新客户端公告
    """
    announcement = get_object_or_404(ClientAnnouncement, id=announcement_id, is_deleted=False)

    if payload.title is not None:
        announcement.title = payload.title
    if payload.content is not None:
        announcement.content = payload.content
    if payload.level is not None:
        announcement.level = payload.level
    if payload.status is not None:
        announcement.status = payload.status
    if payload.publish_time is not None:
        announcement.publish_time = payload.publish_time
    if payload.expire_time is not None:
        announcement.expire_time = payload.expire_time
    if payload.target_version is not None:
        announcement.target_version = payload.target_version

    announcement.sys_modifier = request.auth
    announcement.save()

    logger.info(f"Updated announcement {announcement.id}: {announcement.title}")
    return ClientAnnouncementOut.from_orm(announcement)


@router.delete("/{announcement_id}", summary="管理后台 - 删除公告")
@transaction.atomic
def delete_announcement(request, announcement_id: str):
    """
    删除客户端公告（软删除）
    """
    announcement = get_object_or_404(ClientAnnouncement, id=announcement_id, is_deleted=False)
    announcement.is_deleted = True
    announcement.sys_modifier = request.auth
    announcement.save()

    logger.info(f"Deleted announcement {announcement.id}: {announcement.title}")
    return response_success("删除成功")


@router.post("/{announcement_id}/publish", response=ClientAnnouncementOut, summary="管理后台 - 发布公告")
@transaction.atomic
def publish_announcement(request, announcement_id: str):
    """
    发布公告并通过 WebSocket 推送到客户端
    """
    announcement = get_object_or_404(ClientAnnouncement, id=announcement_id, is_deleted=False)

    # 更新状态为已发布
    announcement.status = 'published'
    if not announcement.publish_time:
        announcement.publish_time = datetime.now()
    announcement.sys_modifier = request.auth
    announcement.save()

    # 推送到客户端
    try:
        push_announcement_to_clients(announcement)
        logger.info(f"Published and pushed announcement {announcement.id}: {announcement.title}")
    except Exception as e:
        logger.error(f"Failed to push announcement {announcement.id}: {e}", exc_info=True)
        # 不影响发布操作，继续返回

    return ClientAnnouncementOut.from_orm(announcement)
