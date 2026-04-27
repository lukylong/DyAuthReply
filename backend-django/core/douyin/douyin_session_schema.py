#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音并发会话 Schema"""
from datetime import datetime
from typing import List, Optional

from ninja import Field, ModelSchema, Schema

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.douyin.douyin_session_model import DouyinSession


class DouyinSessionFilters(FuFilters):
    account_id: Optional[str] = Field(None, q="account_id", alias="account_id")
    worker_id: Optional[str] = Field(None, q="worker_id__icontains", alias="worker_id")
    status: Optional[str] = Field(None, q="status", alias="status")


class DouyinSessionSchemaOut(ModelSchema):
    account_id: str
    account_nickname: Optional[str] = None
    is_alive: bool = False

    class Config:
        model = DouyinSession
        model_fields = "__all__"

    @staticmethod
    def resolve_account_id(obj):
        return str(obj.account_id)

    @staticmethod
    def resolve_account_nickname(obj):
        try:
            return obj.account.nickname if obj.account else None
        except Exception:
            return None

    @staticmethod
    def resolve_is_alive(obj):
        return obj.is_alive()


class DouyinSessionHeartbeatIn(Schema):
    """worker 定期上报心跳 + 指标"""
    account_id: str
    worker_id: str
    context_id: str
    status: str = Field(..., description="idle/running/paused/error/stopped")
    messages_today: int = 0
    replies_today: int = 0
    errors_today: int = 0
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    proxy_url: Optional[str] = None
    last_message_at: Optional[datetime] = None
    error_message: Optional[str] = None


class DouyinSessionControlIn(Schema):
    """后台对会话的控制指令（暂停/恢复/停止）"""
    action: str = Field(..., description="pause | resume | stop | restart")


class DouyinSessionControlOut(Schema):
    success: bool
    message: str


class DouyinSessionBatchIdsIn(Schema):
    ids: List[str]


class DouyinConversationItemOut(Schema):
    id: str
    peer_sec_uid: str
    peer_nickname: Optional[str] = None
    peer_avatar: Optional[str] = None
    last_message_at: Optional[datetime] = None
    last_message_preview: Optional[str] = None
    unread_count: int = 0


class DouyinMessageItemOut(Schema):
    id: str
    direction: str
    content_type: str
    content: str
    received_at: Optional[datetime] = None
    processed: bool = False


class DouyinManualReplyIn(Schema):
    conversation_id: str
    text: str = Field(..., description="手动回复内容")


class DouyinAutoReplyTestIn(Schema):
    conversation_id: str
    text: str = Field(..., description="模拟用户输入，用于触发自动回复规则测试")
