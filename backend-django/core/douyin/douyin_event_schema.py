#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音事件 Schema"""
from datetime import datetime
from typing import List, Optional

from ninja import Field, ModelSchema, Schema

from common.fu_schema import FuFilters
from core.douyin.douyin_event_model import DouyinEvent


class DouyinEventFilters(FuFilters):
    account_id: Optional[str] = Field(None, q="account_id", alias="account_id")
    event_type: Optional[str] = Field(None, q="event_type", alias="event_type")
    level: Optional[str] = Field(None, q="level", alias="level")
    is_read: Optional[bool] = Field(None, q="is_read", alias="is_read")
    start_at: Optional[datetime] = Field(None, q="occurred_at__gte", alias="start_at")
    end_at: Optional[datetime] = Field(None, q="occurred_at__lte", alias="end_at")


class DouyinEventSchemaOut(ModelSchema):
    account_id: Optional[str] = None
    account_nickname: Optional[str] = None

    class Config:
        model = DouyinEvent
        model_fields = "__all__"

    @staticmethod
    def resolve_account_id(obj):
        return str(obj.account_id) if obj.account_id else None

    @staticmethod
    def resolve_account_nickname(obj):
        try:
            return obj.account.nickname if obj.account else None
        except Exception:
            return None


class DouyinEventReportIn(Schema):
    """worker 上报事件"""
    account_id: Optional[str] = None
    event_type: str
    level: str = 'info'
    title: str
    detail: str = ''
    payload: dict = Field(default_factory=dict)
    worker_id: Optional[str] = None
    occurred_at: Optional[datetime] = None


class DouyinEventBatchReadIn(Schema):
    ids: List[str]


class DouyinEventActionOut(Schema):
    success: bool
    message: str
    count: int = 0
