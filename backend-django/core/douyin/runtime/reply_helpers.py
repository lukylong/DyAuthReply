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
def _normalize_send_mode(send_mode: str | None, *, has_links: bool) -> str:
    mode = (send_mode or '').strip() or 'multi_message'
    if mode == 'card_fallback':
        mode = 'multi_message'
    # 含链接卡片时一律分条发送（文本一条 + 每条链接各一条）
    if has_links:
        mode = 'multi_message'
    return mode


def _resolve_rule_reply_fields(rule: "DouyinRule") -> tuple[str, list, str | None]:
    """合并 rule 与绑定 template 的文案/链接/发送模式（rule 字段优先）。"""
    template = getattr(rule, 'template', None)
    rule_links = rule.links or []
    rule_text = (rule.reply_text or '').strip()
    if template is not None:
        base = rule_text or (template.content or '')
        links = rule_links if rule_links else (template.links or [])
        raw_mode = (rule.send_mode or '').strip() or template.send_mode
    else:
        base = rule.reply_text or ''
        links = rule.links or []
        raw_mode = rule.send_mode
    return base, links, raw_mode


def _build_segments(rule: "DouyinRule", peer_nickname: str, *, card_urls: Optional[list[str]] = None) -> list[str]:
    """
    生成需要发送的消息段列表。
    优先级：rule.template.content > rule.reply_text；rule.template.links > rule.links

    multi_message：先文本，再每条链接各发一条独立消息（与主项目 sender 行为一致）。
    links[].title 仅用于后台展示/占位符 {{link_N_title}}，发送时一律忽略，只发 url。

    card_urls：规则关联的伪装卡片落地页 URL 列表（由调用方在 async 上下文用
    resolve_card_landing_urls() 预取后传入，本函数保持纯同步、不触 DB）。
    卡片段排在文案之后、links 之前，各占一条独立消息（抖音自动渲染为卡片）。
    """
    base, links, raw_mode = _resolve_rule_reply_fields(rule)

    normalized_links: list[dict] = []
    for lk in links or []:
        if isinstance(lk, dict):
            url = str(lk.get('url') or '').strip()
            if url:
                normalized_links.append({
                    'title': str(lk.get('title') or '').strip(),
                    'url': url,
                })
        elif isinstance(lk, str) and lk.strip():
            url = lk.strip()
            normalized_links.append({'title': '', 'url': url})

    card_segs = [u for u in (card_urls or []) if isinstance(u, str) and u.strip()]
    has_extra = bool(normalized_links) or bool(card_segs)
    send_mode = _normalize_send_mode(raw_mode, has_links=has_extra)
    text = render_template(base, peer_nickname=peer_nickname, links=normalized_links)
    urls = [lk['url'] for lk in normalized_links]

    if send_mode == 'merged':
        merged = text
        for u in card_segs:
            merged += ('\n' if merged and not merged.endswith('\n') else '') + u
        for u in urls:
            merged += ('\n' if merged and not merged.endswith('\n') else '') + u
        return [merged] if merged.strip() else []

    segs: list[str] = []
    if text.strip():
        segs.append(text.strip())
    # 卡片段：发落地页 URL 本体，抖音抓 og 渲染为卡片气泡
    for u in card_segs:
        segs.append(u.strip())
    for lk in normalized_links:
        # 链接段发 URL 本体，抖音才会识别为可点击链接；title 仅用于后台展示/模板变量
        segs.append(lk['url'])
    return segs


def resolve_card_landing_urls(rule: "DouyinRule") -> list[str]:
    """[同步] 把 rule.card_ids 解析为启用卡片的落地页 URL 列表，保持 card_ids 顺序。

    在 async 上下文调用方需用 sync_to_async 包装本函数后再把结果传给 _build_segments。
    停用 / 已删除 / 不存在的卡片自动跳过。
    """
    card_ids = [str(x) for x in (getattr(rule, 'card_ids', None) or [])]
    if not card_ids:
        return []
    from core.douyin.douyin_card_model import DouyinCard
    from core.douyin.douyin_card_schema import build_landing_url

    enabled = {
        str(c.id)
        for c in DouyinCard.objects.filter(id__in=card_ids, status=True, is_deleted=False).only('id')
    }
    # 保持 card_ids 原始顺序，过滤掉未启用/缺失的
    return [build_landing_url(cid) for cid in card_ids if cid in enabled]


# -------------------- DB 落库 --------------------
@sync_to_async
def write_manual_out_message(
    account_id: str,
    conversation_id: str,
    text: str,
    *,
    external_msg_id: Optional[str] = None,
) -> str:
    from core.douyin.douyin_conversation_model import DouyinConversation
    from core.douyin.douyin_message_model import DouyinMessage

    conv = DouyinConversation.objects.get(id=conversation_id, account_id=account_id)
    ext_id = (external_msg_id or '').strip() or f"manual_out_{timezone.now().timestamp()}"
    now = timezone.now()
    msg, _created = DouyinMessage.objects.get_or_create(
        conversation=conv,
        external_msg_id=ext_id,
        defaults={
            'direction': 'out',
            'content_type': 'text',
            'content': text,
            'raw_payload': {'manual': True},
            'received_at': now,
            'processed': True,
        },
    )
    conv.last_message_at = now
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
    external_msg_id: Optional[str] = None,
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
    ext_id = (external_msg_id or '').strip() or f"auto_out_{int(now.timestamp() * 1000)}"
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
