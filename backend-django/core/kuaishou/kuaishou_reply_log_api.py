#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""快手回复日志 + 看板概览 API"""
from typing import List

from django.shortcuts import get_object_or_404
from ninja import Query, Router
from ninja.pagination import paginate

from common.fu_crud import retrieve
from common.fu_pagination import MyPagination
from core.kuaishou.kuaishou_reply_log_model import KuaishouReplyLog
from core.kuaishou.kuaishou_reply_log_schema import (
    KuaishouDashboardOverviewOut,
    KuaishouReplyLogFilters,
    KuaishouReplyLogSchemaOut,
    KuaishouReplyLogStatOut,
)

router = Router()


@router.get("/reply-log", response=List[KuaishouReplyLogSchemaOut], summary="回复日志列表（分页）")
@paginate(MyPagination)
def list_reply_log(request, filters: KuaishouReplyLogFilters = Query(...)):
    return retrieve(request, KuaishouReplyLog, filters)


@router.get("/reply-log/stat/summary", response=KuaishouReplyLogStatOut, summary="回复日志统计")
def reply_log_stat(request, account_id: str = None):
    qs = KuaishouReplyLog.objects.all()
    if account_id:
        qs = qs.filter(account_id=account_id)
    out = KuaishouReplyLogStatOut(total=qs.count())
    for result in ('success', 'failed', 'skipped', 'cooldown', 'quota_exceeded', 'silent'):
        setattr(out, result, qs.filter(result=result).count())
    durations = list(qs.filter(result='success').values_list('duration_ms', flat=True))
    out.avg_duration_ms = round(sum(durations) / len(durations), 1) if durations else 0.0
    return out


@router.get("/reply-log/{log_id}", response=KuaishouReplyLogSchemaOut, summary="回复日志详情")
def get_reply_log(request, log_id: str):
    return get_object_or_404(KuaishouReplyLog, id=log_id)


@router.get("/dashboard/overview", response=KuaishouDashboardOverviewOut, summary="快手看板概览")
def dashboard_overview(request):
    from django.db.models import Sum

    from core.kuaishou.kuaishou_account_model import KuaishouAccount
    from core.kuaishou.kuaishou_event_model import KuaishouEvent
    from core.kuaishou.kuaishou_session_model import KuaishouSession

    accounts = KuaishouAccount.objects.filter(is_deleted=False)
    sessions = KuaishouSession.objects.all()
    agg = sessions.aggregate(
        msgs=Sum('messages_today'),
        replies=Sum('replies_today'),
    )
    return KuaishouDashboardOverviewOut(
        total_accounts=accounts.count(),
        online_accounts=accounts.filter(status=1).count(),
        total_sessions=sessions.count(),
        running_sessions=sessions.filter(status='running').count(),
        messages_today=agg['msgs'] or 0,
        replies_today=agg['replies'] or 0,
        success_today=KuaishouReplyLog.objects.filter(result='success').count(),
        failed_today=KuaishouReplyLog.objects.filter(result='failed').count(),
        unread_events=KuaishouEvent.objects.filter(is_read=False).count(),
    )
