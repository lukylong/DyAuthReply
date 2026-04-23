#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音事件 API"""
from typing import List

from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Query, Router
from ninja.pagination import paginate

from common.fu_crud import retrieve
from common.fu_pagination import MyPagination
from core.douyin.douyin_event_model import DouyinEvent
from core.douyin.douyin_event_schema import (
    DouyinEventActionOut,
    DouyinEventBatchReadIn,
    DouyinEventFilters,
    DouyinEventReportIn,
    DouyinEventSchemaOut,
)

router = Router()


@router.get("/event", response=List[DouyinEventSchemaOut], summary="事件列表（分页）")
@paginate(MyPagination)
def list_event(request, filters: DouyinEventFilters = Query(...)):
    return retrieve(request, DouyinEvent, filters)


@router.get("/event/unread-count", summary="未读告警数")
def unread_count(request):
    count = DouyinEvent.objects.filter(
        is_read=False, level__in=['warning', 'error', 'critical']
    ).count()
    return {"count": count}


@router.post("/event/report", response=DouyinEventActionOut, summary="worker 上报事件")
def report_event(request, data: DouyinEventReportIn):
    payload = data.dict()
    if not payload.get('occurred_at'):
        payload['occurred_at'] = timezone.now()
    DouyinEvent.objects.create(**payload)
    return DouyinEventActionOut(success=True, message="ok", count=1)


@router.post("/event/batch/read", response=DouyinEventActionOut, summary="批量标记已读")
def batch_read(request, data: DouyinEventBatchReadIn):
    count = DouyinEvent.objects.filter(id__in=data.ids).update(is_read=True)
    return DouyinEventActionOut(success=True, message="ok", count=count)


@router.post("/event/read-all", response=DouyinEventActionOut, summary="全部标记已读")
def read_all(request):
    count = DouyinEvent.objects.filter(is_read=False).update(is_read=True)
    return DouyinEventActionOut(success=True, message="ok", count=count)


@router.get("/event/{eid}", response=DouyinEventSchemaOut, summary="事件详情")
def get_event(request, eid: str):
    return get_object_or_404(DouyinEvent, id=eid)
