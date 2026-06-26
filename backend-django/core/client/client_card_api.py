#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: core/client/client_card_api.py
@Desc: 客户端卡片 API —— 本地 DouyinCard CRUD（真源）+ 成功后经 license 通道同步到公网。
       仅在客户端进程（/api/client/v1/douyin/card）暴露；公网后台用纯净的 douyin_card_api。
       封面图经 /card/cover 代理转发到公网 file_manager 托管（抖音爬虫可抓）。
"""
from typing import List

from django.shortcuts import get_object_or_404
from ninja import File, Query, Router
from ninja.files import UploadedFile

from common.fu_crud import create, retrieve
from common.fu_pagination import MyPagination
from ninja.pagination import paginate

from core.client.card_sync import push_cover, try_sync_card, try_sync_delete, CardSyncError
from core.douyin.douyin_card_api import _normalize_card_payload
from core.douyin.douyin_card_model import DouyinCard
from core.douyin.douyin_card_schema import (
    DouyinCardBatchDeleteIn,
    DouyinCardBatchDeleteOut,
    DouyinCardCoverOut,
    DouyinCardFilters,
    DouyinCardSchemaIn,
    DouyinCardSchemaOut,
    DouyinCardSchemaPatch,
    DouyinCardSimpleOut,
)

router = Router()


def _sync_and_mark(card: DouyinCard) -> None:
    """同步到公网并把结果写回本地 sync_state（不抛异常，失败留 failed 供补同步）。"""
    state = try_sync_card(card)
    if card.sync_state != state:
        card.sync_state = state
        card.save(update_fields=['sync_state', 'sys_update_datetime'])


@router.get("/card", response=List[DouyinCardSchemaOut], summary="卡片列表（分页）")
@paginate(MyPagination)
def list_card(request, filters: DouyinCardFilters = Query(...)):
    return retrieve(request, DouyinCard, filters)


@router.get("/card/all", response=List[DouyinCardSimpleOut], summary="全部启用卡片（下拉多选）")
def list_all_card(request):
    return DouyinCard.objects.filter(status=True, is_deleted=False).order_by('-sort', '-sys_create_datetime')


@router.post("/card/cover", response=DouyinCardCoverOut, summary="卡片封面上传（转发公网托管）")
def upload_card_cover(request, file: UploadedFile = File(...)):
    """client-ui 上传封面 → 经 license 通道转发公网 → 返回公网 cover_file_id + cover_url。"""
    from ninja.errors import HttpError

    try:
        result = push_cover(file.read(), file.name, file.content_type or 'application/octet-stream')
    except CardSyncError as exc:
        raise HttpError(502, f"封面上传到公网失败：{exc}")
    return result


@router.get("/card/{card_id}", response=DouyinCardSchemaOut, summary="卡片详情")
def get_card(request, card_id: str):
    return get_object_or_404(DouyinCard, id=card_id)


@router.post("/card", response=DouyinCardSchemaOut, summary="创建卡片（本地真源 + 同步公网）")
def create_card(request, data: DouyinCardSchemaIn):
    payload = _normalize_card_payload(data.dict())
    if not payload.get('owner_id'):
        payload['owner_id'] = request.auth.id
    payload['sync_state'] = 'pending'
    card = create(request, payload, DouyinCard)
    _sync_and_mark(card)
    return card


@router.put("/card/{card_id}", response=DouyinCardSchemaOut, summary="更新卡片（本地真源 + 同步公网）")
def update_card(request, card_id: str, data: DouyinCardSchemaIn):
    card = get_object_or_404(DouyinCard, id=card_id)
    payload = _normalize_card_payload(data.dict(exclude_unset=True))
    payload.pop('owner_id', None)
    for attr, value in payload.items():
        setattr(card, attr, value)
    card.save()
    _sync_and_mark(card)
    return card


@router.patch("/card/{card_id}", response=DouyinCardSchemaOut, summary="部分更新卡片")
def patch_card(request, card_id: str, data: DouyinCardSchemaPatch):
    card = get_object_or_404(DouyinCard, id=card_id)
    payload = _normalize_card_payload(data.dict(exclude_unset=True))
    for attr, value in payload.items():
        setattr(card, attr, value)
    card.save()
    _sync_and_mark(card)
    return card


@router.delete("/card/{card_id}", summary="删除卡片（本地 + 同步公网停用）")
def delete_card(request, card_id: str):
    card = get_object_or_404(DouyinCard, id=card_id)
    card.delete()
    try_sync_delete(card_id)
    return {"success": True}


@router.post("/card/batch/delete", response=DouyinCardBatchDeleteOut, summary="批量删除卡片")
def batch_delete_card(request, data: DouyinCardBatchDeleteIn):
    count = DouyinCard.objects.filter(id__in=data.ids).delete()[0]
    for cid in data.ids:
        try_sync_delete(cid)
    return {"count": count}
