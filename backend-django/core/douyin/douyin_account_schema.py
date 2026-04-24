#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: douyin_account_schema.py
@Desc: Douyin Account Schema - 抖音账号数据验证模式
"""
from datetime import time as dtime
from typing import List, Optional

from ninja import Field, ModelSchema, Schema
from pydantic import field_validator

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.douyin.douyin_account_model import DouyinAccount


class DouyinAccountFilters(FuFilters):
    """抖音账号过滤器"""
    nickname: Optional[str] = Field(None, q="nickname__icontains", alias="nickname")
    status: Optional[int] = Field(None, q="status", alias="status")
    owner_id: Optional[str] = Field(None, q="owner_id", alias="owner_id")


class DouyinAccountSchemaIn(ModelSchema):
    """抖音账号输入模式（创建 / 完全更新）"""

    owner_id: Optional[str] = Field(None, description="所属用户ID，不传则默认为当前登录用户")

    class Config:
        model = DouyinAccount
        model_exclude = (
            *exclude_fields,
            'owner',
            'last_heartbeat',
            'last_login_at',
            'pending_verification_type',
            'pending_verification_at',
            'pending_verification_until',
            'storage_state_path',
        )

    @field_validator('daily_reply_quota', check_fields=False)
    @classmethod
    def validate_quota(cls, v):
        if v is not None and v < 0:
            raise ValueError('每日回复上限不能为负')
        return v

    @field_validator('min_interval_seconds', 'max_interval_seconds', check_fields=False)
    @classmethod
    def validate_interval(cls, v):
        if v is not None and v < 1:
            raise ValueError('回复间隔至少 1 秒')
        return v


class DouyinAccountSchemaPatch(Schema):
    """抖音账号部分更新模式"""
    nickname: Optional[str] = None
    status: Optional[int] = None
    daily_reply_quota: Optional[int] = None
    min_interval_seconds: Optional[int] = None
    max_interval_seconds: Optional[int] = None
    silent_start: Optional[dtime] = None
    silent_end: Optional[dtime] = None
    remark: Optional[str] = None


class DouyinAccountSchemaOut(ModelSchema):
    """抖音账号输出模式"""
    status_display: Optional[str] = None
    owner_id: Optional[str] = None
    owner_name: Optional[str] = None

    class Config:
        model = DouyinAccount
        model_fields = "__all__"

    @staticmethod
    def resolve_status_display(obj):
        return obj.get_status_display()

    @staticmethod
    def resolve_owner_id(obj):
        return str(obj.owner_id) if obj.owner_id else None

    @staticmethod
    def resolve_owner_name(obj):
        try:
            return obj.owner.name or obj.owner.username if obj.owner else None
        except Exception:
            return None


class DouyinAccountSimpleOut(Schema):
    """抖音账号简单输出（用于选择器）"""
    id: str
    nickname: str
    status: int
    status_display: str

    @staticmethod
    def resolve_status_display(obj):
        return obj.get_status_display()


class DouyinAccountBatchDeleteIn(Schema):
    """批量删除输入"""
    ids: List[str] = Field(..., description="抖音账号ID列表")


class DouyinAccountBatchDeleteOut(Schema):
    """批量删除输出"""
    count: int = Field(..., description="删除成功数")
    failed_ids: List[str] = Field(default=[], description="删除失败的ID")


class DouyinAccountActionOut(Schema):
    """账号动作（触发登录 / 登出）通用输出"""
    success: bool
    message: str
