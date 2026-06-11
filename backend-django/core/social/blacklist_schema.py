#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""跨平台黑名单 Schema"""
from typing import List, Optional

from ninja import Field, ModelSchema, Schema

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.social.blacklist_model import Blacklist


class BlacklistFilters(FuFilters):
    platform: Optional[str] = Field(None, q="platform", alias="platform")
    blacklist_type: Optional[str] = Field(None, q="blacklist_type", alias="blacklist_type")
    scope: Optional[str] = Field(None, q="scope", alias="scope")
    value: Optional[str] = Field(None, q="value__icontains", alias="value")
    status: Optional[bool] = Field(None, q="status", alias="status")


class BlacklistSchemaIn(ModelSchema):
    class Config:
        model = Blacklist
        model_exclude = (*exclude_fields, 'hit_count')


class BlacklistSchemaPatch(Schema):
    platform: Optional[str] = None
    blacklist_type: Optional[str] = None
    value: Optional[str] = None
    scope: Optional[str] = None
    account_id: Optional[str] = None
    group_id: Optional[str] = None
    reason: Optional[str] = None
    status: Optional[bool] = None


class BlacklistSchemaOut(ModelSchema):
    platform_display: Optional[str] = None
    blacklist_type_display: Optional[str] = None
    scope_display: Optional[str] = None

    class Config:
        model = Blacklist
        model_fields = "__all__"

    @staticmethod
    def resolve_platform_display(obj):
        return obj.get_platform_display() if obj.platform else None

    @staticmethod
    def resolve_blacklist_type_display(obj):
        return obj.get_blacklist_type_display()

    @staticmethod
    def resolve_scope_display(obj):
        return obj.get_scope_display()


class BlacklistBatchDeleteIn(Schema):
    ids: List[str]
