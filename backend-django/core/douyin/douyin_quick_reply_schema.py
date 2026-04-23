#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音快捷回复 Schema"""
from typing import List, Optional

from ninja import Field, ModelSchema, Schema

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.douyin.douyin_quick_reply_model import DouyinQuickReply


class DouyinQuickReplyFilters(FuFilters):
    shortcut: Optional[str] = Field(None, q="shortcut__icontains", alias="shortcut")
    title: Optional[str] = Field(None, q="title__icontains", alias="title")
    category_id: Optional[str] = Field(None, q="category_id", alias="category_id")
    status: Optional[bool] = Field(None, q="status", alias="status")


class DouyinQuickReplySchemaIn(ModelSchema):
    category_id: Optional[str] = None
    owner_id: Optional[str] = None

    class Config:
        model = DouyinQuickReply
        model_exclude = (*exclude_fields, 'category', 'owner', 'use_count')


class DouyinQuickReplySchemaOut(ModelSchema):
    category_id: Optional[str] = None
    owner_id: Optional[str] = None
    owner_name: Optional[str] = None

    class Config:
        model = DouyinQuickReply
        model_fields = "__all__"

    @staticmethod
    def resolve_category_id(obj):
        return str(obj.category_id) if obj.category_id else None

    @staticmethod
    def resolve_owner_id(obj):
        return str(obj.owner_id) if obj.owner_id else None

    @staticmethod
    def resolve_owner_name(obj):
        try:
            return (obj.owner.name or obj.owner.username) if obj.owner else None
        except Exception:
            return None


class DouyinQuickReplyBatchDeleteIn(Schema):
    ids: List[str]
