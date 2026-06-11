#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""跨平台黑名单 API"""
from typing import List

from django.shortcuts import get_object_or_404
from ninja import Query, Router
from ninja.errors import HttpError
from ninja.pagination import paginate

from common.fu_crud import create, retrieve
from common.fu_pagination import MyPagination
from core.social.blacklist_model import Blacklist
from core.social.blacklist_schema import (
    BlacklistBatchDeleteIn,
    BlacklistFilters,
    BlacklistSchemaIn,
    BlacklistSchemaOut,
    BlacklistSchemaPatch,
)
from core.social.constants import PLATFORM_VALUES
from core.social.services import account_exists, group_exists

router = Router()


def _validate(payload: dict) -> None:
    platform = payload.get('platform')
    if platform not in PLATFORM_VALUES:
        raise HttpError(400, f"不支持的平台: {platform}")
    if not (payload.get('value') or '').strip():
        raise HttpError(400, "黑名单值不能为空")
    scope = payload.get('scope', 'global')
    if scope == 'account' and not account_exists(platform, payload.get('account_id')):
        raise HttpError(400, "绑定的账号不存在")
    if scope == 'group' and not group_exists(platform, payload.get('group_id')):
        raise HttpError(400, "绑定的分组不存在")


@router.get("/blacklist", response=List[BlacklistSchemaOut], summary="黑名单列表（分页）")
@paginate(MyPagination)
def list_blacklist(request, filters: BlacklistFilters = Query(...)):
    return retrieve(request, Blacklist, filters)


@router.get("/blacklist/{bl_id}", response=BlacklistSchemaOut, summary="黑名单详情")
def get_blacklist(request, bl_id: str):
    return get_object_or_404(Blacklist, id=bl_id)


@router.post("/blacklist", response=BlacklistSchemaOut, summary="创建黑名单")
def create_blacklist(request, data: BlacklistSchemaIn):
    payload = data.dict()
    _validate(payload)
    return create(request, payload, Blacklist)


@router.put("/blacklist/{bl_id}", response=BlacklistSchemaOut, summary="更新黑名单")
def update_blacklist(request, bl_id: str, data: BlacklistSchemaIn):
    bl = get_object_or_404(Blacklist, id=bl_id)
    payload = data.dict()
    _validate(payload)
    for attr, value in payload.items():
        setattr(bl, attr, value)
    bl.save()
    return bl


@router.patch("/blacklist/{bl_id}", response=BlacklistSchemaOut, summary="部分更新黑名单")
def patch_blacklist(request, bl_id: str, data: BlacklistSchemaPatch):
    bl = get_object_or_404(Blacklist, id=bl_id)
    updates = data.dict(exclude_unset=True)
    for attr, value in updates.items():
        setattr(bl, attr, value)
    bl.save()
    return bl


@router.delete("/blacklist/{bl_id}", summary="删除黑名单")
def delete_blacklist(request, bl_id: str):
    bl = get_object_or_404(Blacklist, id=bl_id)
    bl.delete()
    return {"success": True}


@router.post("/blacklist/batch/delete", summary="批量删除黑名单")
def batch_delete_blacklist(request, data: BlacklistBatchDeleteIn):
    count = Blacklist.objects.filter(id__in=data.ids).delete()[0]
    return {"count": count}
