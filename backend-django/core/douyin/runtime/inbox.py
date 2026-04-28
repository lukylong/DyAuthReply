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
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta
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
# 抖音 PC 私信列表里出现在 nickname 位置的"伪 nickname"——一律是时间标签，不是真实昵称。
# - "刚刚" / "X 秒前" / "X 分钟前" / "X 小时前" / "X 天前"
# - "昨天" / "前天" / "星期X"
# - "HH:MM" / "MM-DD" / "MM-DD HH:MM"
_TIME_LINE_RE = re.compile(
    r"^(?:"
    r"刚刚"
    r"|\d+\s*(?:秒|分钟|小时|天|周|月|年)前"
    r"|昨天|前天"
    r"|星期[一二三四五六日天]"
    r"|\d{1,2}:\d{2}"
    r"|\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2})?"
    r")$"
)


def _is_time_like_label(text: str) -> bool:
    """nickname / preview 第一行命中常见时间格式时返回 True，便于剔除脏 nickname。"""
    if not text:
        return False
    return bool(_TIME_LINE_RE.fullmatch(text.strip()))


class LoginGateDetected(RuntimeError):
    """IM 页面仍是登录门面，需要 worker 将账号打回重新登录。"""


class AccountIdentityMismatch(LoginGateDetected):
    """
    当前 BrowserContext 里登录的 sec_uid 与 DB 中 DouyinAccount.sec_uid 不一致。

    典型触发场景：用户在同一个 user_data_dir 上"先登出账号 A，没清 cookie 又扫码登录账号 B"
    （或浏览器里 cookies 被外部覆盖）。继续扫描会把 B 的会话写入 A 的会话表，造成串号脏数据。

    继承 LoginGateDetected 是因为 worker 已有 except 路径会把账号打回（status=2）；
    我们沿用同一通道 + 在 worker 端额外清 storage_state / user_data_dir，让用户必须重扫码。
    """

    def __init__(self, expected: str, actual: str, *, account_id: str):
        self.expected = expected
        self.actual = actual
        self.account_id = account_id
        super().__init__(
            f"账号 {account_id} 身份核对失败：DB 期望 sec_uid={expected[:24]}…，"
            f"实际页面 sec_uid={actual[:24]}…，疑似换号未清干净，已强制下线"
        )


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


def _norm_for_compare(text: str) -> str:
    """归一化文本用于回声去重：去掉所有空白和不可见字符并截首段。"""
    if not text:
        return ''
    cleaned = re.sub(r'\s+', '', text)
    return cleaned[:120]


@sync_to_async
def _recent_outbound_texts(
    account_id: str,
    peer_sec_uid: str,
    *,
    window_seconds: int = 90,
) -> list[str]:
    """
    返回最近 `window_seconds` 内本账号在该会话发出的消息文本（归一化后）。
    用于过滤抖音 DOM 把自己刚发的消息回放为新气泡造成的"自回复"。
    """
    from core.douyin.douyin_message_model import DouyinMessage

    cutoff = timezone.now() - timedelta(seconds=window_seconds)
    rows = DouyinMessage.objects.filter(
        conversation__account_id=account_id,
        conversation__peer_sec_uid=peer_sec_uid,
        direction='out',
        received_at__gte=cutoff,
    ).values_list('content', flat=True)[:30]
    return [_norm_for_compare(r) for r in rows if r]


@sync_to_async
def _recent_outbound_replies_log(
    account_id: str,
    conversation_id: str,
    *,
    window_seconds: int = 90,
) -> list[str]:
    """
    DouyinReplyLog 是另一处 outbound 真源（自动回复成功才落 reply_log）。
    与 DouyinMessage 一起组成回声去重的"自己刚发过"集合。
    """
    from core.douyin.douyin_reply_log_model import DouyinReplyLog

    cutoff = timezone.now() - timedelta(seconds=window_seconds)
    qs = DouyinReplyLog.objects.filter(
        account_id=account_id,
        result='success',
        sent_at__gte=cutoff,
    )
    if conversation_id:
        qs = qs.filter(conversation_id=conversation_id)
    return [_norm_for_compare(r) for r in qs.values_list('reply_text', flat=True)[:30] if r]


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


def _sanitize_conversation_snapshot(nickname: str, preview: str) -> tuple[str, str]:
    """把会话项里明显错误的 nickname 清掉，避免被入库当成 peer_nickname / fallback hash 因子。

    抖音 PC 列表 DOM 在虚拟滚动 / 类名变更时偶尔会让选择器命中：
    - 时间标签（"刚刚"、"X 分钟前"、"昨天"、"HH:MM"）
    - 直接落到消息预览本身（nickname == preview）
    这两类入库后会形成"幽灵会话"——前端表格里看到 nickname 是时间或消息文本。

    清洗策略：nickname 命中以上情况就置空（让上层走 _extract_peer_sec_uid 的真实链接，
    或在 fallback hash 里只用 preview 兜底）。
    """
    nickname = (nickname or '').strip()
    preview = (preview or '').strip()
    if not nickname:
        return '', preview
    if _is_time_like_label(nickname):
        return '', preview
    # nickname 与 preview 完全相同的极短文本：几乎肯定是 selector 误命中
    # （截图里 "22啊啊啊" == "22啊啊啊" 就是这种）。阈值压在 8 以避免误伤
    # 长群名 / 用户自取的稍长 ID。
    if nickname == preview and len(nickname) <= 8:
        return '', preview
    return nickname, preview


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
            nickname, preview = _sanitize_conversation_snapshot(nickname, preview)
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
    # 跳过开头的时间标签行（"刚刚"/"3 分钟前"/"昨天"/"12:34" 等）——抖音 PC
    # 把时间行渲染在昵称上方时，inner_text 拆出来的第一行可能就是时间。
    while lines and _is_time_like_label(lines[0]):
        lines.pop(0)
    if not lines:
        return "", ""
    nickname = lines[0]
    preview_lines = lines[1:]
    if preview_lines and _is_time_like_label(preview_lines[-1]):
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


# ---------- own sec_uid 自动回填 ----------
# 登录采集 sec_uid 经常失败（抖音 PC cookie 名不固定、SSR body 里也不一定有），
# 而 inbox 又强依赖 own_sec_uid 做 self/peer 判定 —— 没有就把所有气泡判成 unknown，
# 进而被回声黑名单兜底吞掉，整条自动回复链路就断了。
# 这里在 IM 页加载后主动多源回填一次。
_SEC_UID_COOKIE_NAMES = (
    'sec_user_id', 'sec_uid', 'sec_user_id_v2', 'sec_uid_v2',
)
_SEC_UID_BODY_PATTERNS = (
    re.compile(r'"sec_uid"\s*:\s*"([A-Za-z0-9_-]{20,})"'),
    re.compile(r'"secUid"\s*:\s*"([A-Za-z0-9_-]{20,})"'),
    re.compile(r'"sec_user_id"\s*:\s*"([A-Za-z0-9_-]{20,})"'),
    re.compile(r'/user/(?:profile/)?([A-Za-z0-9_-]{30,})"'),
)


async def _collect_own_sec_uid(page) -> Optional[str]:
    """从 cookies / URL / page body / window 全局变量 多策略提取自己的 sec_uid。

    采集失败时会打印一份诊断 log（含 cookie 名清单、body 是否出现 sec_uid 字面量），
    便于按真实抖音 PC 端的字段命名继续扩充策略。
    """
    cookie_names: list[str] = []
    # 1) cookies 命名兜底
    try:
        cookies = await page.context.cookies()
        cookie_names = sorted({(c.get('name') or '').strip() for c in cookies if c.get('name')})
        for c in cookies:
            name = (c.get('name') or '').lower()
            if name in _SEC_UID_COOKIE_NAMES:
                v = (c.get('value') or '').strip()
                if v and len(v) >= 20:
                    return v
    except Exception:
        pass
    # 2) URL（IM 进入会话后 URL 上偶尔挂 sec_uid，但通常是对方的，慎用）
    #    → 只在 creator-micro/profile 这种自己的页面 URL 才认
    try:
        url = page.url or ''
        if 'creator-micro' in url or 'creator.douyin.com' in url:
            m = re.search(r'/user/([A-Za-z0-9_-]{30,})', url)
            if m:
                return m.group(1)
    except Exception:
        pass
    # 3) window 全局变量（很多 SPA 把当前用户信息挂在 window.__INITIAL_STATE__ 等）
    try:
        js_uid = await page.evaluate(
            """() => {
                const tryPaths = (obj, paths) => {
                    for (const p of paths) {
                        let cur = obj;
                        for (const seg of p.split('.')) {
                            if (cur && typeof cur === 'object' && seg in cur) cur = cur[seg];
                            else { cur = undefined; break; }
                        }
                        if (typeof cur === 'string' && cur.length >= 20) return cur;
                    }
                    return null;
                };
                const candidates = [
                    'sec_uid', 'secUid', 'sec_user_id', 'secUserId',
                    'user.sec_uid', 'user.secUid', 'user.sec_user_id',
                    'userInfo.sec_uid', 'userInfo.secUid',
                    'currentUser.sec_uid', 'me.sec_uid',
                ];
                for (const root of [window.__INITIAL_STATE__, window.__pace_f, window._SSR_DATA, window.userInfo, window.__USER__, window.user]) {
                    const v = tryPaths(root, candidates);
                    if (v) return v;
                }
                return null;
            }""",
        )
        if js_uid and isinstance(js_uid, str) and len(js_uid) >= 20:
            return js_uid
    except Exception:
        pass
    # 4) page body / SSR 数据
    body_excerpt = ''
    try:
        content = await page.content()
        for pat in _SEC_UID_BODY_PATTERNS:
            m = pat.search(content)
            if m:
                return m.group(1)
        # 命中失败时截一段含 'sec' 的上下文，便于诊断
        idx = content.find('sec_uid')
        if idx < 0:
            idx = content.find('"secUid"')
        if idx < 0:
            idx = content.find('sec_user_id')
        if idx >= 0:
            body_excerpt = content[max(0, idx - 20):idx + 120]
    except Exception:
        pass
    # 5) 终极兜底：临时新开一个 tab，跳 https://www.douyin.com/user/self
    #    抖音会 302 到 https://www.douyin.com/user/MS4wLjABAAAA...
    #    URL 上就带着自己的 sec_uid。
    #    实测抖音 PC 创作者中心 cookie / SSR 都不暴露 sec_uid，这条路最稳。
    import contextlib as _ctxmod
    try:
        ctx = page.context
        probe = await ctx.new_page()
        try:
            await probe.goto(
                "https://www.douyin.com/user/self",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await asyncio.sleep(1.8)
            final_url = probe.url or ''
            logger.info(f"[inbox] sec_uid 探测 /user/self → final_url={final_url}")
            m = re.search(r'/user/([A-Za-z0-9_-]{30,})', final_url)
            if m:
                return m.group(1)
            # 如果 URL 没拿到，再从 body 里捞一次
            try:
                content2 = await probe.content()
                for pat in _SEC_UID_BODY_PATTERNS:
                    mm = pat.search(content2)
                    if mm:
                        logger.info(f"[inbox] sec_uid 探测 /user/self body 命中 {pat.pattern!r}")
                        return mm.group(1)
            except Exception:
                pass
            logger.warning(
                f"[inbox] sec_uid 探测 /user/self 仍 miss，可能 douyin.com 主站未登录 / 风控页 url={final_url}"
            )
        finally:
            with _ctxmod.suppress(Exception):
                await probe.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[inbox] sec_uid 探测 /user/self 抛错: {e}")

    # 全部失败：打印一份诊断行
    logger.debug(
        f"[inbox] sec_uid 采集全部 miss | cookie_names={cookie_names[:30]} "
        f"| body_excerpt={body_excerpt!r}"
    )
    return None


@sync_to_async
def _persist_own_sec_uid(account_id: str, sec_uid: str) -> bool:
    """把回填到的 sec_uid 写回 DouyinAccount。返回是否真的写入。"""
    from core.douyin.douyin_account_model import DouyinAccount
    updated = (
        DouyinAccount.objects
        .filter(id=account_id)
        .exclude(sec_uid=sec_uid)
        .update(sec_uid=sec_uid)
    )
    return updated > 0


async def _probe_own_sec_uid_lightweight(page) -> Optional[str]:
    """轻量级当前页面 sec_uid 探测：只看 cookies / URL / window 全局，不打 /user/self。

    用于身份核对（每轮 scan 入口都会调），失败返回 None 不抛异常。
    /user/self 兜底走 `_collect_own_sec_uid`，只在首次回填或轻量探测全 miss 时使用。
    """
    # 1) cookies
    try:
        cookies = await page.context.cookies()
        for c in cookies:
            name = (c.get('name') or '').lower()
            if name in _SEC_UID_COOKIE_NAMES:
                v = (c.get('value') or '').strip()
                if v and len(v) >= 20:
                    return v
    except Exception:
        pass
    # 2) URL（仅 creator-micro / creator.douyin.com 才认）
    try:
        url = page.url or ''
        if 'creator-micro' in url or 'creator.douyin.com' in url:
            m = re.search(r'/user/([A-Za-z0-9_-]{30,})', url)
            if m:
                return m.group(1)
    except Exception:
        pass
    # 3) window 全局
    try:
        js_uid = await page.evaluate(
            """() => {
                const tryPaths = (obj, paths) => {
                    for (const p of paths) {
                        let cur = obj;
                        for (const seg of p.split('.')) {
                            if (cur && typeof cur === 'object' && seg in cur) cur = cur[seg];
                            else { cur = undefined; break; }
                        }
                        if (typeof cur === 'string' && cur.length >= 20) return cur;
                    }
                    return null;
                };
                const candidates = [
                    'sec_uid', 'secUid', 'sec_user_id', 'secUserId',
                    'user.sec_uid', 'user.secUid', 'user.sec_user_id',
                    'userInfo.sec_uid', 'userInfo.secUid',
                    'currentUser.sec_uid', 'me.sec_uid',
                ];
                for (const root of [window.__INITIAL_STATE__, window.__pace_f, window._SSR_DATA, window.userInfo, window.__USER__, window.user]) {
                    const v = tryPaths(root, candidates);
                    if (v) return v;
                }
                return null;
            }""",
        )
        if js_uid and isinstance(js_uid, str) and len(js_uid) >= 20:
            return js_uid
    except Exception:
        pass
    return None


async def _ensure_account_sec_uid(account, page) -> Optional[str]:
    """确保 own_sec_uid 可用并校验账号身份未漂移。

    - 始终先做一次轻量探测（cookies / URL / window globals）。
    - DB 有 sec_uid + 探测有 sec_uid + 二者不一致 → 抛 AccountIdentityMismatch
      （worker 会把账号打回 + 清掉 storage_state / user_data_dir，强制重扫码）。
    - DB 无 sec_uid + 探测有 → 首次回填，写 DB + 内存对象。
    - DB 有 + 探测无 → 信 DB（探测每轮失败概率不低，不能据此判失败）。
    - 都无 → 走 `_collect_own_sec_uid` 的全链路兜底（含 /user/self），仍失败则返回 None。
    """
    own = (getattr(account, 'sec_uid', '') or '').strip()
    probed = await _probe_own_sec_uid_lightweight(page)

    # 关键：身份核对——避免"A 的 user_data_dir 上扫了 B 的码"导致的串号脏数据
    if own and probed and own != probed:
        logger.warning(
            f"[inbox] ⚠ 账号身份漂移 account={account.id} "
            f"db_sec_uid={own[:24]}… page_sec_uid={probed[:24]}… url={page.url}"
        )
        raise AccountIdentityMismatch(expected=own, actual=probed, account_id=str(account.id))

    if own:
        return own

    discovered = probed or await _collect_own_sec_uid(page)
    if not discovered:
        try:
            cookies = await page.context.cookies()
            names = sorted({(c.get('name') or '') for c in cookies if c.get('name')})
        except Exception:
            names = []
        logger.warning(
            f"[inbox] ⚠ 无法回填 own sec_uid account={account.id} url={page.url} "
            f"cookies={names[:25]} "
            f"（self/peer 判定将退化，气泡会被一律视作 unknown）"
        )
        return None
    try:
        wrote = await _persist_own_sec_uid(str(account.id), discovered)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[inbox] 回填 sec_uid 写库失败 account={account.id} err={e}")
        wrote = False
    try:
        account.sec_uid = discovered
    except Exception:
        pass
    logger.info(
        f"[inbox] ✔ 已回填 own sec_uid account={account.id} sec_uid={discovered[:24]}... persisted={wrote}"
    )
    return discovered


async def _classify_bubble_direction(
    bubble,
    *,
    own_sec_uid: Optional[str],
) -> str:
    """
    判定一条消息气泡是 self（自己发出）还是 peer（对方发来）。

    判定优先级（从高到低）：
      1. bubble 内是否有指向 own_sec_uid 的作者主页链接 → self
      2. bubble 内是否有指向其他 sec_user_id 的作者主页链接 → peer
      3. CSS 类名命中 OTHER_HINT → peer（优先 peer，避免误判自己）
      4. CSS 类名命中 SELF_HINT  → self
      5. 兜底：unknown（让上层按入向处理；上层会再用 outbound 短期黑名单二次校验）

    注意：这里返回 'self' / 'peer' / 'unknown' 三态，便于外层稳健决策。
    """
    # 1) 作者主页链接
    if own_sec_uid:
        for link_sel in S.IM_MESSAGE_AUTHOR_LINK:
            try:
                links = bubble.locator(link_sel)
                count = await links.count()
                for i in range(count):
                    href = (await links.nth(i).get_attribute('href')) or ''
                    if not href:
                        continue
                    href_norm = href.replace('\\', '/').lower()
                    own = own_sec_uid.lower()
                    if own in href_norm:
                        return 'self'
                    # 链接里有 user/<sec_uid> 但不是自己 → 对方
                    m = re.search(r'/user/([A-Za-z0-9_-]{12,})', href)
                    if m:
                        link_uid = m.group(1).lower()
                        if link_uid != own:
                            return 'peer'
                    m2 = re.search(r'sec_uid=([A-Za-z0-9_-]+)', href)
                    if m2:
                        link_uid = m2.group(1).lower()
                        if link_uid != own:
                            return 'peer'
            except Exception:
                continue

    # 2) CSS 类名（other 优先于 self，避免对方消息被误判为 self 触发漏回）
    # 抖音 PC 创作者中心实测：is-me-* 标记在外层 box-item-W0TV01，
    # 而 selector 选中的是内层 box-item-message-*，因此需要同时看 parent。
    try:
        own_cls = (await bubble.get_attribute('class') or '').lower()
    except Exception:
        own_cls = ''
    parent_cls = ''
    try:
        parent_cls = (await bubble.evaluate(
            "(el) => (el.parentElement && el.parentElement.getAttribute('class')) || ''"
        ) or '').lower()
    except Exception:
        pass
    cls = (own_cls + ' ' + parent_cls).strip()
    if cls:
        if any(h in cls for h in S.IM_MESSAGE_OTHER_HINT):
            return 'peer'
        if any(h in cls for h in S.IM_MESSAGE_SELF_HINT):
            return 'self'

    # 3) 兜底：尝试 data-* 属性
    try:
        data_dir = (await bubble.get_attribute('data-direction') or '').lower()
    except Exception:
        data_dir = ''
    if data_dir in ('self', 'out', 'send', 'sent', 'master'):
        return 'self'
    if data_dir in ('peer', 'in', 'other', 'received'):
        return 'peer'

    return 'unknown'


async def _dump_chat_dom_sample(page) -> str:
    """当气泡 selector 全 miss 时调用，dump 一份当前会话面板里出现过的、
    可能是消息气泡相关的 className，方便线上反推抖音 PC 最新真实类名。

    返回一段紧凑的 JSON 字符串（已限长），调用方负责打到 log。
    """
    try:
        info = await page.evaluate(
            """() => {
                const out = { url: location.href };
                // 1) 当前所有带 class 的元素，按 className 出现频次排序，挑跟消息相关的关键词
                const KEYWORDS = ['msg', 'message', 'bubble', 'chat', 'item', 'content', 'master', 'other', 'self', 'peer', 'inbound', 'outbound'];
                const counter = new Map();
                document.querySelectorAll('*[class]').forEach((el) => {
                    const cls = (el.getAttribute('class') || '').trim();
                    if (!cls) return;
                    const lower = cls.toLowerCase();
                    if (!KEYWORDS.some((k) => lower.includes(k))) return;
                    counter.set(cls, (counter.get(cls) || 0) + 1);
                });
                const top = [...counter.entries()]
                    .sort((a, b) => b[1] - a[1])
                    .slice(0, 25)
                    .map(([cls, n]) => `${n}× ${cls}`);
                out.top_classes = top;

                // 2) 找疑似消息列表容器，dump 一段 HTML（去掉冗长属性）
                const candidates = [
                    '[class*="message-list"]',
                    '[class*="msg-list"]',
                    '[class*="chat-content"]',
                    '[class*="chat-window"]',
                    '[class*="conversation-content"]',
                    'main',
                ];
                for (const sel of candidates) {
                    const el = document.querySelector(sel);
                    if (el) {
                        const html = (el.outerHTML || '').slice(0, 1200);
                        out.matched_container = sel;
                        out.html_excerpt = html;
                        break;
                    }
                }
                return out;
            }""",
        )
        # 紧凑序列化
        return json.dumps(info, ensure_ascii=False)[:2400]
    except Exception as e:  # noqa: BLE001
        return f"(dump_failed: {e})"


async def _read_conversation_messages(
    page,
    *,
    limit: int = 5,
    own_sec_uid: Optional[str] = None,
) -> list[dict]:
    """
    读取当前打开会话的最近 `limit` 条消息。
    返回 [{direction, text, ts}]：
      - direction='out' 表示自己发的；
      - direction='in'  表示对方发的；
      - direction='unknown' 表示无法识别——上层应当作"疑似入向"，
        并配合最近 outbound 短期黑名单做二次去重，避免回复自己刚发的消息。
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
                role = await _classify_bubble_direction(bubble, own_sec_uid=own_sec_uid)
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
                if role == 'self':
                    direction = 'out'
                elif role == 'peer':
                    direction = 'in'
                else:
                    direction = 'unknown'
                results.append({'direction': direction, 'text': text})
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
    own_sec_uid = (getattr(account, 'sec_uid', '') or '').strip() or None
    logger.info(
        f"[inbox] ▶ 开始扫描 account={account_id} nickname={account.nickname!r} "
        f"max_conversations={max_conversations} own_sec_uid={own_sec_uid or '(unknown)'}"
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

        # 关键：每次扫描前确保 own_sec_uid 已回填，否则 self/peer 判定会全部退化为 unknown
        own_sec_uid = await _ensure_account_sec_uid(account, page) or own_sec_uid

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
                        # 等待面板切换 + React 异步渲染气泡（之前 0.8~1.5s 不够，导致 total=0）
                        await random_sleep(1.5, 2.2)
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
                            # 注意：之前这里曾把"预览变化"直接当成新入向消息回退，
                            # 但 worker 自己回复后预览也会变成自己发的内容，造成循环回复自己。
                            # 现在不再做 preview→入向 的兜底，只信任 bubble 维度的方向判定。
                        unread_total += 1
                        round_hits += 1

                        logger.info(
                            f"[inbox] 发现可处理会话 #{idx} account={account_id} tab={tab_label or '默认'} "
                            f"nickname={nickname!r} unread={unread} preview={preview[:40]!r}"
                        )
                        bubble_limit = max(unread, 1) if unread > 0 else 3
                        # 抖音 PC IM 面板气泡是 React 异步渲染：点击会话后 1~2 秒才完成首屏。
                        # 第一次读到 0 个气泡时再等一段时间重试一次，能显著降低"明明有消息但 total=0"的概率。
                        bubbles = await _read_conversation_messages(
                            page,
                            limit=bubble_limit,
                            own_sec_uid=own_sec_uid,
                        )
                        if not bubbles:
                            await random_sleep(1.2, 1.8)
                            bubbles = await _read_conversation_messages(
                                page,
                                limit=bubble_limit,
                                own_sec_uid=own_sec_uid,
                            )
                            if bubbles:
                                logger.info(
                                    f"[inbox] 气泡渲染慢，重试 1 次后采到 total={len(bubbles)} "
                                    f"account={account_id} nickname={nickname!r}"
                                )
                        # 仍 0：dump 一份 DOM 类名样本，便于线上反推抖音 PC 真实 className
                        if not bubbles:
                            try:
                                sample = await _dump_chat_dom_sample(page)
                                logger.warning(
                                    f"[inbox] 🔍 气泡 selector 全 miss，DOM 样本 "
                                    f"account={account_id} nickname={nickname!r} | {sample}"
                                )
                            except Exception as _e:  # noqa: BLE001
                                logger.debug(f"[inbox] dump_chat_dom_sample 失败: {_e}")
                        # direction='in' 一定是对方；'unknown' 视作"疑似入向"，
                        # 后面会用最近 outbound 回声黑名单二次去重。
                        inbound_strict = [b for b in bubbles if b.get('direction') == 'in']
                        inbound_loose = [
                            b for b in bubbles
                            if b.get('direction') in ('in', 'unknown')
                        ]
                        if not inbound_strict and inbound_loose:
                            logger.info(
                                f"[inbox] DOM 未明确识别 self/peer，按疑似入向继续 "
                                f"account={account_id} nickname={nickname!r} "
                                f"loose={len(inbound_loose)} bubbles={len(bubbles)}"
                            )
                        inbound = inbound_strict if inbound_strict else inbound_loose

                        # 自己刚发过的内容黑名单（90s 窗口），过滤"DOM 把自己刚发的消息回放成新气泡"
                        recent_outbound_msgs = await _recent_outbound_texts(account_id, peer_sec_uid)
                        recent_reply_logs = await _recent_outbound_replies_log(account_id, conv_id or '')
                        echo_blacklist = set(filter(None, recent_outbound_msgs + recent_reply_logs))

                        logger.info(
                            f"[inbox] 采集消息气泡 nickname={nickname!r} "
                            f"total={len(bubbles)} strict_in={len(inbound_strict)} "
                            f"loose_in={len(inbound_loose)} peer_sec_uid={peer_sec_uid} "
                            f"echo_blacklist={len(echo_blacklist)}"
                        )

                        if unread > 0:
                            picks = inbound[-unread:] if unread <= len(inbound) else inbound
                        else:
                            picks = inbound[-1:]
                        for b in picks:
                            text = b.get('text') or ''
                            if not text:
                                continue
                            text_norm = _norm_for_compare(text)
                            if text_norm and text_norm in echo_blacklist:
                                logger.info(
                                    f"[inbox] ⏭ 跳过：与最近 90s outbound 文本相同（疑似自回声） "
                                    f"account={account_id} nickname={nickname!r} text={text[:60]!r}"
                                )
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
