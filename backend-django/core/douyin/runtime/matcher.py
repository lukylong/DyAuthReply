#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: matcher.py
@Desc: Rule Matcher - 规则匹配引擎

按 priority 降序扫描启用中的规则：
  1. contains —— keywords 任一出现在消息文本（大小写不敏感）
  2. regex    —— regex_pattern 能匹配消息文本
  3. default  —— 兜底规则（始终命中），用于其它规则都未命中时

时段/星期/渠道过滤：
  - channel: 必须与入参 channel（'dm' / 'comment'）一致，或规则为 'all'
  - weekday_mask: 7 位字符串，周一..周日，对应位为 1 才生效
  - time_window_start/end: 允许跨午夜，None 表示全天

冷却：
  matcher 本身不判断冷却，由调用方用 DouyinReplyLog 查询最近命中时间。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, time
from typing import Iterable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.douyin.douyin_rule_model import DouyinRule

logger = logging.getLogger(__name__)


def _time_in_window(now: time, start: Optional[time], end: Optional[time]) -> bool:
    if start is None or end is None:
        return True
    # 跨午夜情形：start > end，命中 [start, 24:00) ∪ [00:00, end]
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


def _rule_effective(rule: "DouyinRule", incoming_channel: str, at: datetime) -> bool:
    if not rule.status:
        return False
    if not _channel_match(rule.channel, incoming_channel):
        return False
    if not _weekday_allowed(rule.weekday_mask or '1111111', at.weekday()):
        return False
    if not _time_in_window(at.time(), rule.time_window_start, rule.time_window_end):
        return False
    return True


def _match_single(rule: "DouyinRule", text: str) -> bool:
    if rule.match_type == 'default':
        return True  # 兜底
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


def match(
    text: str,
    rules: list["DouyinRule"],
    *,
    incoming_channel: str = 'dm',
    at: Optional[datetime] = None,
) -> Optional["DouyinRule"]:
    """
    从已按 priority 降序排列的规则列表中返回第一条命中的。

    策略：先返回非 default 的命中；若所有非 default 都未命中，再返回优先级最高的 default。
    """
    if at is None:
        at = datetime.now()

    text = text or ""
    fallback: Optional["DouyinRule"] = None

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
