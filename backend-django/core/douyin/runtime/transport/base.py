#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: transport/base.py
@Desc: AccountTransport 抽象层

为 douyin worker 把"如何收/发消息"这件事抽象成一个 Transport 接口，
允许后续在不修改 worker 主逻辑的前提下：

  - BrowserTransport: 现有的 Playwright DOM 扫描 + 文本框输入
  - WsInboundDecorator(BrowserTransport): 用 WS 帧做"有新消息"信号，
      第一时间唤醒 BrowserTransport.scan_inbox 把内容真正落库（fallback 策略）
  - 未来可扩展: HttpProtocolTransport（直接用 HTTP IM 接口收发）

Phase 2 第一步：先把接口立起来；BrowserTransport 是默认实现，
行为与改造前完全一致；feature flag 不开就是零侵入。
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator, List, Optional

if TYPE_CHECKING:
    from core.douyin.douyin_account_model import DouyinAccount
    from core.douyin.douyin_rule_model import DouyinRule
    from core.douyin.runtime.inbox import ScannedMessage


@dataclass
class InboundEvent:
    """
    通用的"有新消息"信号。

    来源可能是：
      - 'ws':       WS 帧解码出的 IM 事件（最快路径）
      - 'http':     HTTP 历史拉取的兜底
      - 'browser':  浏览器侧 DOM 监听到的页面变化（兜底）

    设计原则：本事件**不**保证字段完整 / 文本可信；它只是一个"该 scan_inbox 了"的提示，
    具体落库内容仍由 transport.scan_inbox 决定（fallback 策略）。
    """

    account_id: str
    source: str  # 'ws' | 'http' | 'browser'
    ts: float  # epoch seconds
    conversation_hint: Optional[str] = None  # peer sec_uid / nickname / conv_id 任意线索
    raw: dict = field(default_factory=dict)


class AccountTransport(ABC):
    """
    一个账号一份 Transport 实例（由 worker 持有）。

    - start()/stop() 在账号生命周期开始/结束时调用，用于挂载/卸载 WS 监听等资源。
    - scan_inbox()/send_reply() 是 worker 调用的主要 verbs。
    - inbound_events() 提供异步事件流；BrowserTransport 默认空流，
      WsInboundDecorator 才会真正产出事件。

    实现层不应自己直接修改 DB / 推送 ws_notify，这些都由 worker 统一负责。
    """

    name: str = "base"

    # ---------------- 生命周期 ----------------
    async def start(self, account: "DouyinAccount") -> None:
        """挂资源（attach 监听等）。默认 no-op。"""
        return None

    async def stop(self, account: "DouyinAccount") -> None:
        """卸资源。默认 no-op。"""
        return None

    # ---------------- 主 verbs ----------------
    @abstractmethod
    async def scan_inbox(
        self,
        account: "DouyinAccount",
        *,
        max_conversations: int = 15,
        include_recent_without_unread: bool = False,
        conversation_hint: Optional[str] = None,
    ) -> List["ScannedMessage"]:
        """扫一次收件箱，返回新增的入向消息（已落库）。"""

    @abstractmethod
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
        """
        发送自动回复。

        - page 仍由 worker 传入（既兼容 BrowserTransport 直接复用打开的会话页面，
          也方便未来 HttpProtocolTransport 忽略 page 直接走 HTTP）。
        - 返回 DouyinReplyLog.id。
        """

    @abstractmethod
    async def send_text(
        self,
        account: "DouyinAccount",
        page: Any,
        *,
        conversation_id: str,
        text: str,
    ) -> str:
        """
        发送一段任意文本（手动回复用）。与 send_reply 的差异：

        - 没有触发消息 / 规则上下文，纯人工指令；
        - 不写 DouyinReplyLog，但需要落 direction='out' 的 DouyinMessage（用于回声黑名单）；
        - 返回新建 DouyinMessage.id。

        发送失败应抛异常，让上层做证据落盘 + 用户提示。
        """

    # ---------------- 事件流（可选） ----------------
    async def inbound_events(self) -> AsyncIterator[InboundEvent]:  # pragma: no cover - interface
        """
        默认空流。WsInboundDecorator 会重写。

        worker 用法：
            async for evt in transport.inbound_events():
                # 立即唤醒 scan_inbox
        """
        if False:
            yield  # type: ignore[unreachable]
        return

    async def wait_for_inbound_signal(self, *, timeout: float) -> Optional[InboundEvent]:
        """
        快路径辅助：等到下一个入向信号或超时返回 None。

        默认实现等待 timeout 后返回 None（行为退化为"按节流轮询"）。
        WsInboundDecorator 会重写为真正的快速唤醒。
        """
        try:
            await asyncio.sleep(timeout)
        except asyncio.CancelledError:
            raise
        return None
