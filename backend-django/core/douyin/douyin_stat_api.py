#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音看板 API + 多账号批量导入"""
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import List

from django.db.models import Count, Sum
from django.utils import timezone
from ninja import Query, Router
from ninja.errors import HttpError

from core.douyin.douyin_account_model import DouyinAccount
from core.douyin.douyin_daily_stat_model import DouyinDailyStat
from core.douyin.douyin_event_model import DouyinEvent
from core.douyin.douyin_message_model import DouyinMessage
from core.douyin.douyin_reply_log_model import DouyinReplyLog
from core.douyin.douyin_rule_model import DouyinRule
from core.douyin.douyin_session_model import DouyinSession
from core.douyin.douyin_stat_schema import (
    AccountBatchImportIn,
    AccountBatchImportOut,
    DouyinDashboardAccountRankItem,
    DouyinDashboardOverviewOut,
    DouyinDashboardRankOut,
    DouyinDashboardRuleHitItem,
    DouyinDashboardRuleHitsOut,
    DouyinDashboardTrendOut,
    DouyinDashboardTrendPoint,
    DouyinStatQuery,
)

router = Router()


@router.get("/dashboard/overview", response=DouyinDashboardOverviewOut, summary="看板：今日概览")
def dashboard_overview(request):
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    account_qs = DouyinAccount.objects.filter(is_deleted=False)
    msg_today = DouyinMessage.objects.filter(received_at__gte=today_start, direction='in').count()
    reply_today = DouyinReplyLog.objects.filter(sent_at__gte=today_start, result='success').count()
    reply_failed = DouyinReplyLog.objects.filter(sent_at__gte=today_start, result='failed').count()
    events_err = DouyinEvent.objects.filter(occurred_at__gte=today_start, level__in=['error', 'critical']).count()

    # 平均响应
    avg_ms = DouyinReplyLog.objects.filter(
        sent_at__gte=today_start, result='success',
    ).aggregate(v=Sum('duration_ms'))['v']
    reply_cnt = DouyinReplyLog.objects.filter(sent_at__gte=today_start, result='success').count()
    avg_response_ms = int((avg_ms or 0) / reply_cnt) if reply_cnt else 0

    return DouyinDashboardOverviewOut(
        accounts_total=account_qs.count(),
        accounts_online=account_qs.filter(status=1).count(),
        accounts_offline=account_qs.exclude(status=1).count(),
        sessions_running=DouyinSession.objects.filter(status='running').count(),
        messages_today=msg_today,
        replies_today=reply_today,
        replies_failed_today=reply_failed,
        events_error_today=events_err,
        avg_response_ms=avg_response_ms,
    )


@router.get("/dashboard/trend", response=DouyinDashboardTrendOut, summary="看板：近 N 日趋势")
def dashboard_trend(request, query: DouyinStatQuery = Query(...)):
    # settings.USE_TZ=False 时 timezone.localdate() 会抛 ValueError（naive datetime），
    # 统一改用 timezone.now().date() 以兼容两种模式。
    end = query.end_date or timezone.now().date()
    start = query.start_date or (end - timedelta(days=6))
    qs = DouyinDailyStat.objects.filter(stat_date__gte=start, stat_date__lte=end)
    if query.account_id:
        qs = qs.filter(account_id=query.account_id)
    if query.group_id:
        qs = qs.filter(account__group_id=query.group_id)

    bucket: dict = defaultdict(lambda: {"messages_received": 0, "replies_sent": 0, "replies_failed": 0})
    for r in qs:
        b = bucket[r.stat_date]
        b['messages_received'] += r.messages_received
        b['replies_sent'] += r.replies_sent
        b['replies_failed'] += r.replies_failed

    points: List[DouyinDashboardTrendPoint] = []
    cur = start
    while cur <= end:
        v = bucket.get(cur, {"messages_received": 0, "replies_sent": 0, "replies_failed": 0})
        points.append(DouyinDashboardTrendPoint(stat_date=cur, **v))
        cur = cur + timedelta(days=1)
    return DouyinDashboardTrendOut(points=points)


@router.get("/dashboard/account-rank", response=DouyinDashboardRankOut, summary="看板：账号回复排行")
def dashboard_account_rank(request, query: DouyinStatQuery = Query(...)):
    end = query.end_date or timezone.now().date()
    start = query.start_date or (end - timedelta(days=6))
    qs = DouyinDailyStat.objects.filter(stat_date__gte=start, stat_date__lte=end)
    if query.group_id:
        qs = qs.filter(account__group_id=query.group_id)

    agg = (
        qs.values('account_id')
          .annotate(
              replies=Sum('replies_sent'),
              messages=Sum('messages_received'),
              avg_ms=Sum('avg_response_ms'),
          )
          .order_by('-replies')[:20]
    )
    account_map = {str(a.id): a for a in DouyinAccount.objects.filter(id__in=[r['account_id'] for r in agg])}
    items = []
    for r in agg:
        acc = account_map.get(str(r['account_id']))
        items.append(DouyinDashboardAccountRankItem(
            account_id=str(r['account_id']),
            nickname=acc.nickname if acc else '(已删除)',
            replies=r['replies'] or 0,
            messages=r['messages'] or 0,
            response_ms=r['avg_ms'] or 0,
        ))
    return DouyinDashboardRankOut(items=items)


@router.get("/dashboard/rule-hits", response=DouyinDashboardRuleHitsOut, summary="看板：规则命中分布")
def dashboard_rule_hits(request):
    rules = DouyinRule.objects.filter(is_deleted=False).order_by('-hit_count')[:20]
    items = [
        DouyinDashboardRuleHitItem(
            rule_id=str(r.id), rule_name=r.name, hit_count=r.hit_count,
        ) for r in rules
    ]
    return DouyinDashboardRuleHitsOut(items=items)


# ========== 批量导入账号 ==========

@router.post("/account/batch/import", response=AccountBatchImportOut, summary="批量导入抖音账号")
def batch_import_account(request, data: AccountBatchImportIn):
    created = 0
    skipped = 0
    failed: list = []
    owner_id = request.auth.id
    for idx, item in enumerate(data.items):
        try:
            exists = DouyinAccount.objects.filter(
                nickname=item.nickname, owner_id=owner_id, is_deleted=False,
            ).exists()
            if exists:
                if data.skip_duplicate:
                    skipped += 1
                    continue
                raise HttpError(400, f"昵称 {item.nickname} 已存在")
            DouyinAccount.objects.create(
                nickname=item.nickname,
                owner_id=owner_id,
                group_id=item.group_id,
                remark=item.remark,
                daily_reply_quota=item.daily_reply_quota or 200,
                min_interval_seconds=item.min_interval_seconds or 8,
                max_interval_seconds=item.max_interval_seconds or 25,
                work_mode=item.work_mode or 'auto',
                priority=item.priority or 0,
                tags=item.tags or [],
                proxy_url=item.proxy_url,
                sys_creator_id=owner_id,
            )
            created += 1
        except Exception as e:  # noqa: BLE001
            failed.append({"index": idx, "nickname": item.nickname, "error": str(e)})

    return AccountBatchImportOut(
        created_count=created,
        skipped_count=skipped,
        failed_items=failed,
    )
