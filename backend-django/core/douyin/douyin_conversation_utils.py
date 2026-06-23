# -*- coding: utf-8 -*-
"""会话列表查询：去重 + 分页（私信工作台）。"""
from __future__ import annotations

from django.db.models import Q

from core.douyin.douyin_conversation_model import DouyinConversation

# 去重前最多扫描行数（防止极端账号拖垮内存）
_MAX_SCAN_ROWS = 3000
_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 100


def dedupe_conversation_rows(rows: list) -> list:
    """按 peer_sec_uid / fallback 昵称去重，保留排序靠前（较新）的一条。"""
    deduped: list = []
    seen: set[str] = set()
    for row in rows:
        if row.peer_sec_uid.startswith('fallback_') and row.peer_nickname:
            key = f"nick:{row.peer_nickname}"
        else:
            key = f"uid:{row.peer_sec_uid}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def paginate_account_conversations(
    account_id,
    *,
    page: int = 1,
    page_size: int = _DEFAULT_PAGE_SIZE,
    keyword: str = '',
) -> tuple[list, int, bool]:
    """返回 (当前页 rows, total, has_more)。"""
    page = max(1, int(page or 1))
    page_size = min(max(1, int(page_size or _DEFAULT_PAGE_SIZE)), _MAX_PAGE_SIZE)

    qs = DouyinConversation.objects.filter(account_id=account_id)
    kw = (keyword or '').strip()
    if kw:
        qs = qs.filter(
            Q(peer_nickname__icontains=kw)
            | Q(peer_unique_id__icontains=kw)
            | Q(peer_sec_uid__icontains=kw)
        )

    rows = list(
        qs.order_by('-last_message_at', '-sys_create_datetime')[:_MAX_SCAN_ROWS]
    )
    deduped = dedupe_conversation_rows(rows)
    total = len(deduped)
    start = (page - 1) * page_size
    end = start + page_size
    items = deduped[start:end]
    has_more = end < total
    return items, total, has_more
