# -*- coding: utf-8 -*-
"""客户端公告 Schema"""
from datetime import datetime
from typing import Optional
from ninja import Schema


class ClientAnnouncementCreateIn(Schema):
    """创建客户端公告"""
    title: str
    content: str
    level: str = 'info'  # info | warning | urgent
    status: str = 'draft'  # draft | published | revoked
    publish_time: Optional[datetime] = None
    expire_time: Optional[datetime] = None
    target_version: Optional[str] = None


class ClientAnnouncementUpdateIn(Schema):
    """更新客户端公告"""
    title: Optional[str] = None
    content: Optional[str] = None
    level: Optional[str] = None
    status: Optional[str] = None
    publish_time: Optional[datetime] = None
    expire_time: Optional[datetime] = None
    target_version: Optional[str] = None


class ClientAnnouncementOut(Schema):
    """客户端公告输出"""
    id: str
    title: str
    content: str
    level: str
    status: str
    publish_time: Optional[datetime]
    expire_time: Optional[datetime]
    target_version: Optional[str]
    sys_create_datetime: datetime
    sys_update_datetime: datetime


class ClientAnnouncementListOut(Schema):
    """客户端公告列表输出"""
    items: list[ClientAnnouncementOut]
    total: int


class ClientAnnouncementClientOut(Schema):
    """客户端获取公告输出（精简版）"""
    id: str
    title: str
    content: str
    level: str
    publish_time: Optional[datetime]
    expire_time: Optional[datetime]
