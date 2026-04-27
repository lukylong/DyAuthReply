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
import re
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

_AGGREGATE_CONVERSATION_LABELS = {"陌生人消息", "朋友私信", "群消息", "全部消息"}
_TIME_LINE_RE = re.compile(r"^(?:\d{1,2}:\d{2}|昨天|前天|星期[一二三四五六日天]|刚刚)$")


class LoginGateDetected(RuntimeError):
    """IM 页面仍是登录门面，需要 worker 将账号打回重新登录。"""


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


def _fallback_peer_sec_uid(tab_label: str, nickname: str, preview: str) -> str:
    """当页面暂时拿不到真实 sec_uid 时，为会话快照生成稳定兜底键。"""
    h = hashlib.sha1()
    stable_name = nickname or preview[:80]
    h.update(f"{tab_label}|{stable_name}".encode('utf-8'))
    return f"fallback_{h.hexdigest()[:24]}"


@sync_to_async
def _upsert_conversation_snapshot(
    account_id: str,
    peer_sec_uid: str,
    peer_nickname: Optional[str],
    preview: str,
    unread_count: int,
    touched_at,
) -> Optional[str]:
    """只同步会话列表快照，不写消息流水。"""
    from core.douyin.douyin_account_model import DouyinAccount
    from core.douyin.douyin_conversation_model import DouyinConversation

    account = DouyinAccount.objects.filter(id=account_id).first()
    if account is None:
        return None

    conv, _ = DouyinConversation.objects.update_or_create(
        account=account,
        peer_sec_uid=peer_sec_uid,
        defaults={
            'peer_nickname': peer_nickname or '',
            'last_message_at': touched_at,
            'last_message_preview': (preview or '')[:200],
            'unread_count': max(0, int(unread_count or 0)),
        },
    )
    return str(conv.id)


@sync_to_async
def _get_conversation_preview(account_id: str, peer_sec_uid: str) -> Optional[str]:
    from core.douyin.douyin_conversation_model import DouyinConversation

    conv = DouyinConversation.objects.filter(account_id=account_id, peer_sec_uid=peer_sec_uid).first()
    if conv is None:
        return None
    return conv.last_message_preview or ''


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
    """返回当前页真正可见的私信会话项 Locator handles。"""
    for sel in S.IM_CONVERSATION_ITEMS:
        try:
            loc = page.locator(sel)
            count = await loc.count()
            if count > 0:
                items = []
                take = min(count, max(max_items * 3, max_items))
                for i in range(take):
                    item = loc.nth(i)
                    try:
                        if not await item.is_visible():
                            continue
                        box = await item.bounding_box()
                        if not box or box.get("width", 0) < 24 or box.get("height", 0) < 24:
                            continue
                        items.append(item)
                    except Exception:
                        continue
                    if len(items) >= max_items:
                        break
                if items:
                    return items
        except Exception:
            continue
    return []


async def _scroll_conversation_list(page) -> bool:
    """
    尝试滚动会话虚拟列表，触发 ReactVirtualized 懒加载下一屏。

    返回 True 表示页面确实发生了滚动；False 表示已经到底或未找到滚动容器。
    """
    try:
        moved = await page.evaluate(
            """() => {
                const grids = Array.from(document.querySelectorAll('div.ReactVirtualized__Grid'));
                let didMove = false;
                for (const grid of grids) {
                    const maxTop = Math.max(0, grid.scrollHeight - grid.clientHeight);
                    if (maxTop <= 0) continue;
                    const before = grid.scrollTop;
                    const delta = Math.max(480, Math.floor(grid.clientHeight * 0.85));
                    const next = Math.min(before + delta, maxTop);
                    if (next !== before) {
                        grid.scrollTop = next;
                        grid.dispatchEvent(new Event('scroll', { bubbles: true }));
                        didMove = true;
                    }
                }
                return didMove;
            }""",
        )
        return bool(moved)
    except Exception:
        return False


async def _read_visible_conversation_snapshot(page, max_items: int = 30) -> list[dict]:
    """读取当前可见会话项的快照，用于滚动扫描去重。"""
    snapshots: list[dict] = []
    items = await _list_conversation_items(page, max_items=max_items)
    for idx, item in enumerate(items):
        try:
            unread = await _get_unread(item)
            nickname = await _read_nested_text(item, S.IM_CONV_NICKNAME) or ''
            preview = await _read_nested_text(item, S.IM_CONV_LAST_MESSAGE) or ''
            parsed_nickname, parsed_preview = await _fallback_read_conversation_text(item)
            nickname = nickname or parsed_nickname
            preview = preview or parsed_preview
            snapshots.append({
                'idx': idx,
                'unread': unread,
                'nickname': nickname,
                'preview': preview,
                'fingerprint': f"{nickname}|{preview}|{unread}",
                'item': item,
            })
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[inbox] 读取会话快照失败 idx={idx} err={e}")
    return snapshots


async def _fallback_read_conversation_text(item) -> tuple[str, str]:
    """当结构化选择器失效时，从整项文本中尽量拆出 nickname / preview。"""
    try:
        raw = (await item.inner_text()).strip()
    except Exception:
        return "", ""
    return _parse_conversation_lines(raw)


def _parse_conversation_lines(raw: str) -> tuple[str, str]:
    lines = [line.strip() for line in (raw or "").splitlines() if line.strip()]
    if not lines:
        return "", ""
    nickname = lines[0]
    preview_lines = lines[1:]
    if preview_lines and _TIME_LINE_RE.fullmatch(preview_lines[-1]):
        preview_lines = preview_lines[:-1]
    preview = " ".join(preview_lines).strip()
    return nickname, preview


def _is_aggregate_conversation(nickname: str, tab_label: Optional[str]) -> bool:
    return (tab_label or "默认") in {"默认", "全部", "全部消息"} and nickname in _AGGREGATE_CONVERSATION_LABELS


async def _switch_im_tab(page, label: str) -> bool:
    """切换私信页签（全部/陌生人/已关注等），找不到则返回 False。"""
    for sel in S.IM_TAB_SELECTORS:
        try:
            loc = page.locator(sel)
            count = await loc.count()
            if count == 0:
                continue
            for i in range(count):
                tab = loc.nth(i)
                try:
                    text = ((await tab.inner_text()) or "").strip().replace("\n", "")
                except Exception:
                    continue
                if not text or label not in text:
                    continue
                await human_click(tab)
                await random_sleep(0.8, 1.5)
                logger.info(f"[inbox] 已切换私信页签 label={label!r}")
                return True
        except Exception:
            continue
    return False


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
                # 新版陌生人私信只有一个红点，没有数字，命中则视为至少 1 条未读
                try:
                    if await loc.is_visible():
                        return 1
                except Exception:
                    return 1
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
async def scan_inbox(
    account: "DouyinAccount",
    *,
    max_conversations: int = 15,
    include_recent_without_unread: bool = False,
) -> List[ScannedMessage]:
    """
    扫描一次收件箱，返回"本轮新增的入向消息"列表。

    - 默认仅扫描有未读角标的会话，避免对所有会话逐个点击（降低被风控风险）
    - include_recent_without_unread=True 时，对最近会话做一次补扫，每个会话最多补最近 1 条入向消息
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
        im_candidates = getattr(S, "CREATOR_IM_CANDIDATES", [S.CREATOR_IM])
        current_target = im_candidates[0]
        logger.info(f"[inbox] 打开 IM 页面 account={account_id} url={current_target}")
        for im_url in im_candidates:
            current_target = im_url
            await page.goto(im_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(1.5)
            if not await _looks_like_login_gate(page):
                break
        await asyncio.sleep(2.5)
        logger.info(f"[inbox] 页面加载完成 account={account_id} current_url={page.url}")

        if await _looks_like_login_gate(page):
            logger.warning(
                f"[inbox] IM 页面显示登录门面，跳过本轮扫描（不立即判失效） "
                f"account={account_id} url={page.url} target={current_target}"
            )
            raise LoginGateDetected(f"IM 页面仍为登录门面: {page.url}")

        tab_candidates = [None, *getattr(S, "IM_TAB_LABELS", [])]
        scanned_tabs: set[str] = set()
        unread_total = 0
        for tab_label in tab_candidates:
            tab_key = tab_label or "__default__"
            if tab_key in scanned_tabs:
                continue
            scanned_tabs.add(tab_key)
            if tab_label:
                switched = await _switch_im_tab(page, tab_label)
                if not switched:
                    continue
            seen_fingerprints: set[str] = set()
            scan_round = 0
            while True:
                scan_round += 1
                try:
                    snapshots = await _read_visible_conversation_snapshot(page, max_items=max_conversations)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"[inbox] 读取会话快照失败 account={account_id} tab={tab_label or '默认'} round={scan_round}: {e}"
                    )
                    snapshots = []
                logger.info(
                    f"[inbox] 会话列表项数 account={account_id} tab={tab_label or '默认'} "
                    f"round={scan_round} count={len(snapshots)}"
                )
                if not snapshots:
                    break

                round_hits = 0
                for snap in snapshots:
                    idx = int(snap['idx'])
                    unread = int(snap['unread'])
                    nickname = str(snap['nickname'] or '')
                    preview = str(snap['preview'] or '')
                    fingerprint = str(snap['fingerprint'] or '')
                    if fingerprint in seen_fingerprints:
                        continue
                    seen_fingerprints.add(fingerprint)
                    if nickname in ('陌生人消息', '朋友私信', '群消息') and ':' in preview:
                        nickname = preview.split(':', 1)[0].strip() or nickname
                    if _is_aggregate_conversation(nickname, tab_label):
                        logger.info(
                            f"[inbox] 跳过聚合入口 account={account_id} tab={tab_label or '默认'} "
                            f"nickname={nickname!r} preview={preview[:40]!r}"
                        )
                        continue
                    item = snap['item']
                    try:
                        await human_click(item)
                        await random_sleep(0.8, 1.5)
                        logger.info(f"[inbox] 已进入会话 nickname={nickname!r} url={page.url}")
                        peer_sec_uid = await _extract_peer_sec_uid(page) or _fallback_peer_sec_uid(
                            tab_label or '默认',
                            nickname or f"unknown_{idx}",
                            preview or '',
                        )
                        previous_preview = await _get_conversation_preview(account_id, peer_sec_uid)
                        now = timezone.now()
                        conv_id = await _upsert_conversation_snapshot(
                            account_id=account_id,
                            peer_sec_uid=peer_sec_uid,
                            peer_nickname=nickname,
                            preview=preview,
                            unread_count=unread,
                            touched_at=now,
                        )
                        logger.info(
                            f"[inbox] 已同步会话快照 #{idx} account={account_id} tab={tab_label or '默认'} "
                            f"conv_id={conv_id} nickname={nickname!r} unread={unread} "
                            f"peer_sec_uid={peer_sec_uid} preview={preview[:40]!r}"
                        )

                        if unread <= 0 and not include_recent_without_unread:
                            if (preview or '') == (previous_preview or ''):
                                continue
                            try:
                                # 仅当最新一条确实是对方发来时，才把“预览变化”当作新入向消息。
                                bubbles = await _read_conversation_messages(page, limit=3)
                                newest = bubbles[-1] if bubbles else None
                                if not newest or newest.get('direction') != 'in':
                                    logger.info(
                                        f"[inbox] 预览已变化但消息气泡未明确识别为入向，降级按预览处理 "
                                        f"account={account_id} nickname={nickname!r} "
                                        f"prev={str(previous_preview or '')[:40]!r} preview={preview[:40]!r}"
                                    )
                            except Exception as e:  # noqa: BLE001
                                logger.info(
                                    f"[inbox] 预览已变化但消息气泡读取失败，降级按预览处理 "
                                    f"account={account_id} nickname={nickname!r} err={e}"
                                )
                        unread_total += 1
                        round_hits += 1

                        logger.info(
                            f"[inbox] 发现可处理会话 #{idx} account={account_id} tab={tab_label or '默认'} "
                            f"nickname={nickname!r} unread={unread} preview={preview[:40]!r}"
                        )
                        bubble_limit = max(unread, 1) if unread > 0 else 3
                        bubbles = await _read_conversation_messages(page, limit=bubble_limit)
                        inbound = [b for b in bubbles if b['direction'] == 'in']
                        if not inbound and preview:
                            fallback_allowed = unread > 0 or include_recent_without_unread or preview != (previous_preview or '')
                            if fallback_allowed:
                                inbound = [{'direction': 'in', 'text': preview, 'source': 'preview'}]
                                logger.info(
                                    f"[inbox] 消息面板为空，回退使用会话预览 account={account_id} "
                                    f"nickname={nickname!r} tab={tab_label or '默认'} preview={preview[:60]!r}"
                                )
                        logger.info(
                            f"[inbox] 采集消息气泡 nickname={nickname!r} "
                            f"total={len(bubbles)} inbound={len(inbound)} peer_sec_uid={peer_sec_uid}"
                        )

                        if unread > 0:
                            picks = inbound[-unread:] if unread <= len(inbound) else inbound
                        else:
                            picks = inbound[-1:]
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
                                raw={
                                    'preview': preview,
                                    'unread': unread,
                                    'scan_idx': idx,
                                    'tab': tab_label or '默认',
                                    'round': scan_round,
                                    'backfill': include_recent_without_unread and unread <= 0,
                                },
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
                                raw={
                                    'preview': preview,
                                    'bubble_direction': b['direction'],
                                    'tab': tab_label or '默认',
                                    'round': scan_round,
                                    'backfill': include_recent_without_unread and unread <= 0,
                                },
                            ))
                            logger.info(
                                f"[inbox] 已捕获新消息，提前结束本轮扫描以便尽快触发回复 "
                                f"account={account_id} tab={tab_label or '默认'} conv_id={conv_id}"
                            )
                            return new_messages
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            f"[inbox] 处理会话 #{idx} 失败 account={account_id} tab={tab_label or '默认'}: {e}"
                        )
                        continue

                if round_hits == 0 and not await _scroll_conversation_list(page):
                    break
                if round_hits == 0:
                    await random_sleep(0.5, 0.9)
                    if scan_round >= 3:
                        break
                    continue
                if not await _scroll_conversation_list(page):
                    break
                await random_sleep(0.5, 0.9)
                if scan_round >= 8:
                    break

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
