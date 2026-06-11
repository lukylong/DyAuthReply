#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""快手并发会话 Schema"""
from typing import Optional

from ninja import Field, ModelSchema, Schema

from common.fu_schema import FuFilters
from core.kuaishou.kuaishou_session_model import KuaishouSession


class KuaishouSessionFilters(FuFilters):
    status: Optional[str] = Field(None, q="status", alias="status")
    worker_id: Optional[str] = Field(None, q="worker_id", alias="worker_id")
    account_id: Optional[str] = Field(None, q="account_id", alias="account_id")


class KuaishouSessionSchemaOut(ModelSchema):
    status_display: Optional[str] = None
    account_id: Optional[str] = None
    account_nickname: Optional[str] = None

    class Config:
        model = KuaishouSession
        model_fields = "__all__"

    @staticmethod
    def resolve_status_display(obj):
        return obj.get_status_display()

    @staticmethod
    def resolve_account_id(obj):
        return str(obj.account_id) if obj.account_id else None

    @staticmethod
    def resolve_account_nickname(obj):
        try:
            return obj.account.nickname if obj.account_id else None
        except Exception:
            return None


class KuaishouSessionHeartbeatIn(Schema):
    """worker 上报心跳 + 运行指标"""
    account_id: str
    worker_id: str
    context_id: str = ''
    status: str = 'running'
    messages_today: int = 0
    replies_today: int = 0
    errors_today: int = 0
    cpu_percent: float = 0
    memory_mb: float = 0
    proxy_url: Optional[str] = None
    error_message: Optional[str] = None


class KuaishouSessionControlIn(Schema):
    """后台下发会话控制指令"""
    action: str = Field(..., description="pause/resume/stop/restart")
