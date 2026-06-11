#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: core/social/matcher.py
@Desc: 跨平台规则匹配引擎（平台无关）

接受规则对象列表（ReplyRule，亦兼容字段一致的 DouyinRule），返回首个命中的规则。
按 priority 降序：先返回非 default 命中；若都未命中，再返回优先级最高的 default。

时段/星期/渠道过滤、跨午夜处理与抖音原实现保持一致。冷却由调用方依据回复日志判断。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, time
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


def _time_in_window(now: time, start: Optional[time], end: Optional[time]) -> bool:
    if start is None or end is None:
        return True
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end


def _weekday_allowed(mask: str, weekday: int) -> bool:
    """weekday: Monday=0 ... Sunday=6"""
    if not mask or len(mask) != 7:
        return True
    try:
        return mask[weekday] == '1'
    except IndexError:
        return True


def _channel_match(rule_channel: str, incoming_channel: str) -> bool:
    return rule_channel == 'all' or rule_channel == incoming_channel


def _rule_effective(rule, incoming_channel: str, at: datetime) -> bool:
    if not rule.status:
        return False
    if not _channel_match(rule.channel, incoming_channel):
        return False
    if not _weekday_allowed(rule.weekday_mask or '1111111', at.weekday()):
        return False
    if not _time_in_window(at.time(), rule.time_window_start, rule.time_window_end):
        return False
    return True


def _match_single(rule, text: str) -> bool:
    if rule.match_type == 'default':
        return True
    if rule.match_type == 'contains':
        kws: Iterable[str] = rule.keywords or []
        low = text.lower()
        return any(k and k.lower() in low for k in kws)
    if rule.match_type == 'regex':
        pat = rule.regex_pattern
        if not pat:
            return False
        try:
            return bool(re.search(pat, text, flags=re.IGNORECASE | re.DOTALL))
        except re.error as e:
            logger.warning(f"[matcher] 规则 {rule.id} 正则错误: {e}")
            return False
    return False


def match(text: str, rules: list, *, incoming_channel: str = 'dm', at: Optional[datetime] = None):
    """从已按 priority 降序排列的规则列表中返回第一条命中的规则（无命中返回 None）。"""
    if at is None:
        at = datetime.now()

    text = text or ""
    fallback = None

    for rule in rules:
        if not _rule_effective(rule, incoming_channel, at):
            continue
        if rule.match_type == 'default':
            if fallback is None:
                fallback = rule
            continue
        if _match_single(rule, text):
            return rule
    return fallback
