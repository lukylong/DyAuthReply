#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音快捷回复 API"""
from typing import List

from django.shortcuts import get_object_or_404
from ninja import Query, Router
from ninja.pagination import paginate

from common.fu_crud import create, retrieve
from common.fu_pagination import MyPagination
from core.douyin.douyin_quick_reply_model import DouyinQuickReply
from core.douyin.douyin_quick_reply_schema import (
    DouyinQuickReplyBatchDeleteIn,
    DouyinQuickReplyFilters,
    DouyinQuickReplySchemaIn,
    DouyinQuickReplySchemaOut,
)

router = Router()


@router.get("/quick-reply", response=List[DouyinQuickReplySchemaOut], summary="快捷回复列表（分页）")
@paginate(MyPagination)
def list_quick_reply(request, filters: DouyinQuickReplyFilters = Query(...)):
    return retrieve(request, DouyinQuickReply, filters)


@router.get("/quick-reply/all", response=List[DouyinQuickReplySchemaOut], summary="全部快捷回复（人工客服抽屉）")
def list_all(request):
    return DouyinQuickReply.objects.filter(status=True, is_deleted=False).order_by('-sort')


@router.get("/quick-reply/{qid}", response=DouyinQuickReplySchemaOut, summary="快捷回复详情")
def get_quick_reply(request, qid: str):
    return get_object_or_404(DouyinQuickReply, id=qid)


@router.post("/quick-reply", response=DouyinQuickReplySchemaOut, summary="新增快捷回复")
def create_quick_reply(request, data: DouyinQuickReplySchemaIn):
    payload = data.dict()
    if not payload.get('owner_id'):
        payload['owner_id'] = request.auth.id
    return create(request, payload, DouyinQuickReply)


@router.put("/quick-reply/{qid}", response=DouyinQuickReplySchemaOut, summary="更新快捷回复")
def update_quick_reply(request, qid: str, data: DouyinQuickReplySchemaIn):
    qr = get_object_or_404(DouyinQuickReply, id=qid)
    for k, v in data.dict(exclude_unset=True).items():
        setattr(qr, k, v)
    qr.save()
    return qr


@router.delete("/quick-reply/{qid}", summary="删除快捷回复")
def delete_quick_reply(request, qid: str):
    qr = get_object_or_404(DouyinQuickReply, id=qid)
    qr.delete()
    return {"success": True}


@router.post("/quick-reply/batch/delete", summary="批量删除快捷回复")
def batch_delete_quick_reply(request, data: DouyinQuickReplyBatchDeleteIn):
    count = DouyinQuickReply.objects.filter(id__in=data.ids).delete()[0]
    return {"count": count}
