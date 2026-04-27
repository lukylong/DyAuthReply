#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: worker.py
@Desc: Douyin Worker - 常驻 worker 主循环

职责：
  - 每 `refresh_interval` 秒扫描 DB：挑出需要托管的账号
      * status=1（在线） & work_mode in ('auto', 'hybrid')
      * 新增账号 → 启动 _account_loop 协程
      * 已移除账号 → 取消协程 + 关 context
  - 订阅 Redis pubsub，接收后台 API 下发的即时指令：
      * douyin:cmd:login:{account_id}     → 跑扫码登录
      * douyin:cmd:logout:{account_id}    → 关 context + 清登录态
      * douyin:cmd:session:{account_id}:{pause|resume|stop|restart}
  - 每 `heartbeat_interval` 秒向 DouyinSession 写心跳（供前端"并发会话监控"看）

每个账号的 _account_loop：
  - 若 status=0（未登录）且被明确 kick → 跑一次 scan_qrcode_login
  - 若 status=1（在线）→ 按 account.min/max_interval 随机节流，循环 scan_inbox → 回复
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import socket
import time
from contextlib import suppress
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

import psutil
from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone

from core.douyin.runtime.browser import BrowserManager
from core.douyin.runtime.inbox import (
    LoginGateDetected,
    ScannedMessage,
    _read_visible_conversation_snapshot,
    _switch_im_tab,
    scan_inbox,
)
from core.douyin.runtime.login import is_login_valid, mark_account_login_invalid, scan_qrcode_login
from core.douyin.runtime.humanize import human_click
from core.douyin.runtime.matcher import match as match_rule
from core.douyin.runtime.sender import (
    _send_one,
    confirm_text_present_in_recent_messages,
    dump_manual_reply_debug,
    send_reply,
    write_manual_out_message,
)
from core.douyin.runtime.ws_notify import push_event_log, push_to_user

if TYPE_CHECKING:
    from core.douyin.douyin_account_model import DouyinAccount

logger = logging.getLogger(__name__)


def _normalize_nickname(name: Optional[str]) -> str:
    return (name or "").strip().lower().replace(" ", "")


async def _find_conversation_item_across_tabs(
    page,
    *,
    target_nick: str,
    target_preview: str,
    max_items: int = 30,
):
    """
    在全部私信分组里搜索目标会话。

    返回 (locator, tab_label)。locator 找到则可直接点击。
    """
    search_tabs = [
        None,
        "陌生人",
        "陌生人消息",
        "陌生人私信",
        "朋友私信",
        "群消息",
        "全部",
        "全部消息",
        "已关注",
        "粉丝",
    ]
    seen_tabs: set[str] = set()
    target_nick_norm = _normalize_nickname(target_nick)
    target_preview = (target_preview or "").strip()[:24]

    for tab_label in search_tabs:
        tab_key = tab_label or "__current__"
        if tab_key in seen_tabs:
            continue
        seen_tabs.add(tab_key)

        if tab_label:
            try:
                if not await _switch_im_tab(page, tab_label):
                    continue
                await asyncio.sleep(0.8)
            except Exception:
                continue

        try:
            snapshots = await _read_visible_conversation_snapshot(page, max_items=max_items)
        except Exception as e:  # noqa: BLE001
            logger.debug(
                f"[reply] 读取会话快照失败 tab={tab_label or '默认'} err={e}"
            )
            continue

        logger.info(
            f"[reply] 会话搜索 tab={tab_label or '默认'} count={len(snapshots)} "
            f"target_nick={target_nick!r} target_preview={target_preview!r}"
        )
        for snap in snapshots:
            nick_norm = _normalize_nickname(str(snap.get('nickname') or ''))
            preview = str(snap.get('preview') or '')
            nick_exact = bool(target_nick_norm) and nick_norm == target_nick_norm
            nick_fuzzy = bool(target_nick_norm) and (
                target_nick_norm in nick_norm or nick_norm in target_nick_norm
            )
            preview_hit = bool(target_preview) and target_preview in preview
            if nick_exact or nick_fuzzy or preview_hit:
                return snap.get('item'), tab_label or '默认'
    return None, None

# 命令频道（与 API 侧保持一致）
CMD_CHANNEL_PATTERN = "douyin:cmd:*"


# -------------------- DB 辅助 --------------------
@sync_to_async
def _load_managed_accounts() -> list[dict]:
    """加载所有需要 worker 托管的账号（快照为 dict 避免 ORM 对象跨线程）"""
    from core.douyin.douyin_account_model import DouyinAccount

    qs = DouyinAccount.objects.filter(
        status__in=[0, 1, 2],
        work_mode__in=['auto', 'hybrid'],
        is_deleted=False,
    ).exclude(status=3).order_by('-priority', '-sort')
    rows: list[dict] = []
    for a in qs:
        rows.append({
            'id': str(a.id),
            'owner_id': str(a.owner_id) if a.owner_id else '',
            'nickname': a.nickname,
            'status': a.status,
            'work_mode': a.work_mode,
            'priority': a.priority,
            'min_interval_seconds': a.min_interval_seconds,
            'max_interval_seconds': a.max_interval_seconds,
            'silent_start': a.silent_start.isoformat() if a.silent_start else None,
            'silent_end': a.silent_end.isoformat() if a.silent_end else None,
            'daily_reply_quota': a.daily_reply_quota,
            'reply_today': a.reply_today,
            'pending_verification_type': a.pending_verification_type,
            'pending_verification_until': (
                a.pending_verification_until.isoformat()
                if a.pending_verification_until else None
            ),
        })
    return rows


@sync_to_async
def _fetch_account_orm(account_id: str):
    """需要 ORM 对象时再取（login / inbox / sender 调用）"""
    from core.douyin.douyin_account_model import DouyinAccount
    return DouyinAccount.objects.filter(id=account_id).first()


@sync_to_async
def _fetch_conversation(account_id: str, conversation_id: str):
    from core.douyin.douyin_conversation_model import DouyinConversation
    return DouyinConversation.objects.filter(id=conversation_id, account_id=account_id).first()


@sync_to_async
def _session_is_active(account_id: str) -> bool:
    """判断该账号是否仍有活跃会话在心跳。"""
    from core.douyin.douyin_session_model import DouyinSession

    session = DouyinSession.objects.filter(account_id=account_id).first()
    return bool(session and session.status in ('running', 'idle') and session.is_alive())


@sync_to_async
def _create_synthetic_inbound_message(account_id: str, conversation_id: str, text: str) -> ScannedMessage:
    from core.douyin.douyin_conversation_model import DouyinConversation
    from core.douyin.douyin_message_model import DouyinMessage

    conv = DouyinConversation.objects.get(id=conversation_id, account_id=account_id)
    now = timezone.now()
    msg = DouyinMessage.objects.create(
        conversation=conv,
        external_msg_id=f"manual_test_in_{now.timestamp()}",
        direction='in',
        content_type='text',
        content=text,
        raw_payload={'manual_auto_reply_test': True},
        received_at=now,
        processed=False,
    )
    conv.last_message_at = now
    conv.last_message_preview = text[:200]
    conv.unread_count = max(1, conv.unread_count or 0)
    conv.save(update_fields=['last_message_at', 'last_message_preview', 'unread_count', 'sys_update_datetime'])
    return ScannedMessage(
        message_id=str(msg.id),
        conversation_id=str(conv.id),
        peer_sec_uid=conv.peer_sec_uid,
        peer_nickname=conv.peer_nickname,
        text=text,
        received_at=now.isoformat(),
        raw={'manual_auto_reply_test': True},
    )


@sync_to_async
def _load_rules_for_account(account_id: str) -> list:
    """按优先级降序返回启用中的规则"""
    from core.douyin.douyin_rule_model import DouyinRule

    return list(
        DouyinRule.objects.filter(account_id=account_id, status=True)
        .select_related('template')
        .order_by('-priority', '-sys_create_datetime')
    )


@sync_to_async
def _is_in_cooldown(conversation_id: str, rule_id: str, cooldown_seconds: int) -> bool:
    """判断 (会话, 规则) 是否在冷却期内"""
    if cooldown_seconds <= 0:
        return False
    from core.douyin.douyin_reply_log_model import DouyinReplyLog
    threshold = timezone.now() - timedelta(seconds=cooldown_seconds)
    return DouyinReplyLog.objects.filter(
        conversation_id=conversation_id,
        matched_rule_id=rule_id,
        result='success',
        sent_at__gte=threshold,
    ).exists()


@sync_to_async
def _has_replied_to_peer_today(account_id: str, peer_sec_uid: str) -> bool:
    """同一账号对同一用户每天只自动回复一次。"""
    if not peer_sec_uid:
        return False
    from core.douyin.douyin_reply_log_model import DouyinReplyLog

    now = timezone.now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return DouyinReplyLog.objects.filter(
        account_id=account_id,
        conversation__peer_sec_uid=peer_sec_uid,
        result='success',
        sent_at__gte=day_start,
    ).exists()


@sync_to_async
def _mark_message_processed(message_id: str) -> None:
    from core.douyin.douyin_message_model import DouyinMessage
    DouyinMessage.objects.filter(id=message_id).update(processed=True)


@sync_to_async
def _mark_history_sync_completed(account_id: str) -> None:
    from core.douyin.douyin_account_model import DouyinAccount
    DouyinAccount.objects.filter(id=account_id).update(
        last_history_sync_at=timezone.now(),
    )


@sync_to_async
def _blacklist_hit(account_id: str, peer_sec_uid: str, peer_nickname: str, text: str) -> Optional[str]:
    """返回命中原因字符串；未命中返回 None"""
    from core.douyin.douyin_account_model import DouyinAccount
    from core.douyin.douyin_blacklist_model import DouyinBlacklist
    acc = DouyinAccount.objects.filter(id=account_id).first()
    group_id = acc.group_id if acc else None

    qs = DouyinBlacklist.objects.filter(status=True).filter(
        # scope 范围：全局 / 当前账号 / 当前分组
        # 这里用简单的 Python 侧过滤避免复杂 OR
    )
    for bl in qs:
        if bl.scope == 'account' and str(bl.account_id) != str(account_id):
            continue
        if bl.scope == 'group' and str(bl.group_id or '') != str(group_id or ''):
            continue
        if bl.blacklist_type == 'user' and bl.value == peer_sec_uid:
            DouyinBlacklist.objects.filter(id=bl.id).update(hit_count=bl.hit_count + 1)
            return f"用户黑名单: {peer_sec_uid}"
        if bl.blacklist_type == 'nickname_keyword' and bl.value and bl.value in (peer_nickname or ''):
            DouyinBlacklist.objects.filter(id=bl.id).update(hit_count=bl.hit_count + 1)
            return f"昵称黑名单: {bl.value}"
        if bl.blacklist_type == 'content_keyword' and bl.value and bl.value in (text or ''):
            DouyinBlacklist.objects.filter(id=bl.id).update(hit_count=bl.hit_count + 1)
            return f"内容黑名单: {bl.value}"
    return None


@sync_to_async
def _upsert_session(account_id: str, worker_id: str, status: str, metrics: dict) -> None:
    from core.douyin.douyin_account_model import DouyinAccount
    from core.douyin.douyin_session_model import DouyinSession
    acc = DouyinAccount.objects.filter(id=account_id).first()
    if acc is None:
        return
    defaults = {
        'worker_id': worker_id,
        'context_id': f'ctx_{account_id[:8]}',
        'status': status,
        'last_heartbeat': timezone.now(),
        'cpu_percent': metrics.get('cpu', 0),
        'memory_mb': metrics.get('mem', 0),
        'messages_today': metrics.get('messages_today', 0),
        'replies_today': metrics.get('replies_today', 0),
        'errors_today': metrics.get('errors_today', 0),
        'proxy_url': acc.proxy_url or None,
        'started_at': metrics.get('started_at') or timezone.now(),
    }
    DouyinSession.objects.update_or_create(account=acc, defaults=defaults)
    DouyinAccount.objects.filter(id=account_id).update(last_heartbeat=timezone.now())


@sync_to_async
def _log_event(account_id: Optional[str], event_type: str, level: str, title: str, detail: str = '', worker_id: str = '') -> None:
    from core.douyin.douyin_event_model import DouyinEvent
    try:
        DouyinEvent.objects.create(
            account_id=account_id,
            event_type=event_type,
            level=level,
            title=title,
            detail=detail,
            occurred_at=timezone.now(),
            worker_id=worker_id,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[worker] 写 DouyinEvent 失败: {e}")


def _in_silent_window(silent_start: Optional[str], silent_end: Optional[str]) -> bool:
    if not silent_start or not silent_end:
        return False
    try:
        ss = datetime.strptime(silent_start, "%H:%M:%S").time()
        se = datetime.strptime(silent_end, "%H:%M:%S").time()
    except ValueError:
        try:
            ss = datetime.strptime(silent_start, "%H:%M").time()
            se = datetime.strptime(silent_end, "%H:%M").time()
        except ValueError:
            return False
    now = datetime.now().time()
    if ss <= se:
        return ss <= now <= se
    return now >= ss or now <= se


def _can_process_reply(account_status: int, auto_reply_enabled: bool, session_active: bool) -> bool:
    """
    运行时是否允许继续扫描与回复。

    规则：
      - 关闭自动回复的账号永远不处理；
      - 已禁用账号永远不处理；
      - 在线账号直接处理；
      - 失效/待登录账号只要仍有活跃会话，就允许继续托管，避免被一次误判卡死。
    """
    if not auto_reply_enabled:
        return False
    if account_status == 3:
        return False
    return account_status == 1 or session_active


def _should_enforce_daily_peer_limit() -> bool:
    """
    调试阶段默认关闭“同一用户每天只自动回复一次”限制，优先验证链路畅通。
    线上若要恢复限制，设置 DOUYIN_ENFORCE_DAILY_PEER_REPLY_LIMIT=True。
    """
    return bool(getattr(settings, 'DOUYIN_ENFORCE_DAILY_PEER_REPLY_LIMIT', False))


# -------------------- Worker 主类 --------------------
class DouyinWorker:
    """常驻 worker。建议一个 Host 启动一个进程。"""

    def __init__(
        self,
        *,
        refresh_interval: int = 15,
        heartbeat_interval: int = 15,
        idle_poll_min: int = 10,
        idle_poll_max: int = 25,
    ) -> None:
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self.refresh_interval = refresh_interval
        self.heartbeat_interval = heartbeat_interval
        self.idle_poll_min = idle_poll_min
        self.idle_poll_max = idle_poll_max

        self._tasks: dict[str, asyncio.Task] = {}
        self._login_tasks: dict[str, asyncio.Task] = {}
        self._account_metrics: dict[str, dict] = {}
        self._redis = None
        self._stop = asyncio.Event()

    # ---------------- 主入口 ----------------
    async def run(self) -> None:
        logger.info(f"[worker] 启动 DouyinWorker worker_id={self.worker_id}")
        await BrowserManager.start()
        await self._connect_redis()

        try:
            await asyncio.gather(
                self._loop_refresh_accounts(),
                self._loop_heartbeat(),
                self._loop_redis_commands(),
            )
        finally:
            logger.info("[worker] 进入关停流程")
            for aid in list(self._tasks.keys()):
                await self._stop_account(aid)
            await BrowserManager.stop()
            if self._redis is not None:
                with suppress(Exception):
                    await self._redis.close()

    async def stop(self) -> None:
        self._stop.set()

    # ---------------- 子循环 ----------------
    async def _loop_refresh_accounts(self) -> None:
        while not self._stop.is_set():
            try:
                accounts = await _load_managed_accounts()
                wanted_ids = {a['id'] for a in accounts}
                existing = set(self._tasks.keys())

                # 新增
                for a in accounts:
                    if a['id'] not in existing:
                        self._tasks[a['id']] = asyncio.create_task(
                            self._account_loop(a), name=f"dy-acc-{a['id'][:8]}"
                        )
                        logger.info(f"[worker] 启动账号协程 {a['nickname']} ({a['id']})")

                # 移除
                for aid in existing - wanted_ids:
                    await self._stop_account(aid)
            except Exception as e:  # noqa: BLE001
                logger.exception(f"[worker] refresh accounts error: {e}")
            await self._sleep_or_stop(self.refresh_interval)

    async def _loop_heartbeat(self) -> None:
        while not self._stop.is_set():
            try:
                proc = psutil.Process(os.getpid())
                total_mem = proc.memory_info().rss / 1024 / 1024
                cpu = proc.cpu_percent(interval=None)
                per_account = max(1, len(self._tasks))
                for aid in list(self._tasks.keys()):
                    metrics = self._account_metrics.setdefault(aid, {
                        'messages_today': 0, 'replies_today': 0, 'errors_today': 0,
                        'started_at': timezone.now(),
                    })
                    await _upsert_session(
                        account_id=aid,
                        worker_id=self.worker_id,
                        status='running' if aid in self._tasks else 'idle',
                        metrics={
                            'cpu': cpu / per_account,
                            'mem': total_mem / per_account,
                            **metrics,
                        },
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[worker] heartbeat error: {e}")
            await self._sleep_or_stop(self.heartbeat_interval)

    async def _loop_redis_commands(self) -> None:
        """订阅 douyin:cmd:* 频道并派发"""
        if self._redis is None:
            logger.warning("[worker] Redis 未连接，跳过命令监听")
            return
        while not self._stop.is_set():
            try:
                pubsub = self._redis.pubsub()
                await pubsub.psubscribe(CMD_CHANNEL_PATTERN)
                logger.info(f"[worker] 已订阅 {CMD_CHANNEL_PATTERN}")
                async for msg in pubsub.listen():
                    if self._stop.is_set():
                        break
                    if msg is None or msg.get("type") != "pmessage":
                        continue
                    try:
                        channel = msg["channel"]
                        if isinstance(channel, bytes):
                            channel = channel.decode()
                        data = msg.get("data")
                        if isinstance(data, bytes):
                            data = data.decode()
                        try:
                            payload = json.loads(data) if data else {}
                        except json.JSONDecodeError:
                            payload = {"raw": data}
                        asyncio.create_task(self._dispatch_command(channel, payload))
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"[worker] 解析命令失败: {e}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[worker] pubsub 异常: {e}（5 秒后重连）")
                await asyncio.sleep(5)

    # ---------------- 命令派发 ----------------
    async def _dispatch_command(self, channel: str, payload: dict) -> None:
        # channel 形如 douyin:cmd:login:<account_id>
        parts = channel.split(':')
        if len(parts) < 4:
            return
        _, _, action, account_id = parts[0], parts[1], parts[2], parts[3]
        logger.info(f"[worker] 收到命令 action={action} account={account_id} payload={payload}")

        if action == 'login':
            acc = await _fetch_account_orm(account_id)
            if acc is None:
                logger.warning(f"[worker] login 命令忽略：账号不存在 account={account_id}")
                return
            logger.info(
                f"[worker] 即时扫码登录任务派发 account={account_id} nickname={acc.nickname!r}"
            )
            if account_id in self._login_tasks:
                logger.warning(f"[worker] 已存在扫码登录任务，忽略重复派发 account={account_id}")
                return
            await BrowserManager.close_context(account_id)
            from core.douyin.runtime.storage import delete_account_runtime_state
            delete_account_runtime_state(account_id)
            self._start_login_task(account_id, acc)
        elif action == 'login_cancel':
            logger.info(f"[worker] login_cancel 命令 account={account_id}")
            await self._cancel_login_task(account_id)
        elif action == 'manual_reply':
            logger.info(f"[worker] manual_reply 命令 account={account_id} payload={payload}")
            await self._send_manual_reply(
                account_id,
                conversation_id=str(payload.get('conversation_id') or ''),
                text=str(payload.get('text') or ''),
            )
        elif action == 'manual_auto_reply':
            logger.info(f"[worker] manual_auto_reply 命令 account={account_id} payload={payload}")
            await self._run_manual_auto_reply_test(
                account_id,
                conversation_id=str(payload.get('conversation_id') or ''),
                text=str(payload.get('text') or ''),
            )
        elif action == 'logout':
            logger.info(f"[worker] logout 命令 account={account_id}")
            await self._cancel_login_task(account_id)
            await self._stop_account(account_id)
            from core.douyin.runtime.storage import delete_account_runtime_state
            delete_account_runtime_state(account_id)
            logger.info(f"[worker] logout 完成 account={account_id} storage/profile 已清除")
        elif action == 'focus':
            logger.info(f"[worker] focus 命令 account={account_id}")
            await self._focus_account_page(account_id)
        elif action == 'session':
            # channel 形如 douyin:cmd:session:<account_id>:pause  (payload.action 也可)
            sub = parts[4] if len(parts) > 4 else payload.get('action', '')
            logger.info(f"[worker] session 命令 account={account_id} sub={sub}")
            if sub in ('stop', 'restart'):
                await self._stop_account(account_id)
            # pause / resume 当前版本简单处理：只改 DB 状态；
            # 主循环下一轮 refresh 会根据 status 重新决定是否启停

    async def _stop_account(self, account_id: str) -> None:
        task = self._tasks.pop(account_id, None)
        if task is not None:
            task.cancel()
            with suppress(Exception):
                await task
        await BrowserManager.close_context(account_id)
        logger.info(f"[worker] 已停止账号协程 {account_id}")

    def _start_login_task(self, account_id: str, account) -> asyncio.Task:
        task = asyncio.create_task(
            self._run_login_task(account_id, account),
            name=f"dy-login-{account_id[:8]}",
        )
        self._login_tasks[account_id] = task
        return task

    async def _run_login_task(self, account_id: str, account) -> bool:
        try:
            return await scan_qrcode_login(account)
        except asyncio.CancelledError:
            logger.info(f"[worker] 已取消扫码登录任务 account={account_id}")
            raise
        finally:
            self._login_tasks.pop(account_id, None)

    async def _cancel_login_task(self, account_id: str) -> None:
        task = self._login_tasks.pop(account_id, None)
        if task is None:
            logger.info(f"[worker] 当前无可取消的扫码登录任务 account={account_id}")
            return
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
        await BrowserManager.close_context(account_id)
        logger.info(f"[worker] 扫码登录任务已取消并关闭上下文 account={account_id}")

    async def _focus_account_page(self, account_id: str) -> None:
        account = await _fetch_account_orm(account_id)
        if account is None:
            logger.warning(f"[worker] focus 忽略：账号不存在 account={account_id}")
            return
        try:
            from core.douyin.runtime import selectors as S
            target_url = S.CREATOR_IM if getattr(account, 'status', None) == 1 else S.CREATOR_HOME
        except Exception:
            target_url = None
        url = await BrowserManager.focus_account_page(account, target_url=target_url)
        if url:
            logger.info(f"[worker] 已聚焦账号页面 account={account_id} url={url}")
        else:
            logger.warning(f"[worker] 聚焦账号页面失败 account={account_id}")

    async def _send_manual_reply(self, account_id: str, *, conversation_id: str, text: str) -> None:
        if not conversation_id or not text.strip():
            logger.warning(f"[worker] manual_reply 参数不完整 account={account_id}")
            return
        account = await _fetch_account_orm(account_id)
        conv = await _fetch_conversation(account_id, conversation_id)
        if account is None or conv is None:
            logger.warning(f"[worker] manual_reply 忽略：账号或会话不存在 account={account_id} conv={conversation_id}")
            return

        context = await BrowserManager.get_or_create_context(account)
        page = await context.new_page()
        try:
            from core.douyin.runtime import selectors as S
            for im_url in getattr(S, 'CREATOR_IM_CANDIDATES', [S.CREATOR_IM]):
                await page.goto(im_url, wait_until='domcontentloaded', timeout=30000)
                await asyncio.sleep(2)
                if page.url.startswith(im_url) or 'following/chat' in page.url or '/im' in page.url:
                    break

            target_nick = conv.peer_nickname or ''
            target_preview = (conv.last_message_preview or '').strip()
            item, tab_label = await _find_conversation_item_across_tabs(
                page,
                target_nick=target_nick,
                target_preview=target_preview,
            )
            if item is None:
                raise RuntimeError(f"未找到手动回复目标会话：{conv.peer_nickname or conv.peer_sec_uid}")
            logger.info(
                f"[reply] 找到手动回复目标会话 account={account_id} "
                f"tab={tab_label} peer={conv.peer_nickname!r}"
            )
            await human_click(item)
            await asyncio.sleep(1.0)
            normalized_text = text.strip()
            before_debug = await dump_manual_reply_debug(
                page,
                account_id=account_id,
                conversation_id=conversation_id,
                phase='before_send',
                text=normalized_text,
            )
            logger.info(f"[reply] 手动回复发送前证据 account={account_id} path={before_debug}")
            await _send_one(page, normalized_text)
            confirmed = await confirm_text_present_in_recent_messages(page, normalized_text)
            if not confirmed:
                after_debug = await dump_manual_reply_debug(
                    page,
                    account_id=account_id,
                    conversation_id=conversation_id,
                    phase='after_send_missing',
                    text=normalized_text,
                )
                logger.warning(f"[reply] 手动回复发送后未确认，证据 account={account_id} path={after_debug}")
                raise RuntimeError("平台侧未确认发送成功：最近消息列表中未看到刚发送的文本")
            after_debug = await dump_manual_reply_debug(
                page,
                account_id=account_id,
                conversation_id=conversation_id,
                phase='after_send_confirmed',
                text=normalized_text,
            )
            await write_manual_out_message(account_id, conversation_id, normalized_text)
            await push_to_user(account.owner_id, 'reply_sent', {
                'account_id': account_id,
                'peer_nickname': conv.peer_nickname,
                'rule': 'manual',
                'text_preview': normalized_text[:60],
            })
            logger.info(
                f"[reply] ✔ 手动回复成功 account={account_id} peer={conv.peer_nickname!r} "
                f"conv={conversation_id} debug={after_debug}"
            )
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[reply] ✘ 手动回复失败 account={account_id} conv={conversation_id}: {e}")
            await push_to_user(account.owner_id, 'reply_failed', {
                'account_id': account_id,
                'peer_nickname': conv.peer_nickname,
                'error': str(e),
            })
        finally:
            with suppress(Exception):
                await page.close()

    async def _run_manual_auto_reply_test(self, account_id: str, *, conversation_id: str, text: str) -> None:
        if not conversation_id or not text.strip():
            logger.warning(f"[reply] manual_auto_reply 参数不完整 account={account_id}")
            return
        account = await _fetch_account_orm(account_id)
        conv = await _fetch_conversation(account_id, conversation_id)
        if account is None or conv is None:
            logger.warning(f"[reply] manual_auto_reply 忽略：账号或会话不存在 account={account_id} conv={conversation_id}")
            return
        rules = await _load_rules_for_account(account_id)
        synthetic = await _create_synthetic_inbound_message(account_id, conversation_id, text.strip())
        logger.info(
            f"[reply] ▶ 手动触发自动回复测试 account={account_id} conv={conversation_id} "
            f"text={text.strip()!r} rules={len(rules)}"
        )
        await self._handle_one_message(account, synthetic, rules, str(account.owner_id))

    # ---------------- 单账号循环 ----------------
    async def _account_loop(self, a: dict) -> None:
        account_id = a['id']
        owner_id = a['owner_id']
        nickname = a.get('nickname') or account_id[:8]
        self._account_metrics[account_id] = {
            'messages_today': 0, 'replies_today': 0, 'errors_today': 0,
            'started_at': timezone.now(),
            'login_failures': 0,
            'login_retry_not_before': None,
        }
        logger.info(
            f"[worker] ▶ 账号协程启动 account={account_id} nickname={nickname!r} "
            f"status={a['status']} mode={a.get('work_mode')} "
            f"interval=[{a.get('min_interval_seconds')},{a.get('max_interval_seconds')}]s "
            f"quota={a.get('daily_reply_quota')} today={a.get('reply_today')}"
        )

        # 未登录/失效账号不再在 worker 启动时自动弹登录，避免用户还未点击登录就打开浏览器。
        # 登录流程仅由前端显式点击“扫码登录”后通过 Redis 命令触发。
        try:
            if a['status'] in (0, 2):
                acc = await _fetch_account_orm(account_id)
                if (
                    acc is not None
                    and acc.pending_verification_until
                    and timezone.now() < acc.pending_verification_until
                ):
                    wait_seconds = int((acc.pending_verification_until - timezone.now()).total_seconds())
                    logger.warning(
                        f"[worker] 账号待人工验证，暂停自动重登 account={account_id} "
                        f"type={acc.pending_verification_type or 'unknown'} "
                        f"wait={max(wait_seconds, 1)}s"
                    )
                    await asyncio.sleep(min(max(wait_seconds, 1), 60))
                    return
                retry_not_before = self._account_metrics[account_id].get('login_retry_not_before')
                if retry_not_before and timezone.now() < retry_not_before:
                    wait_seconds = int((retry_not_before - timezone.now()).total_seconds())
                    logger.warning(
                        f"[worker] 登录熔断中，跳过本轮 account={account_id} wait={max(wait_seconds, 1)}s"
                    )
                    await asyncio.sleep(max(wait_seconds, 1))
                    return
                logger.info(
                    f"[worker] 账号等待用户显式触发登录 account={account_id} status={a['status']}"
                )
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[worker] 账号 {account_id} 登录异常: {e}")

        loop_count = 0
        while not self._stop.is_set():
            loop_count += 1
            try:
                # 静默时段不扫描
                if _in_silent_window(a.get('silent_start'), a.get('silent_end')):
                    logger.info(
                        f"[worker] ⏸ 账号处于静默时段 account={account_id} "
                        f"window=[{a.get('silent_start')},{a.get('silent_end')}] 休眠 60s"
                    )
                    await asyncio.sleep(60)
                    continue
                # 达到日上限
                acc_row = await _fetch_account_orm(account_id)
                if acc_row is None:
                    logger.info(f"[worker] 账号 {account_id} 已删除，退出循环")
                    return
                if acc_row.reply_today >= acc_row.daily_reply_quota:
                    logger.info(
                        f"[worker] ⏸ 账号已达日回复上限 account={account_id} "
                        f"today={acc_row.reply_today}/{acc_row.daily_reply_quota} 休眠 120s"
                    )
                    await asyncio.sleep(120)
                    continue
                session_active = await _session_is_active(account_id)
                if not _can_process_reply(acc_row.status, acc_row.auto_reply_enabled, session_active):
                    logger.info(
                        f"[worker] 账号不可用于自动回复 account={account_id} "
                        f"status={acc_row.status} auto_reply_enabled={acc_row.auto_reply_enabled} "
                        f"session_active={session_active} 休眠 30s 等待状态恢复"
                    )
                    await asyncio.sleep(30)
                    continue
                if acc_row.status != 1 and session_active:
                    logger.warning(
                        f"[worker] 账号状态非在线，但会话仍活跃，继续托管 account={account_id} "
                        f"status={acc_row.status}"
                    )
                if loop_count == 1 or loop_count % 10 == 0:
                    valid = await is_login_valid(acc_row)
                    if not valid:
                        logger.warning(
                            f"[worker] 检测到在线账号登录态不可用，打回重新登录 account={account_id}"
                        )
                        await mark_account_login_invalid(
                            account_id,
                            "在线校验失败：业务页面不可用",
                            keep_pending_verification=bool(
                                acc_row.pending_verification_until
                                and timezone.now() < acc_row.pending_verification_until
                            ),
                        )
                        await asyncio.sleep(15)
                        return

                backfill_mode = acc_row.last_history_sync_at is None

                # 扫收件箱
                logger.info(
                    f"[worker] #{loop_count} 开始扫描收件箱 account={account_id} ({nickname}) "
                    f"backfill={'Y' if backfill_mode else 'N'}"
                )
                new_msgs: list[ScannedMessage] = await scan_inbox(
                    acc_row,
                    include_recent_without_unread=backfill_mode,
                )
                if backfill_mode:
                    await _mark_history_sync_completed(account_id)
                    logger.info(f"[worker] 已完成首次历史会话补扫标记 account={account_id}")
                self._account_metrics[account_id]['messages_today'] += len(new_msgs)
                logger.info(
                    f"[worker] #{loop_count} 扫描完成 account={account_id} "
                    f"new_msgs={len(new_msgs)} messages_today={self._account_metrics[account_id]['messages_today']}"
                )

                if new_msgs:
                    rules = await _load_rules_for_account(account_id)
                    logger.info(
                        f"[worker] 加载规则 account={account_id} 启用中={len(rules)} 条"
                    )
                    for m in new_msgs:
                        await self._handle_one_message(acc_row, m, rules, owner_id)
                # 随机间隔
                wait = random.uniform(self.idle_poll_min, self.idle_poll_max)
                logger.debug(f"[worker] 账号 {account_id} 休眠 {wait:.1f}s 后继续")
                await asyncio.sleep(wait)
            except LoginGateDetected as e:
                acc_row = await _fetch_account_orm(account_id)
                keep_pending = bool(
                    acc_row and acc_row.pending_verification_until
                    and timezone.now() < acc_row.pending_verification_until
                )
                logger.warning(
                    f"[worker] IM 页面进入登录门面，账号打回重新登录 account={account_id} err={e}"
                )
                await mark_account_login_invalid(
                    account_id,
                    f"IM 页面仍为登录门面: {e}",
                    keep_pending_verification=keep_pending,
                )
                await asyncio.sleep(15)
                return
            except asyncio.CancelledError:
                logger.info(f"[worker] 账号协程被取消 {account_id}")
                raise
            except Exception as e:  # noqa: BLE001
                self._account_metrics[account_id]['errors_today'] += 1
                await _log_event(account_id, 'unknown_error', 'error', '账号循环异常', str(e), self.worker_id)
                logger.exception(f"[worker] account_loop error account={account_id}: {e}")
                await asyncio.sleep(20)

    # ---------------- 单消息处理 ----------------
    async def _handle_one_message(
        self,
        account,
        msg: ScannedMessage,
        rules: list,
        owner_id: str,
    ) -> None:
        account_id = str(account.id)
        peer = msg.peer_nickname or msg.peer_sec_uid or '?'
        preview = (msg.text or '')[:40].replace('\n', ' ')
        logger.info(
            f"[reply] ▶ 处理入向消息 account={account_id} peer={peer!r} "
            f"msg_id={msg.message_id} text={preview!r}"
        )

        session_active = await _session_is_active(account_id)
        if not _can_process_reply(account.status, account.auto_reply_enabled, session_active):
            logger.info(
                f"[reply] ⏭ 跳过：账号已关闭自动回复 account={account_id} "
                f"peer={peer!r} auto_reply_enabled={getattr(account, 'auto_reply_enabled', None)} "
                f"status={getattr(account, 'status', None)} session_active={session_active}"
            )
            await _mark_message_processed(msg.message_id)
            await _log_event(
                account_id,
                'reply_skipped',
                'info',
                '跳过：账号已关闭自动回复',
                f"peer={peer}",
                self.worker_id,
            )
            return

        # 黑名单
        reason = await _blacklist_hit(account_id, msg.peer_sec_uid, msg.peer_nickname or '', msg.text)
        if reason:
            logger.info(f"[reply] ⏭ 跳过：命中黑名单 account={account_id} peer={peer!r} reason={reason}")
            await _log_event(account_id, 'blacklist_hit', 'info', '跳过：命中黑名单', reason, self.worker_id)
            return

        if _should_enforce_daily_peer_limit() and await _has_replied_to_peer_today(account_id, msg.peer_sec_uid):
            logger.info(
                f"[reply] ⏭ 跳过：同一用户今日已自动回复 account={account_id} "
                f"peer={peer!r} peer_sec_uid={msg.peer_sec_uid}"
            )
            await _mark_message_processed(msg.message_id)
            await _log_event(
                account_id,
                'rate_limit',
                'info',
                '跳过：同一用户今日已自动回复',
                f"peer_sec_uid={msg.peer_sec_uid}",
                self.worker_id,
            )
            return

        # 规则匹配
        rule = match_rule(msg.text, rules, incoming_channel='dm', at=datetime.now())
        if rule is None:
            logger.info(
                f"[reply] ⏭ 跳过：无命中规则 account={account_id} peer={peer!r} "
                f"text={preview!r} 候选规则={len(rules)}"
            )
            await _log_event(account_id, 'reply_failed', 'info', '跳过：无命中规则',
                             f"text={msg.text[:60]}", self.worker_id)
            return
        logger.info(
            f"[reply] ✔ 命中规则 account={account_id} rule={rule.name!r} "
            f"match_type={rule.match_type} priority={rule.priority} "
            f"cooldown={rule.cooldown_seconds}s send_mode={rule.send_mode}"
        )

        # 冷却
        if await _is_in_cooldown(msg.conversation_id, str(rule.id), rule.cooldown_seconds):
            logger.info(
                f"[reply] ⏭ 跳过：规则冷却中 account={account_id} "
                f"rule={rule.name!r} conv={msg.conversation_id}"
            )
            await _log_event(account_id, 'rate_limit', 'info', '跳过：规则冷却中',
                             f"rule={rule.name}", self.worker_id)
            return

        # 打开会话（点击会话列表已在 inbox 扫描时完成，这里复用同一个 page）
        context = await BrowserManager.get_or_create_context(account)
        page = await context.new_page()
        try:
            from core.douyin.runtime import selectors as S
            logger.info(f"[reply] 打开 IM 页面 account={account_id}")
            for im_url in getattr(S, 'CREATOR_IM_CANDIDATES', [S.CREATOR_IM]):
                await page.goto(im_url, wait_until='domcontentloaded', timeout=30000)
                await asyncio.sleep(2)
                if page.url.startswith(im_url) or 'following/chat' in page.url or '/im' in page.url:
                    break

            target_nick = msg.peer_nickname or ''
            target_preview = (msg.text or '').strip()
            item, tab_label = await _find_conversation_item_across_tabs(
                page,
                target_nick=target_nick,
                target_preview=target_preview,
            )
            if item is None:
                raise RuntimeError(f"未找到对方会话：nickname={msg.peer_nickname}")
            logger.info(
                f"[reply] 点击会话成功 account={account_id} peer={peer!r} tab={tab_label}"
            )
            await human_click(item)
            await asyncio.sleep(1.5)

            log_id = await send_reply(
                account, page,
                conversation_id=msg.conversation_id,
                trigger_message_id=msg.message_id,
                rule=rule,
                peer_nickname=msg.peer_nickname or '',
            )
            self._account_metrics[account_id]['replies_today'] += 1
            logger.info(
                f"[reply] ✔ 回复成功 account={account_id} peer={peer!r} "
                f"rule={rule.name!r} reply_log={log_id} "
                f"replies_today={self._account_metrics[account_id]['replies_today']}"
            )
            await push_to_user(owner_id, 'reply_sent', {
                'account_id': account_id,
                'peer_nickname': msg.peer_nickname,
                'rule': rule.name,
                'text_preview': (msg.text or '')[:60],
            })
        except Exception as e:  # noqa: BLE001
            self._account_metrics[account_id]['errors_today'] += 1
            logger.exception(
                f"[reply] ✘ 回复失败 account={account_id} peer={peer!r} "
                f"rule={rule.name!r} err={type(e).__name__}: {e}"
            )
            await _log_event(account_id, 'reply_failed', 'error', '回复失败',
                             f"{type(e).__name__}: {e}", self.worker_id)
            await push_to_user(owner_id, 'reply_failed', {
                'account_id': account_id,
                'peer_nickname': msg.peer_nickname,
                'error': str(e),
            })
        finally:
            with suppress(Exception):
                await page.close()

    # ---------------- 基础设施 ----------------
    async def _connect_redis(self) -> None:
        try:
            import redis.asyncio as aioredis
            url = getattr(settings, 'REDIS_URL', None)
            if not url:
                logger.warning("[worker] settings.REDIS_URL 未配置，命令频道将不可用")
                return
            self._redis = aioredis.from_url(url, decode_responses=False)
            await self._redis.ping()
            logger.info(f"[worker] Redis 连接成功 {url}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[worker] Redis 连接失败: {e}，命令频道将不可用")
            self._redis = None

    async def _sleep_or_stop(self, seconds: float) -> None:
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
