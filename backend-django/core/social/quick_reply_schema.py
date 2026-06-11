#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""跨平台快捷回复 Schema"""
from typing import List, Optional

from ninja import Field, ModelSchema, Schema

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.social.quick_reply_model import QuickReply


class QuickReplyFilters(FuFilters):
    platform: Optional[str] = Field(None, q="platform", alias="platform")
    shortcut: Optional[str] = Field(None, q="shortcut__icontains", alias="shortcut")
    title: Optional[str] = Field(None, q="title__icontains", alias="title")
    category_id: Optional[str] = Field(None, q="category_id", alias="category_id")
    status: Optional[bool] = Field(None, q="status", alias="status")


class QuickReplySchemaIn(ModelSchema):
    category_id: Optional[str] = None
    owner_id: Optional[str] = None

    class Config:
        model = QuickReply
        model_exclude = (*exclude_fields, 'category', 'owner', 'use_count')


class QuickReplySchemaPatch(Schema):
    platform: Optional[str] = None
    shortcut: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    category_id: Optional[str] = None
    is_shared: Optional[bool] = None
    status: Optional[bool] = None


class QuickReplySchemaOut(ModelSchema):
    platform_display: Optional[str] = None
    category_id: Optional[str] = None
    category_name: Optional[str] = None
    owner_id: Optional[str] = None
    owner_name: Optional[str] = None

    class Config:
        model = QuickReply
        model_fields = "__all__"

    @staticmethod
    def resolve_platform_display(obj):
        return obj.get_platform_display() if obj.platform else "通用"

    @staticmethod
    def resolve_category_id(obj):
        return str(obj.category_id) if obj.category_id else None

    @staticmethod
    def resolve_category_name(obj):
        try:
            return obj.category.name if obj.category else None
        except Exception:
            return None

    @staticmethod
    def resolve_owner_id(obj):
        return str(obj.owner_id) if obj.owner_id else None

    @staticmethod
    def resolve_owner_name(obj):
        try:
            return (obj.owner.name or obj.owner.username) if obj.owner else None
        except Exception:
            return None


class QuickReplyBatchDeleteIn(Schema):
    ids: List[str]
