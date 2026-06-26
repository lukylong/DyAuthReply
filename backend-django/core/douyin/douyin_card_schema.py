#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音伪装卡片 Schema"""
from typing import List, Optional

from ninja import Field, ModelSchema, Schema

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.douyin.douyin_card_model import DouyinCard


def _public_base_url() -> str:
    """卡片封面图与落地页统一的公网基址。

    封面图始终托管在公网 file_manager（客户端经代理上传），故 URL 必须指向公网域名，
    不能用客户端本地的 BASE_URL（localhost:8000，本地没有该文件 → 破图）。
    优先级：DOUYIN_CARD_LANDING_BASE_URL（客户端从授权服务地址推导）> DOWNLOAD_PUBLIC_BASE_URL > BASE_URL。
    """
    from django.conf import settings

    return (
        getattr(settings, 'DOUYIN_CARD_LANDING_BASE_URL', '')
        or getattr(settings, 'DOWNLOAD_PUBLIC_BASE_URL', '')
        or getattr(settings, 'BASE_URL', '')
        or 'http://localhost:8000'
    ).rstrip('/')


def build_cover_url(cover_file_id: Optional[str]) -> Optional[str]:
    """由 cover_file_id 拼 file_manager 公开访问 URL（auth=None 的 proxy 路由）。

    封面图存在公网，故用公网基址 [[_public_base_url]]，客户端与落地页都能正确访问。
    """
    if not cover_file_id:
        return None
    return f"{_public_base_url()}/api/core/file_manager/proxy/{cover_file_id}"


def build_landing_url(card_id: str) -> str:
    """拼卡片落地页 URL：<公网域名>/c/<card_id>。"""
    return f"{_public_base_url()}/c/{card_id}"


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
