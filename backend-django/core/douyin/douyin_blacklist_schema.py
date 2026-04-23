#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音黑名单 Schema"""
from typing import List, Optional

from ninja import Field, ModelSchema, Schema

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.douyin.douyin_blacklist_model import DouyinBlacklist


class DouyinBlacklistFilters(FuFilters):
    blacklist_type: Optional[str] = Field(None, q="blacklist_type", alias="blacklist_type")
    scope: Optional[str] = Field(None, q="scope", alias="scope")
    value: Optional[str] = Field(None, q="value__icontains", alias="value")
    status: Optional[bool] = Field(None, q="status", alias="status")


class DouyinBlacklistSchemaIn(ModelSchema):
    account_id: Optional[str] = None
    group_id: Optional[str] = None

    class Config:
        model = DouyinBlacklist
        model_exclude = (*exclude_fields, 'account', 'group', 'hit_count')


class DouyinBlacklistSchemaOut(ModelSchema):
    account_id: Optional[str] = None
    group_id: Optional[str] = None

    class Config:
        model = DouyinBlacklist
        model_fields = "__all__"

    @staticmethod
    def resolve_account_id(obj):
        return str(obj.account_id) if obj.account_id else None

    @staticmethod
    def resolve_group_id(obj):
        return str(obj.group_id) if obj.group_id else None


class DouyinBlacklistBatchDeleteIn(Schema):
    ids: List[str]
