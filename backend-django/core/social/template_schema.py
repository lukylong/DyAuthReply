#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""跨平台回复模板 Schema"""
from typing import List, Optional

from ninja import Field, ModelSchema, Schema

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.social.template_model import ReplyTemplate


class ReplyTemplateFilters(FuFilters):
    platform: Optional[str] = Field(None, q="platform", alias="platform")
    name: Optional[str] = Field(None, q="name__icontains", alias="name")
    category_id: Optional[str] = Field(None, q="category_id", alias="category_id")
    status: Optional[bool] = Field(None, q="status", alias="status")
    is_shared: Optional[bool] = Field(None, q="is_shared", alias="is_shared")
    owner_id: Optional[str] = Field(None, q="owner_id", alias="owner_id")


class ReplyTemplateSchemaIn(ModelSchema):
    category_id: Optional[str] = None
    owner_id: Optional[str] = None

    class Config:
        model = ReplyTemplate
        model_exclude = (*exclude_fields, 'category', 'owner', 'use_count')


class ReplyTemplateSchemaPatch(Schema):
    platform: Optional[str] = None
    name: Optional[str] = None
    category_id: Optional[str] = None
    content: Optional[str] = None
    links: Optional[List[dict]] = None
    variables: Optional[List[str]] = None
    send_mode: Optional[str] = None
    status: Optional[bool] = None
    is_shared: Optional[bool] = None
    remark: Optional[str] = None


class ReplyTemplateSchemaOut(ModelSchema):
    platform_display: Optional[str] = None
    category_id: Optional[str] = None
    category_name: Optional[str] = None
    owner_id: Optional[str] = None
    owner_name: Optional[str] = None

    class Config:
        model = ReplyTemplate
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


class ReplyTemplateSimpleOut(Schema):
    id: str
    name: str
    category_id: Optional[str] = None


class ReplyTemplateBatchDeleteIn(Schema):
    ids: List[str]


class ReplyTemplatePreviewIn(Schema):
    """模板预览：支持把变量 context 填入预览渲染后的文本"""
    template_id: str
    context: dict = Field(default_factory=dict, description="变量取值，如 {'nickname': '小明'}")


class ReplyTemplatePreviewOut(Schema):
    rendered: str
    links: List[dict] = []
