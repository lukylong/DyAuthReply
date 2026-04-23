#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音统计看板 Schema"""
from datetime import date, datetime
from typing import List, Optional

from ninja import Field, Schema


class DouyinDashboardOverviewOut(Schema):
    """首页概览（今日实时）"""
    accounts_total: int
    accounts_online: int
    accounts_offline: int
    sessions_running: int
    messages_today: int
    replies_today: int
    replies_failed_today: int
    events_error_today: int
    avg_response_ms: int = 0


class DouyinDashboardTrendPoint(Schema):
    stat_date: date
    messages_received: int = 0
    replies_sent: int = 0
    replies_failed: int = 0


class DouyinDashboardTrendOut(Schema):
    points: List[DouyinDashboardTrendPoint]


class DouyinDashboardAccountRankItem(Schema):
    account_id: str
    nickname: str
    replies: int
    messages: int
    response_ms: int = 0


class DouyinDashboardRankOut(Schema):
    items: List[DouyinDashboardAccountRankItem]


class DouyinDashboardRuleHitItem(Schema):
    rule_id: str
    rule_name: Optional[str] = None
    hit_count: int


class DouyinDashboardRuleHitsOut(Schema):
    items: List[DouyinDashboardRuleHitItem]


class DouyinStatQuery(Schema):
    start_date: Optional[date] = Field(None, description="默认近 7 日")
    end_date: Optional[date] = None
    account_id: Optional[str] = None
    group_id: Optional[str] = None


# ---------- 批量导入账号 ----------
class AccountImportItem(Schema):
    nickname: str
    group_id: Optional[str] = None
    remark: Optional[str] = None
    daily_reply_quota: Optional[int] = 200
    min_interval_seconds: Optional[int] = 8
    max_interval_seconds: Optional[int] = 25
    work_mode: Optional[str] = 'auto'
    priority: Optional[int] = 0
    tags: Optional[List[str]] = []
    proxy_url: Optional[str] = None


class AccountBatchImportIn(Schema):
    items: List[AccountImportItem]
    skip_duplicate: bool = True


class AccountBatchImportOut(Schema):
    created_count: int
    skipped_count: int
    failed_items: List[dict] = []
