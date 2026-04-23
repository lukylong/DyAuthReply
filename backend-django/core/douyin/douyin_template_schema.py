#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音回复模板 Schema（含模板分类）"""
from typing import List, Optional

from ninja import Field, ModelSchema, Schema

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.douyin.douyin_template_category_model import DouyinTemplateCategory
from core.douyin.douyin_template_model import DouyinTemplate


# ---------- 模板分类 ----------
class DouyinTemplateCategoryFilters(FuFilters):
    name: Optional[str] = Field(None, q="name__icontains", alias="name")
    parent_id: Optional[str] = Field(None, q="parent_id", alias="parent_id")


class DouyinTemplateCategorySchemaIn(ModelSchema):
    parent_id: Optional[str] = None

    class Config:
        model = DouyinTemplateCategory
        model_exclude = (*exclude_fields, 'parent')


class DouyinTemplateCategorySchemaOut(ModelSchema):
    parent_id: Optional[str] = None
    template_count: int = 0
    children: List["DouyinTemplateCategorySchemaOut"] = []

    class Config:
        model = DouyinTemplateCategory
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


# ---------- 模板 ----------
class DouyinTemplateFilters(FuFilters):
    name: Optional[str] = Field(None, q="name__icontains", alias="name")
    category_id: Optional[str] = Field(None, q="category_id", alias="category_id")
    status: Optional[bool] = Field(None, q="status", alias="status")
    is_shared: Optional[bool] = Field(None, q="is_shared", alias="is_shared")
    owner_id: Optional[str] = Field(None, q="owner_id", alias="owner_id")


class DouyinTemplateSchemaIn(ModelSchema):
    category_id: Optional[str] = None
    owner_id: Optional[str] = None

    class Config:
        model = DouyinTemplate
        model_exclude = (*exclude_fields, 'category', 'owner', 'use_count')


class DouyinTemplateSchemaPatch(Schema):
    name: Optional[str] = None
    category_id: Optional[str] = None
    content: Optional[str] = None
    links: Optional[List[dict]] = None
    variables: Optional[List[str]] = None
    send_mode: Optional[str] = None
    status: Optional[bool] = None
    is_shared: Optional[bool] = None
    remark: Optional[str] = None


class DouyinTemplateSchemaOut(ModelSchema):
    category_id: Optional[str] = None
    category_name: Optional[str] = None
    owner_id: Optional[str] = None
    owner_name: Optional[str] = None

    class Config:
        model = DouyinTemplate
        model_fields = "__all__"

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


class DouyinTemplateSimpleOut(Schema):
    id: str
    name: str
    category_id: Optional[str] = None


class DouyinTemplateBatchDeleteIn(Schema):
    ids: List[str]


class DouyinTemplatePreviewIn(Schema):
    """模板预览：支持把变量 context 填入预览渲染后的文本"""
    template_id: str
    context: dict = Field(default_factory=dict, description="变量取值，如 {'nickname': '小明'}")


class DouyinTemplatePreviewOut(Schema):
    rendered: str
    links: List[dict] = []
