#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""跨平台回复模板分类 Schema"""
from typing import List, Optional

from ninja import Field, ModelSchema, Schema

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.social.template_category_model import ReplyTemplateCategory


class ReplyTemplateCategoryFilters(FuFilters):
    name: Optional[str] = Field(None, q="name__icontains", alias="name")
    parent_id: Optional[str] = Field(None, q="parent_id", alias="parent_id")


class ReplyTemplateCategorySchemaIn(ModelSchema):
    parent_id: Optional[str] = None

    class Config:
        model = ReplyTemplateCategory
        model_exclude = (*exclude_fields, 'parent')


class ReplyTemplateCategorySchemaOut(ModelSchema):
    parent_id: Optional[str] = None
    template_count: int = 0
    children: List["ReplyTemplateCategorySchemaOut"] = []

    class Config:
        model = ReplyTemplateCategory
        model_fields = "__all__"

    @staticmethod
    def resolve_parent_id(obj):
        return str(obj.parent_id) if obj.parent_id else None

    @staticmethod
    def resolve_template_count(obj):
        return obj.templates.filter(is_deleted=False).count() if hasattr(obj, 'templates') else 0

    @staticmethod
    def resolve_children(obj):
        return []  # 由 API 层按需填充避免 N+1


class ReplyTemplateCategoryBatchDeleteIn(Schema):
    ids: List[str]
