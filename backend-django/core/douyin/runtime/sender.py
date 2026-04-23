#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: sender.py
@Desc: Message Sender - 私信发送

职责：
  1. 渲染模板变量（{{nickname}} {{time_greeting}} {{link_i}}）
  2. 按 send_mode 分解为 N 条"要发送的文本段"
  3. 在当前会话输入框里逐条输入并点发送，带拟人化延迟

send_mode：
  multi_message  : 第一条文本；其后每条链接单独一条
  merged         : 文本 + 链接 拼成一条长文（抖音会自动识别 URL）
  card_fallback  : 目前等价 multi_message（真卡片接口需要商家资质，Demo 暂不支持）

前置条件：
  调用前已打开会话（通常是 inbox 扫描刚点进去的那一个），当前 page 处在会话视图。
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from asgiref.sync import sync_to_async
from django.utils import timezone

from core.douyin.runtime import selectors as S
from core.douyin.runtime.humanize import human_click, human_type, random_sleep

if TYPE_CHECKING:
    from core.douyin.douyin_account_model import DouyinAccount
    from core.douyin.douyin_rule_model import DouyinRule
    from core.douyin.douyin_message_model import DouyinMessage

logger = logging.getLogger(__name__)


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
    优先级：rule.template.content > rule.reply_text
            rule.template.links  > rule.links
    """
    template = getattr(rule, 'template', None)
    if template is not None:
        base = template.content or ''
        links = template.links or []
        send_mode = rule.send_mode or template.send_mode
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

    # multi_message 或 card_fallback（暂退化为 multi_message）
    segs: list[str] = []
    if text.strip():
        segs.append(text)
    segs.extend(urls)
    return segs


# -------------------- Playwright 发送 --------------------
async def _locate_input(page):
    for sel in S.IM_INPUT_BOX:
        try:
            loc = page.locator(sel).first
            if await loc.count():
                return loc
        except Exception:
            continue
    return None


async def _locate_send_btn(page):
    for sel in S.IM_SEND_BUTTON:
        try:
            loc = page.locator(sel).first
            if await loc.count():
                return loc
        except Exception:
            continue
    return None


async def _send_one(page, text: str) -> None:
    """在当前会话输入一条消息并点击发送按钮"""
    inp = await _locate_input(page)
    if inp is None:
        raise RuntimeError("未找到输入框")

    await human_type(inp, text)
    await random_sleep(0.3, 0.8)

    btn = await _locate_send_btn(page)
    if btn is not None:
        await human_click(btn)
    else:
        # 兜底：Enter 发送
        try:
            await page.keyboard.press("Enter")
        except Exception:
            raise RuntimeError("未找到发送按钮且 Enter 发送失败")


# -------------------- DB 落库 --------------------
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


# -------------------- 主入口 --------------------
async def send_reply(
    account: "DouyinAccount",
    page,
    *,
    conversation_id: str,
    trigger_message_id: str,
    rule: "DouyinRule",
    peer_nickname: str = '',
) -> str:
    """
    发送自动回复。调用前应已打开对应会话（page 处在会话视图）。

    Returns:
        DouyinReplyLog.id
    """
    t0 = datetime.utcnow().timestamp()
    segments = _build_segments(rule, peer_nickname)
    if not segments:
        log_id = await _write_reply_log(
            account_id=str(account.id),
            conversation_id=conversation_id,
            trigger_message_id=trigger_message_id,
            rule_id=str(rule.id),
            text='',
            links=[],
            result='skipped',
            error_message='规则渲染结果为空',
        )
        return log_id

    try:
        for i, seg in enumerate(segments):
            logger.info(f"[sender] 发送段 {i+1}/{len(segments)} len={len(seg)} account={account.id}")
            await _send_one(page, seg)
            # 随机间隔 1~3 秒
            if i < len(segments) - 1:
                import random
                await asyncio.sleep(random.uniform(1.0, 3.0))

        duration = int((datetime.utcnow().timestamp() - t0) * 1000)
        links_payload = []
        tpl = getattr(rule, 'template', None)
        links_payload = (tpl.links if tpl else rule.links) or []

        first_text = segments[0]
        return await _write_reply_log(
            account_id=str(account.id),
            conversation_id=conversation_id,
            trigger_message_id=trigger_message_id,
            rule_id=str(rule.id),
            text=first_text,
            links=links_payload,
            result='success',
            duration_ms=duration,
        )
    except Exception as e:  # noqa: BLE001
        duration = int((datetime.utcnow().timestamp() - t0) * 1000)
        logger.exception(f"[sender] 发送失败 account={account.id} err={e}")
        return await _write_reply_log(
            account_id=str(account.id),
            conversation_id=conversation_id,
            trigger_message_id=trigger_message_id,
            rule_id=str(rule.id),
            text=segments[0] if segments else '',
            links=[],
            result='failed',
            error_message=f'{type(e).__name__}: {e}',
            duration_ms=duration,
        )
