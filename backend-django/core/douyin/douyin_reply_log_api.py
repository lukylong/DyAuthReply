#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: douyin_reply_log_api.py
@Desc: Douyin Reply Log API - 抖音回复日志查询接口（只读）
"""
from typing import List

from django.db.models import Avg, Count, Q
from django.shortcuts import get_object_or_404
from ninja import Query, Router
from ninja.pagination import paginate

from common.fu_crud import retrieve
from common.fu_pagination import MyPagination
from core.douyin.douyin_reply_log_model import DouyinReplyLog
from core.douyin.douyin_reply_log_schema import (
    DouyinReplyLogFilters,
    DouyinReplyLogSchemaOut,
    DouyinReplyLogStatOut,
)

router = Router()


@router.get("/reply-log", response=List[DouyinReplyLogSchemaOut], summary="获取回复日志列表（分页）")
@paginate(MyPagination)
def list_reply_log(request, filters: DouyinReplyLogFilters = Query(...)):
    queryset = retrieve(request, DouyinReplyLog, filters).select_related(
        'matched_rule', 'conversation'
    )
    return queryset


@router.get("/reply-log/{log_id}", response=DouyinReplyLogSchemaOut, summary="获取回复日志详情")
def get_reply_log(request, log_id: str):
    return get_object_or_404(
        DouyinReplyLog.objects.select_related('matched_rule', 'conversation'),
        id=log_id,
    )


@router.get("/reply-log/stat/summary", response=DouyinReplyLogStatOut, summary="回复日志统计概览")
def stat_reply_log(request, account_id: str = None):
    """按结果分桶统计：total/success/failed/skipped/... + 平均耗时"""
    queryset = DouyinReplyLog.objects.all()
    if account_id:
        queryset = queryset.filter(account_id=account_id)

    counts = queryset.aggregate(
        total=Count('id'),
        success=Count('id', filter=Q(result='success')),
        failed=Count('id', filter=Q(result='failed')),
        skipped=Count('id', filter=Q(result='skipped')),
        cooldown=Count('id', filter=Q(result='cooldown')),
        quota_exceeded=Count('id', filter=Q(result='quota_exceeded')),
        silent=Count('id', filter=Q(result='silent')),
        avg_duration_ms=Avg('duration_ms'),
    )
    return DouyinReplyLogStatOut(
        total=counts['total'] or 0,
        success=counts['success'] or 0,
        failed=counts['failed'] or 0,
        skipped=counts['skipped'] or 0,
        cooldown=counts['cooldown'] or 0,
        quota_exceeded=counts['quota_exceeded'] or 0,
        silent=counts['silent'] or 0,
        avg_duration_ms=float(counts['avg_duration_ms'] or 0),
    )
