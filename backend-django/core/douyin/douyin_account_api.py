#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: douyin_account_api.py
@Desc: Douyin Account API - 抖音账号管理接口
        提供账号 CRUD、批量删除、登录/登出触发等操作。登录/登出触发只是写入指令，
        真正的浏览器动作由独立的 douyin worker 进程消费执行（M2 里程碑）。
"""
from typing import List

from django.shortcuts import get_object_or_404
from ninja import Query, Router
from ninja.errors import HttpError
from ninja.pagination import paginate

from common.fu_crud import create, delete, retrieve
from common.fu_pagination import MyPagination
from core.douyin.douyin_account_model import DouyinAccount
from core.douyin.douyin_account_schema import (
    DouyinAccountActionOut,
    DouyinAccountBatchDeleteIn,
    DouyinAccountBatchDeleteOut,
    DouyinAccountFilters,
    DouyinAccountSchemaIn,
    DouyinAccountSchemaOut,
    DouyinAccountSchemaPatch,
    DouyinAccountSimpleOut,
)

router = Router()


@router.get("/account", response=List[DouyinAccountSchemaOut], summary="获取抖音账号列表（分页）")
@paginate(MyPagination)
def list_douyin_account(request, filters: DouyinAccountFilters = Query(...)):
    """分页查询抖音账号列表"""
    return retrieve(request, DouyinAccount, filters)


@router.get("/account/all", response=List[DouyinAccountSimpleOut], summary="获取所有抖音账号（简化版）")
def list_all_douyin_account(request):
    """返回所有未禁用账号，供前端下拉选择"""
    return DouyinAccount.objects.exclude(status=3).order_by('-sort', '-sys_create_datetime')


@router.get("/account/{account_id}", response=DouyinAccountSchemaOut, summary="获取抖音账号详情")
def get_douyin_account(request, account_id: str):
    return get_object_or_404(DouyinAccount, id=account_id)


@router.post("/account", response=DouyinAccountSchemaOut, summary="创建抖音账号")
def create_douyin_account(request, data: DouyinAccountSchemaIn):
    """创建抖音账号。若 owner_id 未传则默认为当前登录用户。"""
    payload = data.dict()
    if not payload.get('owner_id'):
        payload['owner_id'] = request.auth.id
    if DouyinAccount.objects.filter(nickname=payload['nickname'], owner_id=payload['owner_id']).exists():
        raise HttpError(400, f"同一用户下已存在昵称为 {payload['nickname']} 的账号")
    return create(request, payload, DouyinAccount)


@router.put("/account/{account_id}", response=DouyinAccountSchemaOut, summary="更新抖音账号（完全替换）")
def update_douyin_account(request, account_id: str, data: DouyinAccountSchemaIn):
    account = get_object_or_404(DouyinAccount, id=account_id)
    payload = data.dict()
    for attr, value in payload.items():
        if value is None and attr == 'owner_id':
            continue
        setattr(account, attr, value)
    account.save()
    return account


@router.patch("/account/{account_id}", response=DouyinAccountSchemaOut, summary="部分更新抖音账号")
def patch_douyin_account(request, account_id: str, data: DouyinAccountSchemaPatch):
    account = get_object_or_404(DouyinAccount, id=account_id)
    for attr, value in data.dict(exclude_unset=True).items():
        setattr(account, attr, value)
    account.save()
    return account


@router.delete("/account/{account_id}", response=DouyinAccountSchemaOut, summary="删除抖音账号")
def delete_douyin_account(request, account_id: str):
    """删除账号：在线账号需先登出/禁用"""
    account = get_object_or_404(DouyinAccount, id=account_id)
    if account.is_online():
        raise HttpError(400, "在线账号无法直接删除，请先登出或禁用")
    return delete(account_id, DouyinAccount)


@router.post(
    "/account/batch/delete",
    response=DouyinAccountBatchDeleteOut,
    summary="批量删除抖音账号",
)
def batch_delete_douyin_account(request, data: DouyinAccountBatchDeleteIn):
    failed_ids: List[str] = []
    success = 0
    for aid in data.ids:
        try:
            acc = DouyinAccount.objects.get(id=aid)
            if acc.is_online():
                failed_ids.append(aid)
                continue
            acc.delete()
            success += 1
        except DouyinAccount.DoesNotExist:
            failed_ids.append(aid)
    return DouyinAccountBatchDeleteOut(count=success, failed_ids=failed_ids)


@router.post(
    "/account/{account_id}/login",
    response=DouyinAccountActionOut,
    summary="触发扫码登录",
)
def trigger_login(request, account_id: str):
    """
    触发扫码登录：只写入意图，真正的浏览器动作由 douyin worker 在 M2 阶段实现。
    这里只做前置校验并将账号置为"未登录"态，等待 worker 打开有头浏览器。
    """
    account = get_object_or_404(DouyinAccount, id=account_id)
    if account.status == 3:
        raise HttpError(400, "该账号已禁用，无法登录")
    account.status = 0
    account.save(update_fields=['status', 'sys_update_datetime'])
    return DouyinAccountActionOut(
        success=True,
        message="已下发扫码登录指令，请在弹出的浏览器窗口完成扫码",
    )


@router.post(
    "/account/{account_id}/logout",
    response=DouyinAccountActionOut,
    summary="登出抖音账号",
)
def trigger_logout(request, account_id: str):
    """登出：清理 storage_state 并置为未登录。真正清理动作由 worker 完成。"""
    account = get_object_or_404(DouyinAccount, id=account_id)
    account.status = 0
    account.storage_state_path = ''
    account.save(update_fields=['status', 'storage_state_path', 'sys_update_datetime'])
    return DouyinAccountActionOut(success=True, message="登出指令已下发")
