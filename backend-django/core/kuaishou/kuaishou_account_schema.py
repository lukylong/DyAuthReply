#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""快手账号 Schema"""
from typing import List, Optional

from ninja import Field, ModelSchema, Schema

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.kuaishou.kuaishou_account_model import KuaishouAccount

_RUNTIME_FIELDS = (
    'status', 'cookie_state_path', 'last_heartbeat', 'last_login_at',
    'last_history_sync_at', 'pending_verification_type', 'pending_verification_at',
    'pending_verification_until', 'reply_today',
)


class KuaishouAccountFilters(FuFilters):
    nickname: Optional[str] = Field(None, q="nickname__icontains", alias="nickname")
    status: Optional[int] = Field(None, q="status", alias="status")
    work_mode: Optional[str] = Field(None, q="work_mode", alias="work_mode")
    group_id: Optional[str] = Field(None, q="group_id", alias="group_id")
    owner_id: Optional[str] = Field(None, q="owner_id", alias="owner_id")


class KuaishouAccountSchemaIn(ModelSchema):
    group_id: Optional[str] = None

    class Config:
        model = KuaishouAccount
        model_exclude = (*exclude_fields, 'owner', 'group', *_RUNTIME_FIELDS)


class KuaishouAccountSchemaPatch(Schema):
    nickname: Optional[str] = None
    avatar: Optional[str] = None
    group_id: Optional[str] = None
    tags: Optional[List[str]] = None
    work_mode: Optional[str] = None
    priority: Optional[int] = None
    daily_reply_quota: Optional[int] = None
    auto_reply_enabled: Optional[bool] = None
    min_interval_seconds: Optional[int] = None
    max_interval_seconds: Optional[int] = None
    proxy_url: Optional[str] = None
    user_agent: Optional[str] = None
    remark: Optional[str] = None


class KuaishouAccountSchemaOut(ModelSchema):
    status_display: Optional[str] = None
    work_mode_display: Optional[str] = None
    group_id: Optional[str] = None
    group_name: Optional[str] = None

    class Config:
        model = KuaishouAccount
        model_fields = "__all__"

    @staticmethod
    def resolve_status_display(obj):
        return obj.get_status_display()

    @staticmethod
    def resolve_work_mode_display(obj):
        return obj.get_work_mode_display()

    @staticmethod
    def resolve_group_id(obj):
        return str(obj.group_id) if obj.group_id else None

    @staticmethod
    def resolve_group_name(obj):
        try:
            return obj.group.name if obj.group_id else None
        except Exception:
            return None


class KuaishouAccountSimpleOut(Schema):
    id: str
    nickname: str
    status: int


class KuaishouAccountBatchDeleteIn(Schema):
    ids: List[str]
