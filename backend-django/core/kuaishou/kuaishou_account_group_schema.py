#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""快手账号分组 Schema"""
from typing import List, Optional

from ninja import Field, ModelSchema, Schema

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.kuaishou.kuaishou_account_group_model import KuaishouAccountGroup


class KuaishouAccountGroupFilters(FuFilters):
    name: Optional[str] = Field(None, q="name__icontains", alias="name")
    status: Optional[bool] = Field(None, q="status", alias="status")


class KuaishouAccountGroupSchemaIn(ModelSchema):
    owner_id: Optional[str] = None

    class Config:
        model = KuaishouAccountGroup
        model_exclude = (*exclude_fields, 'owner')


class KuaishouAccountGroupSchemaOut(ModelSchema):
    owner_id: Optional[str] = None
    account_count: int = 0

    class Config:
        model = KuaishouAccountGroup
        model_fields = "__all__"

    @staticmethod
    def resolve_owner_id(obj):
        return str(obj.owner_id) if obj.owner_id else None

    @staticmethod
    def resolve_account_count(obj):
        return obj.accounts.filter(is_deleted=False).count() if hasattr(obj, 'accounts') else 0


class KuaishouAccountGroupBatchDeleteIn(Schema):
    ids: List[str]
