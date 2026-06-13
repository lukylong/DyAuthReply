#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: runtime/reply_helpers.py
@Desc: 回复内容渲染 + 出向消息/回复日志落库 —— 中性模块，不依赖浏览器/Playwright。

历史上这些函数定义在 sender.py（DOM 发送模块，import humanize/selectors）里，被纯协议
发送路径（http_protocol / dual_run）共用。脱浏览器后这些消费方不应再 import sender.py，
故把模板渲染与 DB 落库下沉到本模块。sender.py 仍 re-export 以兼容其内部 DOM 代码。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from asgiref.sync import sync_to_async
from django.utils import timezone

if TYPE_CHECKING:
    from core.douyin.douyin_rule_model import DouyinRule


# -------------------- 模板渲染 --------------------
def _time_greeting(now: Optional[datetime] = None) -> str:
    h = (now or datetime.now()).hour
    if 5 <= h < 11:
        return "早上好"
    if 11 <= h < 14:
        return "中午好"
    if 14 <= h < 18:
        return "下午好"
    if 18 <= h < 23:
        return "晚上好"
    return "夜深了"


def render_template(content: str, *, peer_nickname: str = '', links: Optional[list] = None, extra: Optional[dict] = None) -> str:
    """替换 {{var}} 变量"""
    ctx = {
        'nickname': peer_nickname or '',
        'peer_nickname': peer_nickname or '',
        'time_greeting': _time_greeting(),
    }
    if extra:
        ctx.update(extra)
    for i, lk in enumerate(links or [], start=1):
        if isinstance(lk, dict):
            ctx[f'link_{i}'] = lk.get('url', '')
            ctx[f'link_{i}_title'] = lk.get('title', '')
        else:
            ctx[f'link_{i}'] = str(lk)

    def _sub(m: re.Match) -> str:
        key = m.group(1).strip()
        return str(ctx.get(key, m.group(0)))

    return re.sub(r'\{\{\s*(\w+)\s*\}\}', _sub, content or '')


# -------------------- 消息拆分 --------------------
def _build_segments(rule: "DouyinRule", peer_nickname: str) -> list[str]:
    """
    生成需要发送的消息段列表。
    优先级：rule.template.content > rule.reply_text；rule.template.links > rule.links
    """
    template = getattr(rule, 'template', None)
    if template is not None:
        base = template.content or ''
        links = template.links or []
        send_mode = template.send_mode
    else:
        base = rule.reply_text or ''
        links = rule.links or []
        send_mode = rule.send_mode

    text = render_template(base, peer_nickname=peer_nickname, links=links)
    urls = [lk.get('url') if isinstance(lk, dict) else str(lk) for lk in links]
    urls = [u for u in urls if u]

    if send_mode == 'merged':
        merged = text
        for u in urls:
            merged += ('\n' if merged and not merged.endswith('\n') else '') + u
        return [merged] if merged.strip() else []

    segs: list[str] = []
    if text.strip():
        segs.append(text)
    segs.extend(urls)
    return segs


# -------------------- DB 落库 --------------------
@sync_to_async
def write_manual_out_message(
    account_id: str,
    conversation_id: str,
    text: str,
) -> str:
    from core.douyin.douyin_conversation_model import DouyinConversation
    from core.douyin.douyin_message_model import DouyinMessage

    conv = DouyinConversation.objects.get(id=conversation_id, account_id=account_id)
    msg = DouyinMessage.objects.create(
        conversation=conv,
        external_msg_id=f"manual_out_{timezone.now().timestamp()}",
        direction='out',
        content_type='text',
        content=text,
        raw_payload={'manual': True},
        received_at=timezone.now(),
        processed=True,
    )
    conv.last_message_at = timezone.now()
    conv.last_message_preview = text[:200]
    conv.save(update_fields=['last_message_at', 'last_message_preview', 'sys_update_datetime'])
    return str(msg.id)


@sync_to_async
def _record_auto_outbound_message(
    account_id: str,
    conversation_id: str,
    text: str,
    *,
    rule_id: Optional[str] = None,
) -> Optional[str]:
    """
    自动回复发送成功后，落一条 direction='out' 的 DouyinMessage 行。
    用作下一轮 scan_inbox 的“自己刚发过”回声黑名单的真源之一。
    """
    from core.douyin.douyin_conversation_model import DouyinConversation
    from core.douyin.douyin_message_model import DouyinMessage

    conv = DouyinConversation.objects.filter(id=conversation_id, account_id=account_id).first()
    if conv is None:
        return None
    now = timezone.now()
    ext_id = f"auto_out_{int(now.timestamp() * 1000)}"
    msg, _ = DouyinMessage.objects.get_or_create(
        conversation=conv,
        external_msg_id=ext_id,
        defaults={
            'direction': 'out',
            'content_type': 'text',
            'content': text,
            'raw_payload': {'auto': True, 'rule_id': rule_id or ''},
            'received_at': now,
            'processed': True,
        },
    )
    conv.last_message_at = now
    conv.last_message_preview = (text or '')[:200]
    try:
        conv.save(update_fields=['last_message_at', 'last_message_preview', 'sys_update_datetime'])
    except Exception:  # noqa: BLE001
        conv.save(update_fields=['last_message_at', 'last_message_preview'])
    return str(msg.id)


@sync_to_async
def _write_reply_log(
    account_id: str,
    conversation_id: str,
    trigger_message_id: str,
    rule_id: str,
    text: str,
    links: list,
    result: str,
    error_message: str = '',
    duration_ms: int = 0,
) -> str:
    from django.db.models import F
    from core.douyin.douyin_account_model import DouyinAccount
    from core.douyin.douyin_message_model import DouyinMessage
    from core.douyin.douyin_reply_log_model import DouyinReplyLog
    from core.douyin.douyin_rule_model import DouyinRule

    log = DouyinReplyLog.objects.create(
        account_id=account_id,
        conversation_id=conversation_id,
        trigger_message_id=trigger_message_id,
        matched_rule_id=rule_id,
        reply_text=text,
        reply_links=links,
        result=result,
        error_message=error_message or None,
        duration_ms=duration_ms,
        sent_at=timezone.now() if result == 'success' else None,
    )

    if result == 'success':
        DouyinRule.objects.filter(id=rule_id).update(hit_count=F('hit_count') + 1)
        DouyinAccount.objects.filter(id=account_id).update(reply_today=F('reply_today') + 1)
        DouyinMessage.objects.filter(id=trigger_message_id).update(processed=True)
    elif result in ('skipped', 'cooldown', 'quota_exceeded', 'silent'):
        DouyinMessage.objects.filter(id=trigger_message_id).update(processed=True)

    return str(log.id)
