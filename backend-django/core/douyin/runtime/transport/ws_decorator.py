#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: transport/ws_decorator.py
@Desc: WsInboundDecorator - 用 WS 帧作为"立即 scan_inbox"的快路径触发器

策略选择：fallback（用户已确认）。这个装饰器**不**直接用 WS 帧解出来的内容入库，
只用作"有变化就立刻扫一次"的低延时信号。真实落库内容仍由 inner.scan_inbox 决定，
这避免了"protobuf 没 schema → 解错 → 入错数据"的硬伤。

工作流：
  1. start(account): 拿到该账号的 BrowserContext
     - context.on('page', ...)  → 新 page 都 attach
     - 已有 page 也都 attach
     - page.on('websocket', ...)  → 拿到 WS
     - ws.on('framereceived', ...) → 帧入 frontier 解码 → 命中即向 _signal 发事件
  2. wait_for_inbound_signal(timeout): 异步等到下一个事件 / 超时
  3. scan_inbox / send_reply: 直接委派 inner

线程/进程安全：每个账号 BrowserTransport 实例一份装饰器实例（worker 每账号实例化）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Any, AsyncIterator, List, Optional

from core.douyin.runtime.browser import BrowserManager
from core.douyin.runtime.transport.base import AccountTransport, InboundEvent
from core.douyin.runtime.transport.frontier import (
    decode_frontier_frame,
    is_im_websocket_url,
)

if TYPE_CHECKING:
    from core.douyin.douyin_account_model import DouyinAccount
    from core.douyin.douyin_rule_model import DouyinRule
    from core.douyin.runtime.inbox import ScannedMessage

logger = logging.getLogger(__name__)


class WsInboundDecorator(AccountTransport):
    """
    将 BrowserTransport 装饰成"WS 唤醒 + 浏览器扫描内容"。

    - 不替换内容来源：scan_inbox / send_reply 都直接委派 inner
    - 仅加一条快路径：WS 帧 → 解 frontier → 命中即 _signal.set() + 入 queue
    """

    name = "ws_inbound"

    def __init__(self, inner: AccountTransport) -> None:
        self._inner = inner
        # 每个账号一份事件 queue + signal
        self._signal: asyncio.Event = asyncio.Event()
        self._queue: "asyncio.Queue[InboundEvent]" = asyncio.Queue(maxsize=64)
        self._account_id: Optional[str] = None
        self._attached_page_ids: set[int] = set()
        self._attached_ws_ids: set[int] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # 防抖：极短时间内来一堆帧就只发一个 signal
        self._last_signal_ts: float = 0.0
        self._signal_min_gap_sec: float = 0.5

    # ---------------- 生命周期 ----------------
    async def start(self, account: "DouyinAccount") -> None:
        self._account_id = str(account.id)
        self._loop = asyncio.get_running_loop()
        try:
            ctx = await BrowserManager.get_or_create_context(account)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[transport.ws] 启动时未能拿到 BrowserContext，仅退化为节流轮询 "
                f"account={self._account_id} err={type(e).__name__}: {e}"
            )
            return

        # 监听后续新 page
        try:
            ctx.on("page", self._on_new_page)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[transport.ws] context.on('page') 失败 account={self._account_id} err={e}")

        # 已存在的 page 也 attach
        with suppress(Exception):
            for p in list(ctx.pages):
                self._attach_page(p)

        logger.info(
            f"[transport.ws] WsInboundDecorator 启动 account={self._account_id} "
            f"existing_pages={len(getattr(ctx, 'pages', []) or [])}"
        )

    async def stop(self, account: "DouyinAccount") -> None:
        # 浏览器侧的 page/ws 监听跟随 context 关闭一同释放，这里只清本地缓存
        self._attached_page_ids.clear()
        self._attached_ws_ids.clear()
        self._signal.set()  # 唤醒所有等待者退出
        logger.info(f"[transport.ws] WsInboundDecorator 停止 account={self._account_id}")

    # ---------------- 主 verbs（委派） ----------------
    async def scan_inbox(
        self,
        account: "DouyinAccount",
        *,
        max_conversations: int = 15,
        include_recent_without_unread: bool = False,
    ) -> List["ScannedMessage"]:
        return await self._inner.scan_inbox(
            account,
            max_conversations=max_conversations,
            include_recent_without_unread=include_recent_without_unread,
        )

    async def send_reply(
        self,
        account: "DouyinAccount",
        page: Any,
        *,
        conversation_id: str,
        trigger_message_id: str,
        rule: "DouyinRule",
        peer_nickname: str = "",
    ) -> str:
        return await self._inner.send_reply(
            account,
            page,
            conversation_id=conversation_id,
            trigger_message_id=trigger_message_id,
            rule=rule,
            peer_nickname=peer_nickname,
        )

    async def send_text(
        self,
        account: "DouyinAccount",
        page: Any,
        *,
        conversation_id: str,
        text: str,
    ) -> str:
        return await self._inner.send_text(
            account,
            page,
            conversation_id=conversation_id,
            text=text,
        )

    # ---------------- 事件流 ----------------
    async def inbound_events(self) -> AsyncIterator[InboundEvent]:
        while True:
            evt = await self._queue.get()
            yield evt

    async def wait_for_inbound_signal(self, *, timeout: float) -> Optional[InboundEvent]:
        """
        等到下一个 IM 入向信号，或超时返回 None。

        worker 用法：把这个塞进主循环代替 asyncio.sleep(idle_poll)；
        命中即立刻进入下一轮 scan_inbox，没命中就走超时退化路径。
        """
        try:
            await asyncio.wait_for(self._signal.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        # 拿到信号后清空 + 取最新一条事件返回
        self._signal.clear()
        evt: Optional[InboundEvent] = None
        try:
            evt = self._queue.get_nowait()
        except asyncio.QueueEmpty:
            evt = None
        # 把后面挤压的事件丢掉（避免连续多次唤醒）
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        return evt

    # ---------------- 内部 attach ----------------
    def _on_new_page(self, page: Any) -> None:
        try:
            self._attach_page(page)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[transport.ws] _attach_page 失败 account={self._account_id} err={e}")

    def _attach_page(self, page: Any) -> None:
        pid = id(page)
        if pid in self._attached_page_ids:
            return
        self._attached_page_ids.add(pid)
        try:
            page.on("websocket", self._on_websocket)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[transport.ws] page.on('websocket') 失败 account={self._account_id} err={e}")

    def _on_websocket(self, ws: Any) -> None:
        url = getattr(ws, "url", "") or ""
        if not is_im_websocket_url(url):
            return
        wsid = id(ws)
        if wsid in self._attached_ws_ids:
            return
        self._attached_ws_ids.add(wsid)
        logger.info(f"[transport.ws] 监听到 IM WS account={self._account_id} url={url}")

        def on_recv(payload: Any) -> None:
            try:
                self._handle_frame(payload, url=url)
            except Exception as e:  # noqa: BLE001
                logger.debug(
                    f"[transport.ws] 处理帧失败 account={self._account_id} "
                    f"err={type(e).__name__}: {e}"
                )

        with suppress(Exception):
            ws.on("framereceived", on_recv)

    def _handle_frame(self, payload: Any, *, url: str) -> None:
        if isinstance(payload, str):
            try:
                data = payload.encode("latin1")
            except Exception:  # noqa: BLE001
                return
        elif isinstance(payload, (bytes, bytearray, memoryview)):
            data = bytes(payload)
        else:
            return

        hint = decode_frontier_frame(data, url=url)
        if hint is None:
            return
        # outbound 帧不触发扫描（避免自己发的消息也唤醒一次扫）
        if hint.direction == "outbound":
            return

        # 防抖
        now = time.time()
        if now - self._last_signal_ts < self._signal_min_gap_sec:
            return
        self._last_signal_ts = now

        evt = InboundEvent(
            account_id=self._account_id or "",
            source="ws",
            ts=now,
            conversation_hint=hint.conversation_hint,
            raw={
                "keywords": hint.keywords_matched,
                "text_preview": (hint.text_candidate or "")[:120],
                "server_ts_ms": hint.server_ts_ms,
                "direction": hint.direction,
            },
        )
        # 入队（满了就丢最旧）+ 设 signal
        try:
            self._queue.put_nowait(evt)
        except asyncio.QueueFull:
            with suppress(Exception):
                self._queue.get_nowait()
            with suppress(Exception):
                self._queue.put_nowait(evt)
        self._signal.set()
        logger.info(
            f"[transport.ws] 收到 IM 信号 account={self._account_id} "
            f"keywords={hint.keywords_matched} text={evt.raw['text_preview']!r}"
        )
