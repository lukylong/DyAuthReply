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
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from asgiref.sync import sync_to_async
from django.utils import timezone

from core.douyin.runtime import selectors as S
from core.douyin.runtime.humanize import human_click, human_type, random_sleep

if TYPE_CHECKING:
    from core.douyin.douyin_account_model import DouyinAccount
    from core.douyin.douyin_conversation_model import DouyinConversation
    from core.douyin.douyin_rule_model import DouyinRule
    from core.douyin.douyin_message_model import DouyinMessage

logger = logging.getLogger(__name__)


def _manual_debug_dir(account_id: str) -> Path:
    root = Path("/app/tmp/douyin-manual-debug") / str(account_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


async def dump_manual_reply_debug(
    page,
    *,
    account_id: str,
    conversation_id: str,
    phase: str,
    text: str,
) -> Path:
    """
    保存手动回复调试证据：
    - 页面截图
    - 最近消息气泡摘要
    """
    ts = timezone.now().strftime("%Y%m%d_%H%M%S")
    base = _manual_debug_dir(account_id) / f"{conversation_id}_{phase}_{ts}"
    shot = base.with_suffix(".png")
    meta = base.with_suffix(".txt")
    try:
        await page.screenshot(path=str(shot), full_page=True)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[sender] 手动回复调试截图失败 account={account_id} phase={phase} err={e}")
    bubbles = await _read_recent_bubbles(page, limit=8)
    lines = [
        f"phase={phase}",
        f"text={text}",
        f"url={page.url}",
        "bubbles:",
    ]
    for item in bubbles:
        lines.append(f"- {item.get('direction')} | {item.get('text')}")
    try:
        meta.write_text("\n".join(lines), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[sender] 手动回复调试摘要写入失败 account={account_id} phase={phase} err={e}")
    return base


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

    # multi_message 或 card_fallback（暂退化为 multi_message）
    segs: list[str] = []
    if text.strip():
        segs.append(text)
    segs.extend(urls)
    return segs


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
    用作下一轮 scan_inbox 的"自己刚发过"回声黑名单的真源之一。

    使用 get_or_create 防止极端并发下重复落库。
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


async def _read_input_text(locator) -> str:
    try:
        return await locator.evaluate(
            """(el) => {
                if (!el) return '';
                const value = typeof el.value === 'string' ? el.value : '';
                const text = typeof el.innerText === 'string' ? el.innerText : '';
                return value || text || '';
            }"""
        )
    except Exception:
        return ""


async def _is_input_cleared(locator) -> bool:
    return not _normalize_message_text(await _read_input_text(locator))


async def _read_page_body_text(page) -> str:
    try:
        return await page.evaluate("() => document.body?.innerText || ''")
    except Exception:
        return ""


async def _set_editor_text(locator, text: str) -> None:
    """针对新版 contenteditable 编辑器，直接写入文本并派发 input/change 事件。"""
    await locator.evaluate(
        """(el, value) => {
            const text = String(value || '');
            const isContentEditable = el.getAttribute('contenteditable') === 'true';
            if (isContentEditable) {
                el.focus();
                el.innerHTML = '';
                const lines = text.split('\\n');
                lines.forEach((line, index) => {
                    if (index > 0) el.appendChild(document.createElement('br'));
                    el.appendChild(document.createTextNode(line));
                });
            } else {
                el.value = text;
            }
            el.dispatchEvent(new InputEvent('input', { bubbles: true, data: text, inputType: 'insertText' }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""",
        text,
    )


async def _send_one(page, text: str) -> None:
    """在当前会话输入一条消息并发送。

    优先模拟真实用户：聚焦输入框 -> 输入文本 -> 按 Enter。
    若 Enter 失败，再回退到点击发送按钮。
    """
    inp = await _locate_input(page)
    if inp is None:
        logger.error("[sender] 未找到输入框 selectors 全部落空")
        raise RuntimeError("未找到输入框")
    logger.debug(f"[sender] 输入框定位成功，开始输入 len={len(text)}")

    try:
        await inp.click()
        try:
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
        except Exception:
            pass
        await human_type(inp, text)
    except Exception:
        # 极少数情况下 contenteditable 可能拒绝逐段输入，退回到直接写 DOM。
        await _set_editor_text(inp, text)
    await random_sleep(0.3, 0.8)

    try:
        await inp.press("Enter")
    except Exception:
        try:
            await page.keyboard.press("Enter")
        except Exception:
            pass

    await asyncio.sleep(0.5)
    if await _is_input_cleared(inp):
        return

    btn = await _locate_send_btn(page)
    if btn is not None:
        try:
            if await btn.is_visible() and await btn.is_enabled():
                logger.debug("[sender] 点击发送按钮")
                await human_click(btn)
                await asyncio.sleep(0.5)
                if await _is_input_cleared(inp):
                    return
        except Exception:
            pass
        logger.info("[sender] Enter 发送失败后发送按钮不可见或不可用，跳过点击兜底")
    else:
        logger.info("[sender] 未找到发送按钮，且 Enter 发送失败")
    raise RuntimeError("Enter 发送失败且未找到可用发送按钮")


def _normalize_message_text(text: str) -> str:
    return " ".join((text or "").split())


def _split_expected_parts(text: str) -> list[str]:
    parts = []
    for part in re.split(r"\n+", text or ""):
        part = part.strip()
        if part:
            parts.append(part)
    return parts


async def confirm_text_present_in_recent_messages(
    page,
    text: str,
    *,
    links: Optional[list] = None,
    limit: int = 6,
) -> bool:
    """
    发送后再读一遍最近消息，确认文本已经出现在页面里。
    这是平台侧成功的更强证据，避免“代码以为点了发送”。
    """
    await asyncio.sleep(1.2)
    bubbles = await _read_recent_bubbles(page, limit=limit)
    target_parts = _split_expected_parts(text)
    target_links = []
    for lk in links or []:
        if isinstance(lk, dict):
            url = (lk.get('url') or '').strip()
        else:
            url = str(lk).strip()
        if url:
            target_links.append(url)
    for msg in bubbles:
        if msg.get('direction') != 'out':
            continue
        bubble_text = _normalize_message_text(msg.get('text') or '')
        if target_parts and all(part in bubble_text for part in target_parts):
            if all(url in bubble_text for url in target_links):
                return True
        elif not target_parts and target_links and all(url in bubble_text for url in target_links):
            return True
        elif not target_parts and not target_links and bubble_text:
            return True
    input_loc = await _locate_input(page)
    input_cleared = True
    if input_loc is not None:
        input_cleared = await _is_input_cleared(input_loc)
    if input_cleared:
        body_text = _normalize_message_text(await _read_page_body_text(page))
        if target_parts and all(part in body_text for part in target_parts):
            if all(url in body_text for url in target_links):
                return True
        elif not target_parts and target_links and all(url in body_text for url in target_links):
            return True
        elif not target_parts and not target_links and body_text:
            return True
    return await _confirm_text_present_in_conversation_preview(page, _normalize_message_text(text))


async def _read_recent_bubbles(page, limit: int = 6) -> list[dict]:
    """
    读取最近 limit 条消息气泡及其方向。

    方向判定遵循 `core.douyin.runtime.inbox._classify_bubble_direction` 的规则：
      1. 类名命中 IM_MESSAGE_OTHER_HINT → 'in'（peer 优先，避免误判为 self）
      2. 类名命中 IM_MESSAGE_SELF_HINT  → 'out'
      3. 兜底：沿用旧的"任意 self hint 命中"宽松判定，再按 hint 子集判 in/out
    confirm_text_present_in_recent_messages 只关心 direction=='out'
    的气泡是否包含目标文本，所以这里采取"宁可漏报 self，也别把对方误判成 self"。
    """
    results: list[dict] = []
    for sel in S.IM_MESSAGE_BUBBLES:
        try:
            loc = page.locator(sel)
            count = await loc.count()
            if count == 0:
                continue
            start = max(0, count - limit)
            for i in range(start, count):
                bubble = loc.nth(i)
                own_cls = (await bubble.get_attribute('class') or '').lower()
                # 抖音 PC 创作者中心 is-me-* 标记在外层，selector 选中的是内层，
                # 这里把 parent 的 class 一起拼进来判定，跟 inbox._classify_bubble_direction 保持一致。
                try:
                    parent_cls = (await bubble.evaluate(
                        "(el) => (el.parentElement && el.parentElement.getAttribute('class')) || ''"
                    ) or '').lower()
                except Exception:
                    parent_cls = ''
                cls = (own_cls + ' ' + parent_cls).strip()
                if any(h in cls for h in S.IM_MESSAGE_OTHER_HINT):
                    direction = 'in'
                elif any(h in cls for h in S.IM_MESSAGE_SELF_HINT):
                    direction = 'out'
                else:
                    direction = 'unknown'
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
                results.append({'direction': direction, 'text': text})
            return results
        except Exception:
            continue
    return results


async def _confirm_text_present_in_conversation_preview(page, target: str) -> bool:
    """
    新版私信页有时发送后不会立刻在右侧消息气泡区稳定渲染，但左侧当前会话预览会先更新为“刚刚 + 文本”。
    这里把它作为次一级成功证据。
    """
    preview_selectors = [
        "div.item-content-BSDfEh",
        "div.text-W2RFz4",
        "div[class*='item-content']",
    ]
    time_selectors = [
        "span.item-header-time-XtnnBd",
        "span[class*='item-header-time']",
    ]
    preview_text = ""
    for sel in preview_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count():
                preview_text = ((await loc.inner_text()) or "").strip()
                if preview_text:
                    break
        except Exception:
            continue

    preview_time = ""
    for sel in time_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count():
                preview_time = ((await loc.inner_text()) or "").strip()
                if preview_time:
                    break
        except Exception:
            continue

    if preview_text == target and preview_time in ("刚刚", "刚刚发送"):
        logger.info(
            f"[sender] 通过会话预览确认发送成功 preview={preview_text!r} time={preview_time!r}"
        )
        return True
    return False


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
    account_id = str(account.id)
    t0 = datetime.utcnow().timestamp()
    segments = _build_segments(rule, peer_nickname)
    logger.info(
        f"[sender] ▶ 开始发送 account={account_id} peer={peer_nickname!r} "
        f"rule={rule.name!r} send_mode={rule.send_mode} segments={len(segments)}"
    )
    if not segments:
        logger.warning(
            f"[sender] ⏭ 渲染结果为空，跳过发送 account={account_id} rule={rule.name!r}"
        )
        log_id = await _write_reply_log(
            account_id=account_id,
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
            preview = seg[:40].replace('\n', ' ')
            logger.info(
                f"[sender] 发送段 {i+1}/{len(segments)} account={account_id} "
                f"len={len(seg)} preview={preview!r}"
            )
            await _send_one(page, seg)
            if not await confirm_text_present_in_recent_messages(
                page,
                seg,
                links=getattr(getattr(rule, 'template', None), 'links', None) if getattr(rule, 'template', None) else rule.links,
            ):
                raise RuntimeError(f"平台侧未确认发送成功：第 {i+1}/{len(segments)} 段未出现在最近消息列表")
            # 随机间隔 1~3 秒
            if i < len(segments) - 1:
                import random
                gap = random.uniform(1.0, 3.0)
                logger.debug(f"[sender] 段间等待 {gap:.2f}s")
                await asyncio.sleep(gap)

        duration = int((datetime.utcnow().timestamp() - t0) * 1000)
        links_payload = []
        tpl = getattr(rule, 'template', None)
        links_payload = (tpl.links if tpl else rule.links) or []

        first_text = segments[0]
        log_id = await _write_reply_log(
            account_id=account_id,
            conversation_id=conversation_id,
            trigger_message_id=trigger_message_id,
            rule_id=str(rule.id),
            text=first_text,
            links=links_payload,
            result='success',
            duration_ms=duration,
        )
        # 关键：把每段实际发出的内容落成 direction='out' 的 DouyinMessage，
        # 让下一轮 scan_inbox 的 echo_blacklist 能稳定命中——避免"自己回复自己"。
        for seg in segments:
            try:
                await _record_auto_outbound_message(
                    account_id=account_id,
                    conversation_id=conversation_id,
                    text=seg,
                    rule_id=str(rule.id),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"[sender] 落 outbound DouyinMessage 失败（不影响发送主流程） "
                    f"account={account_id} conv={conversation_id} err={e}"
                )
        logger.info(
            f"[sender] ✔ 发送成功 account={account_id} peer={peer_nickname!r} "
            f"segments={len(segments)} duration_ms={duration} reply_log={log_id}"
        )
        return log_id
    except Exception as e:  # noqa: BLE001
        duration = int((datetime.utcnow().timestamp() - t0) * 1000)
        logger.exception(
            f"[sender] ✘ 发送失败 account={account_id} peer={peer_nickname!r} "
            f"rule={rule.name!r} duration_ms={duration} err={type(e).__name__}: {e}"
        )
        log_id = await _write_reply_log(
            account_id=account_id,
            conversation_id=conversation_id,
            trigger_message_id=trigger_message_id,
            rule_id=str(rule.id),
            text=segments[0] if segments else '',
            links=[],
            result='failed',
            error_message=f'{type(e).__name__}: {e}',
            duration_ms=duration,
        )
        logger.info(f"[sender] 失败日志已落库 reply_log={log_id}")
        raise RuntimeError(f"发送失败，reply_log={log_id}: {type(e).__name__}: {e}") from e
