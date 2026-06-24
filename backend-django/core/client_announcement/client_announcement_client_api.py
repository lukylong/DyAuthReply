# -*- coding: utf-8 -*-
"""客户端公告 API - 客户端专用接口"""
import logging
from datetime import datetime

from django.db.models import Q
from ninja import Router

from core.client_announcement.client_announcement_model import ClientAnnouncement
from core.client_announcement.client_announcement_schema import ClientAnnouncementClientOut

logger = logging.getLogger(__name__)
router = Router()


@router.get("/announcements", response=list[ClientAnnouncementClientOut], auth=None, summary="客户端 - 获取最新公告")
def get_client_announcements(request, limit: int = 10):
    """
    客户端获取最新公告列表

    仅返回已发布且未过期的公告
    """
    now = datetime.now()

    queryset = ClientAnnouncement.objects.filter(
        is_deleted=False,
        status='published',
    ).filter(
        Q(expire_time__isnull=True) | Q(expire_time__gt=now)
    ).order_by('-publish_time')[:limit]

    return [ClientAnnouncementClientOut.from_orm(item) for item in queryset]
