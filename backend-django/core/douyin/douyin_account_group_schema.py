#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音账号分组 Schema"""
from typing import List, Optional

from ninja import Field, ModelSchema, Schema

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.douyin.douyin_account_group_model import DouyinAccountGroup


class DouyinAccountGroupFilters(FuFilters):
    name: Optional[str] = Field(None, q="name__icontains", alias="name")
    status: Optional[bool] = Field(None, q="status", alias="status")


class DouyinAccountGroupSchemaIn(ModelSchema):
    owner_id: Optional[str] = Field(None, description="负责人ID")

    class Config:
        model = DouyinAccountGroup
        model_exclude = (*exclude_fields, 'owner')


class DouyinAccountGroupSchemaOut(ModelSchema):
    owner_id: Optional[str] = None
    owner_name: Optional[str] = None
    account_count: int = 0

    class Config:
        model = DouyinAccountGroup
        model_fields = "__all__"

    @staticmethod
    def resolve_owner_id(obj):
        return str(obj.owner_id) if obj.owner_id else None

    @staticmethod
    def resolve_owner_name(obj):
        try:
            return (obj.owner.name or obj.owner.username) if obj.owner else None
        except Exception:
            return None

    @staticmethod
    def resolve_account_count(obj):
        return obj.accounts.filter(is_deleted=False).count() if hasattr(obj, 'accounts') else 0


class DouyinAccountGroupSimpleOut(Schema):
    id: str
    name: str
    color: str


class DouyinAccountGroupBatchDeleteIn(Schema):
    ids: List[str]
