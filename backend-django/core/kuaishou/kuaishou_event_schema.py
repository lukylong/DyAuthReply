#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""快手运行时事件 Schema"""
from datetime import datetime
from typing import List, Optional

from ninja import Field, ModelSchema, Schema

from common.fu_schema import FuFilters
from core.kuaishou.kuaishou_event_model import KuaishouEvent


class KuaishouEventFilters(FuFilters):
    account_id: Optional[str] = Field(None, q="account_id", alias="account_id")
    event_type: Optional[str] = Field(None, q="event_type", alias="event_type")
    level: Optional[str] = Field(None, q="level", alias="level")
    is_read: Optional[bool] = Field(None, q="is_read", alias="is_read")


class KuaishouEventSchemaOut(ModelSchema):
    level_display: Optional[str] = None
    event_type_display: Optional[str] = None
    account_id: Optional[str] = None

    class Config:
        model = KuaishouEvent
        model_fields = "__all__"

    @staticmethod
    def resolve_level_display(obj):
        return obj.get_level_display()

    @staticmethod
    def resolve_event_type_display(obj):
        return obj.get_event_type_display()

    @staticmethod
    def resolve_account_id(obj):
        return str(obj.account_id) if obj.account_id else None


class KuaishouEventReportIn(Schema):
    """worker 上报运行时事件"""
    account_id: Optional[str] = None
    session_id: Optional[str] = None
    event_type: str
    level: str = 'info'
    title: str
    detail: str = ''
    payload: dict = Field(default_factory=dict)
    worker_id: Optional[str] = None
    occurred_at: Optional[datetime] = None


class KuaishouEventMarkReadIn(Schema):
    ids: List[str]
