#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: inbox.py
@Desc: Inbox Scanner - 私信收件箱扫描

策略（DOM 扫描，最稳但可能慢）：
  1. 打开 /im 私信中心
  2. 遍历会话列表，读取每个会话的 nickname / last_message / unread_badge
  3. 若未读数 > 0 或 last_message 变化：点击进入该会话
  4. 读取会话面板内最近 N 条消息气泡，抽取 direction / text
  5. 按 (conversation, external_msg_id) upsert DouyinMessage；
     对方发来的 direction='in' 且未处理的消息会返回给 worker 做后续回复

external_msg_id 生成规则（抖音没暴露原生 msg_id）：
  sha1(peer_sec_uid + received_at.iso + content[:60])[:32]

返回 (List[ScannedMessage]): 本次扫描新增的入向消息（已入库）
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

from asgiref.sync import sync_to_async
from django.utils import timezone

from core.douyin.runtime import selectors as S
from core.douyin.runtime.browser import BrowserManager
from core.douyin.runtime.humanize import human_click, random_sleep

if TYPE_CHECKING:
    from core.douyin.douyin_account_model import DouyinAccount

logger = logging.getLogger(__name__)


async def _looks_like_login_gate(page) -> bool:
    try:
        text = await page.evaluate("() => (document.body?.innerText || '').slice(0, 2000)")
        text = (text or "").replace("\n", "")
        hints = ("扫码登录", "验证码登录", "登录/注册", "创作者登录", "我是创作者")
        return any(h in text for h in hints)
    except Exception:
        return False


# -------------------- 数据结构 --------------------
@dataclass
class ScannedMessage:
    """本次扫描新增的入向消息（供 worker 后续处理）"""
    message_id: str           # DouyinMessage.id
    conversation_id: str      # DouyinConversation.id
    peer_sec_uid: str
    peer_nickname: Optional[str]
    text: str
    received_at: str          # ISO
    raw: dict = field(default_factory=dict)


# -------------------- DB 辅助 --------------------
def _hash_msg_id(peer_sec_uid: str, received_at_iso: str, content: str) -> str:
    h = hashlib.sha1()
    h.update((peer_sec_uid + '|' + received_at_iso + '|' + (content or '')[:60]).encode('utf-8'))
    return h.hexdigest()[:32]


@sync_to_async
def _upsert_conversation_and_message(
    account_id: str,
    peer_sec_uid: str,
    peer_nickname: Optional[str],
    text: str,
    received_at,
    raw: dict,
) -> Optional[tuple]:
    """
    upsert DouyinConversation + DouyinMessage。
    返回 (conversation_id, message_id) 表示新增了一条入向消息；返回 None 表示重复跳过。
    """
    from core.douyin.douyin_account_model import DouyinAccount
    from core.douyin.douyin_conversation_model import DouyinConversation
    from core.douyin.douyin_message_model import DouyinMessage

    account = DouyinAccount.objects.filter(id=account_id).first()
    if account is None:
        return None

    conv, _ = DouyinConversation.objects.update_or_create(
        account=account,
        peer_sec_uid=peer_sec_uid,
        defaults={
            'peer_nickname': peer_nickname or '',
            'last_message_at': received_at,
            'last_message_preview': (text or '')[:200],
        },
    )

    ext_id = _hash_msg_id(peer_sec_uid, received_at.isoformat(), text)
    msg, created = DouyinMessage.objects.get_or_create(
        conversation=conv,
        external_msg_id=ext_id,
        defaults={
            'direction': 'in',
            'content_type': 'text',
            'content': text or '',
            'raw_payload': raw,
            'received_at': received_at,
            'processed': False,
        },
    )
    return (str(conv.id), str(msg.id)) if created else None


# -------------------- Playwright 抽取 --------------------
async def _list_conversation_items(page, max_items: int = 30) -> list:
    """返回私信会话列表的 Locator handles"""
    for sel in S.IM_CONVERSATION_ITEMS:
        try:
            loc = page.locator(sel)
            count = await loc.count()
            if count > 0:
                take = min(count, max_items)
                return [loc.nth(i) for i in range(take)]
        except Exception:
            continue
    return []


async def _read_nested_text(item, selectors: list[str]) -> Optional[str]:
    for sel in selectors:
        try:
            loc = item.locator(sel).first
            if await loc.count():
                return (await loc.inner_text()).strip()
        except Exception:
            continue
    return None


async def _get_unread(item) -> int:
    for sel in S.IM_CONV_UNREAD_BADGE:
        try:
            loc = item.locator(sel).first
            if await loc.count():
                txt = (await loc.inner_text()).strip()
                if txt.isdigit():
                    return int(txt)
                if txt:
                    return 99  # "99+"
        except Exception:
            continue
    return 0


async def _extract_peer_sec_uid(page) -> Optional[str]:
    """
    抖音会话 URL 形如 .../im?conversation_id=xxx&sec_uid=xxx
    或在当前会话面板的 data-* 属性里能提取；做多种兜底。
    """
    try:
        url = page.url
        import re
        m = re.search(r'sec_uid=([^&]+)', url)
        if m:
            return m.group(1)
        m = re.search(r'conversation_id=(\d+_\d+_\d+)', url)
        if m:
            # 形如 "self_sec_peer_sec" 的复合 id，取后半段
            parts = m.group(1).split('_')
            if len(parts) >= 2:
                return parts[-1]
    except Exception:
        pass
    return None


async def _read_conversation_messages(page, limit: int = 5) -> list[dict]:
    """
    读取当前打开会话的最近 `limit` 条消息。
    返回 [{direction, text, ts}]，ts 留空由外层补充。
    """
    results: list[dict] = []
    for sel in S.IM_MESSAGE_BUBBLES:
        try:
            loc = page.locator(sel)
            count = await loc.count()
            if count == 0:
                continue
            take_from = max(0, count - limit)
            for i in range(take_from, count):
                bubble = loc.nth(i)
                # 方向
                cls = (await bubble.get_attribute('class') or '').lower()
                is_self = any(h in cls for h in S.IM_MESSAGE_SELF_HINT)
                # 文本
                text: Optional[str] = None
                for tsel in S.IM_MESSAGE_TEXT:
                    try:
                        tl = bubble.locator(tsel).first
                        if await tl.count():
                            text = (await tl.inner_text()).strip()
                            if text:
                                break
                    except Exception:
                        continue
                if text is None:
                    try:
                        text = (await bubble.inner_text()).strip()
                    except Exception:
                        text = ''
                results.append({'direction': 'out' if is_self else 'in', 'text': text})
            return results
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[inbox] 读取消息气泡失败 sel={sel} err={e}")
            continue
    return results


# -------------------- 主入口 --------------------
async def scan_inbox(account: "DouyinAccount", *, max_conversations: int = 15) -> List[ScannedMessage]:
    """
    扫描一次收件箱，返回"本轮新增的入向消息"列表。

    - 仅扫描有未读角标的会话，避免对所有会话逐个点击（降低被风控风险）
    - 若全无未读则直接返回 []
    """
    account_id = str(account.id)
    logger.info(
        f"[inbox] ▶ 开始扫描 account={account_id} nickname={account.nickname!r} "
        f"max_conversations={max_conversations}"
    )
    context = await BrowserManager.get_or_create_context(account)
    page = await context.new_page()
    new_messages: list[ScannedMessage] = []
    try:
        logger.info(f"[inbox] 打开 IM 页面 account={account_id} url={S.CREATOR_IM}")
        await page.goto(S.CREATOR_IM, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2.5)
        logger.info(f"[inbox] 页面加载完成 account={account_id} current_url={page.url}")

        if await _looks_like_login_gate(page):
            logger.warning(
                f"[inbox] IM 页面显示登录门面，跳过本轮扫描（不立即判失效） "
                f"account={account_id} url={page.url}"
            )
            return new_messages

        items = await _list_conversation_items(page, max_items=max_conversations)
        logger.info(f"[inbox] 会话列表项数 account={account_id} count={len(items)}")
        if not items:
            logger.info(f"[inbox] 未发现会话项，结束本轮扫描 account={account_id}")
            return new_messages

        unread_total = 0
        for idx, item in enumerate(items):
            try:
                unread = await _get_unread(item)
                if unread <= 0:
                    continue
                unread_total += 1

                nickname = await _read_nested_text(item, S.IM_CONV_NICKNAME) or ''
                preview = await _read_nested_text(item, S.IM_CONV_LAST_MESSAGE) or ''
                logger.info(
                    f"[inbox] 发现未读会话 #{idx} account={account_id} "
                    f"nickname={nickname!r} unread={unread} preview={preview[:40]!r}"
                )

                await human_click(item)
                await random_sleep(0.8, 1.5)
                logger.info(f"[inbox] 已进入会话 nickname={nickname!r} url={page.url}")

                peer_sec_uid = await _extract_peer_sec_uid(page) or f"unknown_{idx}"
                bubbles = await _read_conversation_messages(page, limit=max(unread, 1))
                inbound = [b for b in bubbles if b['direction'] == 'in']
                logger.info(
                    f"[inbox] 采集消息气泡 nickname={nickname!r} "
                    f"total={len(bubbles)} inbound={len(inbound)} peer_sec_uid={peer_sec_uid}"
                )
                now = timezone.now()

                picks = inbound[-unread:] if unread <= len(inbound) else inbound
                for b in picks:
                    text = b.get('text') or ''
                    if not text:
                        continue
                    result = await _upsert_conversation_and_message(
                        account_id=account_id,
                        peer_sec_uid=peer_sec_uid,
                        peer_nickname=nickname,
                        text=text,
                        received_at=now,
                        raw={'preview': preview, 'unread': unread, 'scan_idx': idx},
                    )
                    if result is None:
                        logger.debug(
                            f"[inbox] 消息已存在，跳过 nickname={nickname!r} text={text[:40]!r}"
                        )
                        continue
                    conv_id, msg_id = result
                    logger.info(
                        f"[inbox] ✔ 新入向消息 account={account_id} nickname={nickname!r} "
                        f"msg_id={msg_id} text={text[:60]!r}"
                    )
                    new_messages.append(ScannedMessage(
                        message_id=msg_id,
                        conversation_id=conv_id,
                        peer_sec_uid=peer_sec_uid,
                        peer_nickname=nickname,
                        text=text,
                        received_at=now.isoformat(),
                        raw={'preview': preview, 'bubble_direction': b['direction']},
                    ))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[inbox] 处理会话 #{idx} 失败 account={account_id}: {e}")
                continue

        logger.info(
            f"[inbox] ✔ 扫描完成 account={account_id} "
            f"未读会话={unread_total} 新增入向={len(new_messages)}"
        )
        return new_messages
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[inbox] ✘ scan_inbox 异常 account={account_id}: {e}")
        return new_messages
    finally:
        try:
            await page.close()
        except Exception:
            pass
