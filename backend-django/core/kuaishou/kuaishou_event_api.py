#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""快手运行时事件 API（含 worker 上报、标记已读）"""
from typing import List

from django.utils import timezone
from ninja import Query, Router
from ninja.pagination import paginate

from common.fu_crud import retrieve
from common.fu_pagination import MyPagination
from core.kuaishou.kuaishou_event_model import KuaishouEvent
from core.kuaishou.kuaishou_event_schema import (
    KuaishouEventFilters,
    KuaishouEventMarkReadIn,
    KuaishouEventReportIn,
    KuaishouEventSchemaOut,
)

router = Router()


@router.get("/event", response=List[KuaishouEventSchemaOut], summary="运行事件列表（分页）")
@paginate(MyPagination)
def list_event(request, filters: KuaishouEventFilters = Query(...)):
    return retrieve(request, KuaishouEvent, filters)


@router.post("/event/report", response=KuaishouEventSchemaOut, summary="worker 上报运行时事件")
def report_event(request, data: KuaishouEventReportIn):
    event = KuaishouEvent.objects.create(
        account_id=data.account_id,
        session_id=data.session_id,
        event_type=data.event_type,
        level=data.level,
        title=data.title,
        detail=data.detail,
        payload=data.payload,
        worker_id=data.worker_id,
        occurred_at=data.occurred_at or timezone.now(),
    )
    return event


@router.post("/event/mark-read", summary="批量标记事件已读")
def mark_read(request, data: KuaishouEventMarkReadIn):
    count = KuaishouEvent.objects.filter(id__in=data.ids).update(is_read=True)
    return {"count": count}
