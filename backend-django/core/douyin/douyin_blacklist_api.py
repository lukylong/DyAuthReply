#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音黑名单 API"""
from typing import List

from django.shortcuts import get_object_or_404
from ninja import Query, Router
from ninja.pagination import paginate

from common.fu_crud import create, retrieve
from common.fu_pagination import MyPagination
from core.douyin.douyin_blacklist_model import DouyinBlacklist
from core.douyin.douyin_blacklist_schema import (
    DouyinBlacklistBatchDeleteIn,
    DouyinBlacklistFilters,
    DouyinBlacklistSchemaIn,
    DouyinBlacklistSchemaOut,
)

router = Router()


@router.get("/blacklist", response=List[DouyinBlacklistSchemaOut], summary="黑名单列表（分页）")
@paginate(MyPagination)
def list_blacklist(request, filters: DouyinBlacklistFilters = Query(...)):
    return retrieve(request, DouyinBlacklist, filters)


@router.get("/blacklist/{bid}", response=DouyinBlacklistSchemaOut, summary="黑名单详情")
def get_blacklist(request, bid: str):
    return get_object_or_404(DouyinBlacklist, id=bid)


@router.post("/blacklist", response=DouyinBlacklistSchemaOut, summary="新增黑名单")
def create_blacklist(request, data: DouyinBlacklistSchemaIn):
    return create(request, data.dict(), DouyinBlacklist)


@router.put("/blacklist/{bid}", response=DouyinBlacklistSchemaOut, summary="更新黑名单")
def update_blacklist(request, bid: str, data: DouyinBlacklistSchemaIn):
    entry = get_object_or_404(DouyinBlacklist, id=bid)
    for k, v in data.dict(exclude_unset=True).items():
        setattr(entry, k, v)
    entry.save()
    return entry


@router.delete("/blacklist/{bid}", summary="移出黑名单")
def delete_blacklist(request, bid: str):
    entry = get_object_or_404(DouyinBlacklist, id=bid)
    entry.delete()
    return {"success": True}


@router.post("/blacklist/batch/delete", summary="批量移出黑名单")
def batch_delete_blacklist(request, data: DouyinBlacklistBatchDeleteIn):
    count = DouyinBlacklist.objects.filter(id__in=data.ids).delete()[0]
    return {"count": count}
