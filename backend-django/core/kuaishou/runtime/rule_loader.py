#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: core/kuaishou/runtime/rule_loader.py
@Desc: 从共用层 core.social 加载快手账号的规则与黑名单

规则/黑名单存在跨平台共用表（core_social_rule / core_social_blacklist），
按 platform='kuaishou' + account_id 软引用过滤。这正是共用层设计的价值：
快手 worker 与抖音 worker 共享同一套内容策略表，仅以 platform 维度隔离。
"""
from __future__ import annotations

from asgiref.sync import sync_to_async

from core.social.constants import PLATFORM_KUAISHOU


@sync_to_async
def load_rules_for_account(account_id: str) -> list:
    """按优先级降序返回该快手账号启用中的规则（含该平台全局规则 account_id 为空）。"""
    from django.db.models import Q

    from core.social.rule_model import ReplyRule

    return list(
        ReplyRule.objects.filter(
            Q(account_id=account_id) | Q(account_id__isnull=True),
            platform=PLATFORM_KUAISHOU,
            status=True,
            is_deleted=False,
        ).order_by('-priority', '-sys_create_datetime')
    )


@sync_to_async
def blacklist_hit(account_id: str, group_id, peer_user_id: str, peer_nickname: str, text: str):
    """命中返回原因字符串；未命中返回 None。"""
    from core.social.blacklist_model import Blacklist

    rows = Blacklist.objects.filter(
        platform=PLATFORM_KUAISHOU,
        status=True,
        is_deleted=False,
    )
    for bl in rows:
        if bl.scope == 'account' and str(bl.account_id or '') != str(account_id or ''):
            continue
        if bl.scope == 'group' and str(bl.group_id or '') != str(group_id or ''):
            continue
        if bl.blacklist_type == 'user' and bl.value == peer_user_id:
            Blacklist.objects.filter(id=bl.id).update(hit_count=bl.hit_count + 1)
            return f"用户黑名单: {peer_user_id}"
        if bl.blacklist_type == 'nickname_keyword' and bl.value and bl.value in (peer_nickname or ''):
            Blacklist.objects.filter(id=bl.id).update(hit_count=bl.hit_count + 1)
            return f"昵称黑名单: {bl.value}"
        if bl.blacklist_type == 'content_keyword' and bl.value and bl.value in (text or ''):
            Blacklist.objects.filter(id=bl.id).update(hit_count=bl.hit_count + 1)
            return f"内容黑名单: {bl.value}"
    return None
