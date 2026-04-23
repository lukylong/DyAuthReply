#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音账号分组 API"""
from typing import List

from django.shortcuts import get_object_or_404
from ninja import Query, Router
from ninja.errors import HttpError
from ninja.pagination import paginate

from common.fu_crud import create, retrieve
from common.fu_pagination import MyPagination
from core.douyin.douyin_account_group_model import DouyinAccountGroup
from core.douyin.douyin_account_group_schema import (
    DouyinAccountGroupBatchDeleteIn,
    DouyinAccountGroupFilters,
    DouyinAccountGroupSchemaIn,
    DouyinAccountGroupSchemaOut,
    DouyinAccountGroupSimpleOut,
)

router = Router()


@router.get("/account-group", response=List[DouyinAccountGroupSchemaOut], summary="分组列表（分页）")
@paginate(MyPagination)
def list_group(request, filters: DouyinAccountGroupFilters = Query(...)):
    return retrieve(request, DouyinAccountGroup, filters)


@router.get("/account-group/all", response=List[DouyinAccountGroupSimpleOut], summary="全部分组（下拉）")
def list_all_group(request):
    return DouyinAccountGroup.objects.filter(status=True).order_by('-sort', '-sys_create_datetime')


@router.get("/account-group/{group_id}", response=DouyinAccountGroupSchemaOut, summary="分组详情")
def get_group(request, group_id: str):
    return get_object_or_404(DouyinAccountGroup, id=group_id)


@router.post("/account-group", response=DouyinAccountGroupSchemaOut, summary="创建分组")
def create_group(request, data: DouyinAccountGroupSchemaIn):
    payload = data.dict()
    if not payload.get('owner_id'):
        payload['owner_id'] = request.auth.id
    return create(request, payload, DouyinAccountGroup)


@router.put("/account-group/{group_id}", response=DouyinAccountGroupSchemaOut, summary="更新分组")
def update_group(request, group_id: str, data: DouyinAccountGroupSchemaIn):
    group = get_object_or_404(DouyinAccountGroup, id=group_id)
    for attr, value in data.dict(exclude_unset=True).items():
        setattr(group, attr, value)
    group.save()
    return group


@router.delete("/account-group/{group_id}", summary="删除分组")
def delete_group(request, group_id: str):
    group = get_object_or_404(DouyinAccountGroup, id=group_id)
    if group.accounts.filter(is_deleted=False).exists():
        raise HttpError(400, "分组下仍有账号，请先迁移或删除账号")
    group.delete()
    return {"success": True}


@router.post("/account-group/batch/delete", summary="批量删除分组")
def batch_delete_group(request, data: DouyinAccountGroupBatchDeleteIn):
    count = 0
    failed: List[str] = []
    for gid in data.ids:
        try:
            g = DouyinAccountGroup.objects.get(id=gid)
            if g.accounts.filter(is_deleted=False).exists():
                failed.append(gid)
                continue
            g.delete()
            count += 1
        except DouyinAccountGroup.DoesNotExist:
            failed.append(gid)
    return {"count": count, "failed_ids": failed}
