#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""跨平台快捷回复 API"""
from typing import List

from django.shortcuts import get_object_or_404
from ninja import Query, Router
from ninja.errors import HttpError
from ninja.pagination import paginate

from common.fu_crud import create, retrieve
from common.fu_pagination import MyPagination
from core.social.constants import PLATFORM_VALUES
from core.social.quick_reply_model import QuickReply
from core.social.quick_reply_schema import (
    QuickReplyBatchDeleteIn,
    QuickReplyFilters,
    QuickReplySchemaIn,
    QuickReplySchemaOut,
    QuickReplySchemaPatch,
)

router = Router()


def _check_platform(platform) -> None:
    if platform and platform not in PLATFORM_VALUES:
        raise HttpError(400, f"不支持的平台: {platform}")


@router.get("/quick-reply", response=List[QuickReplySchemaOut], summary="快捷回复列表（分页）")
@paginate(MyPagination)
def list_quick_reply(request, filters: QuickReplyFilters = Query(...)):
    return retrieve(request, QuickReply, filters)


@router.get("/quick-reply/{qr_id}", response=QuickReplySchemaOut, summary="快捷回复详情")
def get_quick_reply(request, qr_id: str):
    return get_object_or_404(QuickReply, id=qr_id)


@router.post("/quick-reply", response=QuickReplySchemaOut, summary="创建快捷回复")
def create_quick_reply(request, data: QuickReplySchemaIn):
    payload = data.dict()
    _check_platform(payload.get('platform'))
    if not payload.get('owner_id'):
        payload['owner_id'] = request.auth.id
    return create(request, payload, QuickReply)


@router.put("/quick-reply/{qr_id}", response=QuickReplySchemaOut, summary="更新快捷回复")
def update_quick_reply(request, qr_id: str, data: QuickReplySchemaIn):
    qr = get_object_or_404(QuickReply, id=qr_id)
    payload = data.dict(exclude_unset=True)
    _check_platform(payload.get('platform'))
    for attr, value in payload.items():
        setattr(qr, attr, value)
    qr.save()
    return qr


@router.patch("/quick-reply/{qr_id}", response=QuickReplySchemaOut, summary="部分更新快捷回复")
def patch_quick_reply(request, qr_id: str, data: QuickReplySchemaPatch):
    qr = get_object_or_404(QuickReply, id=qr_id)
    payload = data.dict(exclude_unset=True)
    _check_platform(payload.get('platform'))
    for attr, value in payload.items():
        setattr(qr, attr, value)
    qr.save()
    return qr


@router.delete("/quick-reply/{qr_id}", summary="删除快捷回复")
def delete_quick_reply(request, qr_id: str):
    qr = get_object_or_404(QuickReply, id=qr_id)
    qr.delete()
    return {"success": True}


@router.post("/quick-reply/batch/delete", summary="批量删除快捷回复")
def batch_delete_quick_reply(request, data: QuickReplyBatchDeleteIn):
    count = QuickReply.objects.filter(id__in=data.ids).delete()[0]
    return {"count": count}
