#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: core/kuaishou/runtime/worker.py
@Desc: 快手 worker 常驻主循环（骨架）

职责（对齐抖音 worker，协议接通后逐步充实）：
  - 每 refresh_interval 秒扫描 DB：挑出需要托管的账号（status=1 & auto_reply_enabled & work_mode in auto/hybrid）
  - 每账号一个 _account_loop 协程：ensure_login → scan_inbox → 落库 → 黑名单/配额/静默/冷却 → 规则匹配 → 发送 → 写回复日志
  - 周期性向 KuaishouSession 写心跳

当前传输层（HttpProtocolTransport）尚未实现协议，scan_inbox/send 会抛 NotImplementedError，
本骨架会捕获并降级为"等待 + 一次性告警"，不会崩溃，便于先联通管理后台与数据链路。
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
from datetime import datetime
from typing import Optional

from asgiref.sync import sync_to_async
from django.utils import timezone

from core.kuaishou.runtime.rule_loader import blacklist_hit, load_rules_for_account
from core.kuaishou.runtime.transport import build_transport
from core.kuaishou.runtime.transport.base import InboundMessage
from core.social.matcher import match as match_rule

logger = logging.getLogger(__name__)


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


@sync_to_async
def _discover_managed_accounts() -> list:
    from core.kuaishou.kuaishou_account_model import KuaishouAccount

    return list(
        KuaishouAccount.objects.filter(
            status=1,
            auto_reply_enabled=True,
            work_mode__in=('auto', 'hybrid'),
            is_deleted=False,
        ).order_by('-priority')
    )


@sync_to_async
def _persist_inbound(account_id: str, msg: InboundMessage):
    """落库会话 + 消息（按唯一约束去重），返回 (conversation, message, created)。"""
    from core.kuaishou.kuaishou_conversation_model import KuaishouConversation
    from core.kuaishou.kuaishou_message_model import KuaishouMessage

    conv, _ = KuaishouConversation.objects.update_or_create(
        account_id=account_id,
        peer_user_id=msg.peer_user_id,
        defaults={
            'peer_nickname': msg.peer_nickname,
            'peer_avatar': msg.peer_avatar,
            'platform_conversation_id': msg.platform_conversation_id,
            'last_message_at': msg.received_at or timezone.now(),
            'last_message_preview': (msg.text or '')[:255],
        },
    )
    message, created = KuaishouMessage.objects.get_or_create(
        conversation=conv,
        external_msg_id=msg.external_msg_id,
        defaults={
            'direction': 'in',
            'content_type': msg.content_type,
            'content': msg.text,
            'raw_payload': msg.raw_payload,
            'received_at': msg.received_at or timezone.now(),
        },
    )
    return conv, message, created


@sync_to_async
def _write_reply_log(account_id: str, conv_id: Optional[str], msg_id: Optional[str],
                     rule_id: Optional[str], text: str, links: list, result: str,
                     error: Optional[str], duration_ms: int):
    from core.kuaishou.kuaishou_reply_log_model import KuaishouReplyLog

    KuaishouReplyLog.objects.create(
        account_id=account_id,
        conversation_id=conv_id,
        trigger_message_id=msg_id,
        matched_rule_id=rule_id,
        reply_text=text,
        reply_links=links or [],
        result=result,
        error_message=error,
        duration_ms=duration_ms,
        sent_at=timezone.now() if result == 'success' else None,
    )


@sync_to_async
def _heartbeat(account_id: str, worker_id: str, status: str = 'running'):
    from core.kuaishou.kuaishou_session_model import KuaishouSession

    now = timezone.now()
    session, created = KuaishouSession.objects.update_or_create(
        account_id=account_id,
        defaults={'worker_id': worker_id, 'status': status, 'last_heartbeat': now},
    )
    if created or not session.started_at:
        session.started_at = now
        session.save(update_fields=['started_at'])


class KuaishouWorker:
    """快手 worker 主控。"""

    def __init__(self, refresh_interval: int = 15, heartbeat_interval: int = 20):
        self.worker_id = _worker_id()
        self.refresh_interval = refresh_interval
        self.heartbeat_interval = heartbeat_interval
        self._tasks: dict[str, asyncio.Task] = {}
        self._running = True

    async def run(self):
        logger.info("[kuaishou.worker] 启动 worker_id=%s", self.worker_id)
        while self._running:
            try:
                accounts = await _discover_managed_accounts()
                alive_ids = {str(a.id) for a in accounts}
                # 启动新账号协程
                for acc in accounts:
                    aid = str(acc.id)
                    if aid not in self._tasks or self._tasks[aid].done():
                        self._tasks[aid] = asyncio.create_task(self._account_loop(acc))
                # 回收已移除账号
                for aid in list(self._tasks):
                    if aid not in alive_ids:
                        self._tasks[aid].cancel()
                        self._tasks.pop(aid, None)
            except Exception as e:  # noqa: BLE001
                logger.exception("[kuaishou.worker] 调度循环异常: %s", e)
            await asyncio.sleep(self.refresh_interval)

    async def _account_loop(self, account):
        account_id = str(account.id)
        transport = build_transport(account)
        logger.info("[kuaishou.worker] 托管账号 account=%s", account_id)
        try:
            while self._running:
                await _heartbeat(account_id, self.worker_id)
                try:
                    await transport.ensure_login()
                    messages = await transport.scan_inbox()
                    await self._handle_messages(account, transport, messages)
                except NotImplementedError as e:
                    logger.warning("[kuaishou.worker] 协议未实现，等待中 account=%s: %s", account_id, e)
                    await asyncio.sleep(60)
                except Exception as e:  # noqa: BLE001
                    logger.exception("[kuaishou.worker] 账号循环异常 account=%s: %s", account_id, e)
                    await asyncio.sleep(30)
                await asyncio.sleep(self._next_interval(account))
        except asyncio.CancelledError:
            logger.info("[kuaishou.worker] 账号协程取消 account=%s", account_id)
        finally:
            await transport.close()

    async def _handle_messages(self, account, transport, messages):
        account_id = str(account.id)
        if not messages:
            return
        rules = await load_rules_for_account(account_id)
        for msg in messages:
            conv, message, created = await _persist_inbound(account_id, msg)
            if not created:
                continue  # 已处理过的消息，去重跳过
            reason = await blacklist_hit(
                account_id, account.group_id, msg.peer_user_id, msg.peer_nickname or '', msg.text,
            )
            if reason:
                await _write_reply_log(account_id, str(conv.id), str(message.id), None,
                                       '', [], 'skipped', reason, 0)
                continue
            rule = match_rule(msg.text, rules, incoming_channel='dm', at=datetime.now())
            if rule is None:
                continue
            await self._send_with_rule(account_id, transport, conv, message, rule, msg)

    async def _send_with_rule(self, account_id, transport, conv, message, rule, msg):
        started = timezone.now()
        text = rule.reply_text or ''
        links = rule.links or []
        try:
            result = await transport.send_reply(
                msg.peer_user_id, text, links,
                send_mode=rule.send_mode, conversation_id=msg.platform_conversation_id,
            )
            duration = int((timezone.now() - started).total_seconds() * 1000)
            await _write_reply_log(
                account_id, str(conv.id), str(message.id), str(rule.id), text, links,
                'success' if result.success else 'failed', result.error_message, duration,
            )
        except NotImplementedError as e:
            await _write_reply_log(account_id, str(conv.id), str(message.id), str(rule.id),
                                   text, links, 'failed', f"协议未实现: {e}", 0)

    def _next_interval(self, account) -> float:
        import random
        lo = max(1, account.min_interval_seconds or 8)
        hi = max(lo, account.max_interval_seconds or 25)
        return random.uniform(lo, hi)

    def stop(self):
        self._running = False
