#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""快手账号分组 API"""
from typing import List

from django.shortcuts import get_object_or_404
from ninja import Query, Router
from ninja.pagination import paginate

from common.fu_crud import create, retrieve
from common.fu_pagination import MyPagination
from core.kuaishou.kuaishou_account_group_model import KuaishouAccountGroup
from core.kuaishou.kuaishou_account_group_schema import (
    KuaishouAccountGroupBatchDeleteIn,
    KuaishouAccountGroupFilters,
    KuaishouAccountGroupSchemaIn,
    KuaishouAccountGroupSchemaOut,
)
from core.kuaishou.kuaishou_account_model import KuaishouAccount

router = Router()


@router.get("/account-group", response=List[KuaishouAccountGroupSchemaOut], summary="账号分组列表（分页）")
@paginate(MyPagination)
def list_group(request, filters: KuaishouAccountGroupFilters = Query(...)):
    return retrieve(request, KuaishouAccountGroup, filters)


@router.get("/account-group/all", response=List[KuaishouAccountGroupSchemaOut], summary="全部分组（下拉）")
def list_all_group(request):
    return KuaishouAccountGroup.objects.filter(is_deleted=False).order_by('-sort', '-sys_create_datetime')


@router.post("/account-group", response=KuaishouAccountGroupSchemaOut, summary="创建分组")
def create_group(request, data: KuaishouAccountGroupSchemaIn):
    payload = data.dict()
    if not payload.get('owner_id'):
        payload['owner_id'] = request.auth.id
    return create(request, payload, KuaishouAccountGroup)


@router.put("/account-group/{group_id}", response=KuaishouAccountGroupSchemaOut, summary="更新分组")
def update_group(request, group_id: str, data: KuaishouAccountGroupSchemaIn):
    group = get_object_or_404(KuaishouAccountGroup, id=group_id)
    for attr, value in data.dict(exclude_unset=True).items():
        setattr(group, attr, value)
    group.save()
    return group


@router.delete("/account-group/{group_id}", summary="删除分组")
def delete_group(request, group_id: str):
    group = get_object_or_404(KuaishouAccountGroup, id=group_id)
    KuaishouAccount.objects.filter(group_id=group_id).update(group=None)
    group.delete()
    return {"success": True}


@router.post("/account-group/batch/delete", summary="批量删除分组")
def batch_delete_group(request, data: KuaishouAccountGroupBatchDeleteIn):
    KuaishouAccount.objects.filter(group_id__in=data.ids).update(group=None)
    count = KuaishouAccountGroup.objects.filter(id__in=data.ids).delete()[0]
    return {"count": count}
