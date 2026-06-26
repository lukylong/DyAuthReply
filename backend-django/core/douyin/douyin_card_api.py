#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音伪装卡片 API"""
from typing import List
from urllib.parse import urlsplit

from django.shortcuts import get_object_or_404
from ninja import Query, Router
from ninja.errors import HttpError
from ninja.pagination import paginate

from common.fu_crud import create, retrieve
from common.fu_pagination import MyPagination
from core.douyin.douyin_card_model import DouyinCard
from core.douyin.douyin_card_schema import (
    DouyinCardBatchDeleteIn,
    DouyinCardBatchDeleteOut,
    DouyinCardFilters,
    DouyinCardSchemaIn,
    DouyinCardSchemaOut,
    DouyinCardSchemaPatch,
    DouyinCardSimpleOut,
)

router = Router()


def _validate_target_url(url) -> str:
    """卡片目标链接仅允许 http/https，防开放重定向与脚本注入。"""
    u = (url or '').strip()
    if not u:
        raise HttpError(400, "卡片目标链接不能为空")
    scheme = urlsplit(u).scheme.lower()
    if scheme not in ('http', 'https'):
        raise HttpError(400, "卡片目标链接仅支持 http/https")
    return u


def _normalize_card_payload(payload: dict) -> dict:
    normalized = dict(payload)
    if 'target_url' in normalized:
        normalized['target_url'] = _validate_target_url(normalized.get('target_url'))
    cover = normalized.get('cover_file_id')
    if cover == '':
        normalized['cover_file_id'] = None
    return normalized


@router.get("/card", response=List[DouyinCardSchemaOut], summary="卡片列表（分页）")
@paginate(MyPagination)
def list_card(request, filters: DouyinCardFilters = Query(...)):
    return retrieve(request, DouyinCard, filters)


@router.get("/card/all", response=List[DouyinCardSimpleOut], summary="全部启用卡片（下拉多选）")
def list_all_card(request):
    return DouyinCard.objects.filter(status=True, is_deleted=False).order_by('-sort', '-sys_create_datetime')


@router.get("/card/{card_id}", response=DouyinCardSchemaOut, summary="卡片详情")
def get_card(request, card_id: str):
    return get_object_or_404(DouyinCard, id=card_id)


@router.post("/card", response=DouyinCardSchemaOut, summary="创建卡片")
def create_card(request, data: DouyinCardSchemaIn):
    payload = _normalize_card_payload(data.dict())
    if not payload.get('owner_id'):
        payload['owner_id'] = request.auth.id
    return create(request, payload, DouyinCard)


@router.put("/card/{card_id}", response=DouyinCardSchemaOut, summary="更新卡片")
def update_card(request, card_id: str, data: DouyinCardSchemaIn):
    card = get_object_or_404(DouyinCard, id=card_id)
    payload = _normalize_card_payload(data.dict(exclude_unset=True))
    payload.pop('owner_id', None)
    for attr, value in payload.items():
        setattr(card, attr, value)
    card.save()
    return card


@router.patch("/card/{card_id}", response=DouyinCardSchemaOut, summary="部分更新卡片")
def patch_card(request, card_id: str, data: DouyinCardSchemaPatch):
    card = get_object_or_404(DouyinCard, id=card_id)
    payload = _normalize_card_payload(data.dict(exclude_unset=True))
    for attr, value in payload.items():
        setattr(card, attr, value)
    card.save()
    return card


@router.delete("/card/{card_id}", summary="删除卡片")
def delete_card(request, card_id: str):
    card = get_object_or_404(DouyinCard, id=card_id)
    card.delete()
    return {"success": True}


@router.post("/card/batch/delete", response=DouyinCardBatchDeleteOut, summary="批量删除卡片")
def batch_delete_card(request, data: DouyinCardBatchDeleteIn):
    count = DouyinCard.objects.filter(id__in=data.ids).delete()[0]
    return {"count": count}
