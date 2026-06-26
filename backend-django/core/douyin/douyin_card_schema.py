#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音伪装卡片 Schema"""
from typing import List, Optional

from ninja import Field, ModelSchema, Schema

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.douyin.douyin_card_model import DouyinCard


def build_cover_url(cover_file_id: Optional[str]) -> Optional[str]:
    """由 cover_file_id 拼 file_manager 公开访问 URL（auth=None 的 proxy 路由）。

    用 settings.BASE_URL 作为前缀，前端/落地页都可直接访问。
    """
    if not cover_file_id:
        return None
    from django.conf import settings

    base = (getattr(settings, 'BASE_URL', '') or 'http://localhost:8000').rstrip('/')
    return f"{base}/api/core/file_manager/proxy/{cover_file_id}"


def build_landing_url(card_id: str) -> str:
    """拼卡片落地页 URL：<公网域名>/c/<card_id>。"""
    from django.conf import settings

    base = (
        getattr(settings, 'DOUYIN_CARD_LANDING_BASE_URL', '')
        or getattr(settings, 'DOWNLOAD_PUBLIC_BASE_URL', '')
        or getattr(settings, 'BASE_URL', '')
        or 'http://localhost:8000'
    ).rstrip('/')
    return f"{base}/c/{card_id}"


class DouyinCardFilters(FuFilters):
    title: Optional[str] = Field(None, q="title__icontains", alias="title")
    remark: Optional[str] = Field(None, q="remark__icontains", alias="remark")
    status: Optional[bool] = Field(None, q="status", alias="status")
    owner_id: Optional[str] = Field(None, q="owner_id", alias="owner_id")


class DouyinCardSchemaIn(ModelSchema):
    owner_id: Optional[str] = None

    class Config:
        model = DouyinCard
        model_exclude = (*exclude_fields, 'owner')


class DouyinCardSchemaPatch(Schema):
    title: Optional[str] = None
    description: Optional[str] = None
    cover_file_id: Optional[str] = None
    target_url: Optional[str] = None
    remark: Optional[str] = None
    status: Optional[bool] = None
    is_shared: Optional[bool] = None


class DouyinCardSchemaOut(ModelSchema):
    owner_id: Optional[str] = None
    owner_name: Optional[str] = None
    cover_url: Optional[str] = None
    landing_url: Optional[str] = None

    class Config:
        model = DouyinCard
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
    def resolve_cover_url(obj):
        return build_cover_url(obj.cover_file_id)

    @staticmethod
    def resolve_landing_url(obj):
        return build_landing_url(str(obj.id))


class DouyinCardSimpleOut(Schema):
    """规则多选下拉用的精简卡片信息"""
    id: str
    title: str
    cover_url: Optional[str] = None
    target_url: Optional[str] = None

    @staticmethod
    def resolve_cover_url(obj):
        return build_cover_url(getattr(obj, 'cover_file_id', None))


class DouyinCardBatchDeleteIn(Schema):
    ids: List[str] = Field(..., description="卡片ID列表")


class DouyinCardBatchDeleteOut(Schema):
    count: int


class DouyinCardCoverOut(Schema):
    cover_file_id: str
    cover_url: str
