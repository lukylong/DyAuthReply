#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""快手账号管理 API"""
from typing import List

from django.shortcuts import get_object_or_404
from ninja import Query, Router
from ninja.errors import HttpError
from ninja.pagination import paginate

from common.fu_crud import create, retrieve
from common.fu_pagination import MyPagination
from core.kuaishou.kuaishou_account_group_model import KuaishouAccountGroup
from core.kuaishou.kuaishou_account_model import KuaishouAccount
from core.kuaishou.kuaishou_account_schema import (
    KuaishouAccountBatchDeleteIn,
    KuaishouAccountFilters,
    KuaishouAccountSchemaIn,
    KuaishouAccountSchemaOut,
    KuaishouAccountSchemaPatch,
    KuaishouAccountSimpleOut,
)

router = Router()


@router.get("/account", response=List[KuaishouAccountSchemaOut], summary="快手账号列表（分页）")
@paginate(MyPagination)
def list_account(request, filters: KuaishouAccountFilters = Query(...)):
    return retrieve(request, KuaishouAccount, filters)


@router.get("/account/all", response=List[KuaishouAccountSimpleOut], summary="全部账号（下拉）")
def list_all_account(request):
    return KuaishouAccount.objects.filter(is_deleted=False).order_by('-sort', '-sys_create_datetime')


@router.get("/account/{account_id}", response=KuaishouAccountSchemaOut, summary="账号详情")
def get_account(request, account_id: str):
    return get_object_or_404(KuaishouAccount, id=account_id)


@router.post("/account", response=KuaishouAccountSchemaOut, summary="新增快手账号")
def create_account(request, data: KuaishouAccountSchemaIn):
    payload = data.dict()
    group_id = payload.get('group_id')
    if group_id and not KuaishouAccountGroup.objects.filter(id=group_id).exists():
        raise HttpError(400, "所属分组不存在")
    payload['owner_id'] = request.auth.id
    return create(request, payload, KuaishouAccount)


@router.put("/account/{account_id}", response=KuaishouAccountSchemaOut, summary="更新账号")
def update_account(request, account_id: str, data: KuaishouAccountSchemaIn):
    account = get_object_or_404(KuaishouAccount, id=account_id)
    payload = data.dict()
    group_id = payload.get('group_id')
    if group_id and not KuaishouAccountGroup.objects.filter(id=group_id).exists():
        raise HttpError(400, "所属分组不存在")
    for attr, value in payload.items():
        setattr(account, attr, value)
    account.save()
    return account


@router.patch("/account/{account_id}", response=KuaishouAccountSchemaOut, summary="部分更新账号")
def patch_account(request, account_id: str, data: KuaishouAccountSchemaPatch):
    account = get_object_or_404(KuaishouAccount, id=account_id)
    for attr, value in data.dict(exclude_unset=True).items():
        setattr(account, attr, value)
    account.save()
    return account


@router.delete("/account/{account_id}", summary="删除账号")
def delete_account(request, account_id: str):
    account = get_object_or_404(KuaishouAccount, id=account_id)
    account.delete()
    return {"success": True}


@router.post("/account/batch/delete", summary="批量删除账号")
def batch_delete_account(request, data: KuaishouAccountBatchDeleteIn):
    count = KuaishouAccount.objects.filter(id__in=data.ids).delete()[0]
    return {"count": count}
