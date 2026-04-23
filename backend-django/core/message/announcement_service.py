#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: announcement_service.py
@Desc: 公告服务
"""
"""
公告服务
"""
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from django.db import connection, transaction
from django.db.models import Q

from .announcement_model import Announcement, AnnouncementRead
from .message_service import NotifyService

logger = logging.getLogger(__name__)


class AnnouncementService:
    """公告管理服务"""

    @staticmethod
    def _build_user_target_context(user) -> tuple[set[str], set[str], str]:
        """构建用户接收范围上下文"""
        user_dept_ids: set[str] = set()
        user_role_ids: set[str] = set()
        user_id = str(user.id)

        # user.dept 是单个 Dept 对象（ForeignKey），不是 QuerySet
        # 使用 try-except 处理 dept 被删除但 dept_id 仍存在的情况
        try:
            if hasattr(user, 'dept') and user.dept:
                user_dept_ids = {str(user.dept.id)}
        except Exception:
            pass

        # 兼容不同用户模型字段命名：core_roles（当前）/ role（历史）
        try:
            if hasattr(user, 'core_roles'):
                user_role_ids = {
                    str(role_id)
                    for role_id in user.core_roles.values_list('id', flat=True)
                }
            elif hasattr(user, 'role'):
                user_role_ids = {
                    str(role_id)
                    for role_id in user.role.values_list('id', flat=True)
                }
        except Exception:
            pass

        return user_dept_ids, user_role_ids, user_id

    @staticmethod
    def _normalize_target_ids(target_ids) -> set[str]:
        """规范化公告目标ID列表"""
        if not target_ids:
            return set()
        return {str(target_id) for target_id in target_ids if target_id is not None}

    @staticmethod
    def _is_announcement_visible_for_user(
            announcement: Announcement,
            user_dept_ids: set[str],
            user_role_ids: set[str],
            user_id: str,
    ) -> bool:
        """判断公告是否对用户可见（Python 侧过滤，兼容 SQLite）"""
        if announcement.target_type == 'all':
            return True

        target_ids = AnnouncementService._normalize_target_ids(announcement.target_ids)
        if not target_ids:
            return False

        if announcement.target_type == 'dept':
            return bool(target_ids & user_dept_ids)
        if announcement.target_type == 'role':
            return bool(target_ids & user_role_ids)
        if announcement.target_type == 'user':
            return user_id in target_ids
        return False

    @staticmethod
    def _get_visible_queryset(now):
        """获取基础可见公告 QuerySet（不含接收范围过滤）"""
        return Announcement.objects.filter(
            status='published',
            is_deleted=False,
        ).filter(
            Q(expire_time__isnull=True) | Q(expire_time__gt=now)
        )

    @staticmethod
    def get_list(
            page: int = 1,
            page_size: int = 20,
            status: str = None,
            keyword: str = None,
    ) -> tuple:
        """获取公告列表（管理端）"""
        queryset = Announcement.objects.filter(is_deleted=False)

        if status:
            queryset = queryset.filter(status=status)
        if keyword:
            queryset = queryset.filter(
                Q(title__icontains=keyword) | Q(content__icontains=keyword)
            )

        total = queryset.count()
        offset = (page - 1) * page_size
        items = list(queryset.select_related('publisher')[offset:offset + page_size])

        return items, total

    @staticmethod
    def get_user_announcements(
            user,
            page: int = 1,
            page_size: int = 20,
            unread_only: bool = False,
    ) -> tuple:
        """获取用户可见的公告列表"""
        now = datetime.now()

        user_dept_ids, user_role_ids, user_id = AnnouncementService._build_user_target_context(user)
        queryset = AnnouncementService._get_visible_queryset(now)
        offset = (page - 1) * page_size

        # SQLite 不支持 JSONField contains，改为 Python 侧过滤
        if connection.vendor == 'sqlite':
            all_items = list(queryset.select_related('publisher'))
            visible_items = [
                item for item in all_items
                if AnnouncementService._is_announcement_visible_for_user(
                    item, user_dept_ids, user_role_ids, user_id
                )
            ]

            if unread_only:
                read_ids = set(AnnouncementRead.objects.filter(
                    user=user
                ).values_list('announcement_id', flat=True))
                visible_items = [
                    item for item in visible_items
                    if item.id not in read_ids
                ]

            total = len(visible_items)
            items = visible_items[offset:offset + page_size]
        else:
            queryset = queryset.filter(
                Q(target_type='all') |
                Q(target_type='dept', target_ids__contains=list(user_dept_ids)) |
                Q(target_type='role', target_ids__contains=list(user_role_ids)) |
                Q(target_type='user', target_ids__contains=[user_id])
            )

            if unread_only:
                read_ids = AnnouncementRead.objects.filter(
                    user=user
                ).values_list('announcement_id', flat=True)
                queryset = queryset.exclude(id__in=read_ids)

            total = queryset.count()
            items = list(queryset.select_related('publisher')[offset:offset + page_size])

        # 标记已读状态
        if items:
            read_ids = set(AnnouncementRead.objects.filter(
                user=user,
                announcement_id__in=[a.id for a in items]
            ).values_list('announcement_id', flat=True))

            for item in items:
                item.is_read = item.id in read_ids

        return items, total

    @staticmethod
    def get_by_id(announcement_id: str) -> Optional[Announcement]:
        """获取公告详情"""
        try:
            return Announcement.objects.select_related('publisher').get(
                id=announcement_id,
                is_deleted=False,
            )
        except Announcement.DoesNotExist:
            return None

    @staticmethod
    @transaction.atomic
    def create(data: Dict[str, Any], user) -> Announcement:
        """创建公告"""
        announcement = Announcement.objects.create(
            title=data['title'],
            content=data['content'],
            summary=data.get('summary', ''),
            status=data.get('status', 'draft'),
            priority=data.get('priority', 0),
            is_top=data.get('is_top', False),
            target_type=data.get('target_type', 'all'),
            target_ids=data.get('target_ids', []),
            publish_time=data.get('publish_time'),
            expire_time=data.get('expire_time'),
            publisher=user,
            sys_creator=user,
            sys_modifier=user,
        )

        # 如果直接发布，发送通知
        if announcement.status == 'published':
            AnnouncementService._send_announcement_notification(announcement)

        return announcement

    @staticmethod
    @transaction.atomic
    def update(announcement_id: str, data: Dict[str, Any], user) -> Optional[Announcement]:
        """更新公告"""
        announcement = AnnouncementService.get_by_id(announcement_id)
        if not announcement:
            return None

        old_status = announcement.status

        # 更新字段
        for field in ['title', 'content', 'summary', 'status', 'priority',
                      'is_top', 'target_type', 'target_ids', 'publish_time', 'expire_time']:
            if field in data:
                setattr(announcement, field, data[field])

        announcement.sys_modifier = user
        announcement.save()

        # 如果从草稿变为发布，发送通知
        if old_status == 'draft' and announcement.status == 'published':
            AnnouncementService._send_announcement_notification(announcement)

        return announcement

    @staticmethod
    def delete(announcement_id: str, user) -> bool:
        """删除公告（软删除）"""
        announcement = AnnouncementService.get_by_id(announcement_id)
        if not announcement:
            return False

        announcement.is_deleted = True
        announcement.sys_modifier = user
        announcement.save(update_fields=['is_deleted', 'sys_modifier', 'sys_update_datetime'])
        return True

    @staticmethod
    @transaction.atomic
    def publish(announcement_id: str, user) -> Optional[Announcement]:
        """发布公告"""
        announcement = AnnouncementService.get_by_id(announcement_id)
        if not announcement:
            return None

        if announcement.status != 'draft':
            raise ValueError("只能发布草稿状态的公告")

        announcement.status = 'published'
        announcement.publish_time = datetime.now()
        announcement.publisher = user
        announcement.sys_modifier = user
        announcement.save()

        # 发送通知
        AnnouncementService._send_announcement_notification(announcement)

        return announcement

    @staticmethod
    def mark_as_read(announcement_id: str, user) -> bool:
        """标记公告为已读"""
        announcement = AnnouncementService.get_by_id(announcement_id)
        if not announcement:
            return False

        # 创建阅读记录
        _, created = AnnouncementRead.objects.get_or_create(
            announcement=announcement,
            user=user,
            defaults={'sys_creator': user, 'sys_modifier': user}
        )

        if created:
            # 更新阅读计数
            from django.db.models import F
            Announcement.objects.filter(id=announcement_id).update(
                read_count=F('read_count') + 1
            )

        return True

    @staticmethod
    def get_unread_count(user) -> int:
        """获取未读公告数量"""
        now = datetime.now()
        user_dept_ids, user_role_ids, user_id = AnnouncementService._build_user_target_context(user)
        queryset = AnnouncementService._get_visible_queryset(now)
        read_ids = set(AnnouncementRead.objects.filter(
            user=user
        ).values_list('announcement_id', flat=True))

        if connection.vendor == 'sqlite':
            visible_ids = [
                item.id
                for item in queryset
                if AnnouncementService._is_announcement_visible_for_user(
                    item, user_dept_ids, user_role_ids, user_id
                )
            ]
            return sum(1 for item_id in visible_ids if item_id not in read_ids)

        queryset = queryset.filter(
            Q(target_type='all') |
            Q(target_type='dept', target_ids__contains=list(user_dept_ids)) |
            Q(target_type='role', target_ids__contains=list(user_role_ids)) |
            Q(target_type='user', target_ids__contains=[user_id])
        )
        return queryset.exclude(id__in=read_ids).count()

    @staticmethod
    def get_read_stats(announcement_id: str) -> Dict[str, Any]:
        """获取公告阅读统计"""
        announcement = AnnouncementService.get_by_id(announcement_id)
        if not announcement:
            return {}

        reads = AnnouncementRead.objects.filter(
            announcement=announcement
        ).select_related('user').order_by('-read_at')

        return {
            'total_read': reads.count(),
            'readers': [
                {
                    'user_id': str(r.user_id),
                    'user_name': r.user.name or r.user.username,
                    'read_at': r.read_at.isoformat(),
                }
                for r in reads[:100]  # 最多返回100条
            ]
        }

    @staticmethod
    def _send_announcement_notification(announcement: Announcement):
        """发送公告通知"""
        from core.user.user_model import User

        # 获取接收用户
        recipient_ids = []

        if announcement.target_type == 'all':
            # 全员
            recipient_ids = list(User.objects.filter(
                is_deleted=False,
                is_active=True,
            ).values_list('id', flat=True))

        elif announcement.target_type == 'dept':
            # 指定部门
            dept_ids = announcement.target_ids or []
            recipient_ids = list(User.objects.filter(
                is_deleted=False,
                is_active=True,
                dept__id__in=dept_ids,
            ).values_list('id', flat=True))

        elif announcement.target_type == 'role':
            # 指定角色
            role_ids = announcement.target_ids or []
            recipient_ids = list(User.objects.filter(
                is_deleted=False,
                is_active=True,
                role__id__in=role_ids,
            ).values_list('id', flat=True))

        elif announcement.target_type == 'user':
            # 指定用户
            recipient_ids = announcement.target_ids or []

        if not recipient_ids:
            logger.warning(f"公告 {announcement.id} 没有接收用户")
            return

        # 发送站内消息
        recipient_ids = [str(uid) for uid in recipient_ids]

        NotifyService.send(
            recipient_ids=recipient_ids,
            title=f"【公告】{announcement.title}",
            content=announcement.summary or announcement.content[:200],
            channels=['site'],
            msg_type='announcement',
            link_type='announcement',
            link_id=str(announcement.id),
            sender_id=str(announcement.publisher_id) if announcement.publisher_id else None,
        )

        logger.info(f"公告通知已发送: {announcement.id}, 接收人数: {len(recipient_ids)}")
