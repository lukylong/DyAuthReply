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
from core.douyin.runtime.inbox import LoginGateDetected, ScannedMessage, scan_inbox
from core.douyin.runtime.login import is_login_valid, mark_account_login_invalid, scan_qrcode_login
from core.douyin.runtime.matcher import match as match_rule
from core.douyin.runtime.sender import send_reply
from core.douyin.runtime.ws_notify import push_event_log, push_to_user

if TYPE_CHECKING:
    from core.douyin.douyin_account_model import DouyinAccount

logger = logging.getLogger(__name__)


def _normalize_nickname(name: Optional[str]) -> str:
    return (name or "").strip().lower().replace(" ", "")

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
            task = asyncio.create_task(
                self._run_login_task(account_id, acc),
                name=f"dy-login-{account_id[:8]}",
            )
            self._login_tasks[account_id] = task
        elif action == 'login_cancel':
            logger.info(f"[worker] login_cancel 命令 account={account_id}")
            await self._cancel_login_task(account_id)
        elif action == 'logout':
            logger.info(f"[worker] logout 命令 account={account_id}")
            await self._cancel_login_task(account_id)
            await self._stop_account(account_id)
            from core.douyin.runtime.storage import delete_storage_state
            delete_storage_state(account_id)
            logger.info(f"[worker] logout 完成 account={account_id} storage 已清除")
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

    async def _run_login_task(self, account_id: str, account) -> None:
        try:
            await scan_qrcode_login(account)
        except asyncio.CancelledError:
            logger.info(f"[worker] 已取消扫码登录任务 account={account_id}")
            raise
        finally:
            self._login_tasks.pop(account_id, None)

    async def _cancel_login_task(self, account_id: str) -> None:
        task = self._login_tasks.pop(account_id, None)
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
        await BrowserManager.close_context(account_id)
        logger.info(f"[worker] 扫码登录任务已取消并关闭上下文 account={account_id}")

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

        # 若账号从未登录 / 登录失效 → 直接跑一次扫码登录
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
                    f"[worker] 账号需要扫码登录 account={account_id} status={a['status']}"
                )
                if acc is not None:
                    ok = await scan_qrcode_login(acc)
                    if not ok:
                        metrics = self._account_metrics[account_id]
                        failures = int(metrics.get('login_failures') or 0) + 1
                        metrics['login_failures'] = failures
                        # 连续失败 3 次后，进入 10 分钟冷却，降低风控触发概率
                        if failures >= 3:
                            cooldown_until = timezone.now() + timedelta(minutes=10)
                            metrics['login_retry_not_before'] = cooldown_until
                            logger.warning(
                                f"[worker] 登录连续失败触发熔断 account={account_id} "
                                f"failures={failures} cooldown_until={cooldown_until.isoformat()}"
                            )
                        else:
                            metrics['login_retry_not_before'] = None
                        logger.warning(
                            f"[worker] 账号 {nickname} ({account_id}) 登录失败，30s 后重试"
                        )
                        await asyncio.sleep(30)
                        return
                    self._account_metrics[account_id]['login_failures'] = 0
                    self._account_metrics[account_id]['login_retry_not_before'] = None
                    logger.info(
                        f"[worker] 账号 {nickname} ({account_id}) 登录成功，进入扫描循环"
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
                if acc_row.status != 1:
                    logger.info(
                        f"[worker] 账号非在线 account={account_id} status={acc_row.status} "
                        f"休眠 30s 等待状态恢复"
                    )
                    await asyncio.sleep(30)
                    continue
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

                # 扫收件箱
                logger.info(
                    f"[worker] #{loop_count} 开始扫描收件箱 account={account_id} ({nickname})"
                )
                new_msgs: list[ScannedMessage] = await scan_inbox(acc_row)
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

        # 黑名单
        reason = await _blacklist_hit(account_id, msg.peer_sec_uid, msg.peer_nickname or '', msg.text)
        if reason:
            logger.info(f"[reply] ⏭ 跳过：命中黑名单 account={account_id} peer={peer!r} reason={reason}")
            await _log_event(account_id, 'blacklist_hit', 'info', '跳过：命中黑名单', reason, self.worker_id)
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
            await page.goto(S.CREATOR_IM, wait_until='domcontentloaded', timeout=30000)
            await asyncio.sleep(2)

            # 在会话列表里找到对应昵称并点进去
            items = page.locator(S.IM_CONVERSATION_ITEMS[0])
            count = await items.count()
            logger.info(
                f"[reply] 定位会话 account={account_id} peer={peer!r} 会话项数={count}"
            )
            hit = False
            target_nick = _normalize_nickname(msg.peer_nickname)
            target_preview = (msg.text or "").strip()[:12]
            for i in range(min(count, 20)):
                it = items.nth(i)
                try:
                    nick_loc = it.locator(S.IM_CONV_NICKNAME[0]).first
                    nick_txt = ""
                    if await nick_loc.count():
                        nick_txt = (await nick_loc.inner_text()).strip()
                    nick_norm = _normalize_nickname(nick_txt)
                    nick_exact = bool(target_nick) and nick_norm == target_nick
                    nick_fuzzy = bool(target_nick) and (target_nick in nick_norm or nick_norm in target_nick)

                    preview_hit = False
                    if target_preview:
                        try:
                            preview_loc = it.locator(S.IM_CONV_LAST_MESSAGE[0]).first
                            if await preview_loc.count():
                                preview_txt = (await preview_loc.inner_text()).strip()
                                preview_hit = target_preview in preview_txt
                        except Exception:
                            preview_hit = False

                    if nick_exact or nick_fuzzy or preview_hit:
                        await it.click()
                        hit = True
                        logger.info(
                            f"[reply] 点击会话成功 account={account_id} peer={peer!r} idx={i} "
                            f"nick_exact={nick_exact} nick_fuzzy={nick_fuzzy} preview_hit={preview_hit}"
                        )
                        break
                except Exception:
                    continue
            if not hit:
                raise RuntimeError(f"未找到对方会话：nickname={msg.peer_nickname}")
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
