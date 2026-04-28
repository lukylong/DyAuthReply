#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: transport/browser.py
@Desc: BrowserTransport - 复用现有 Playwright 实现的默认 transport

它是一层"无副作用包装"：把 worker 之前直接 import 的
  - core.douyin.runtime.inbox.scan_inbox
  - core.douyin.runtime.sender.send_reply
封装成 AccountTransport 接口实现，行为完全等价。

所以打开 / 关闭 feature flag DOUYIN_TRANSPORT_WS_INBOUND 都不会影响默认路径。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, List

from core.douyin.runtime.inbox import scan_inbox
from core.douyin.runtime.sender import (
    _send_one,
    confirm_text_present_in_recent_messages,
    send_reply,
    write_manual_out_message,
)
from core.douyin.runtime.transport.base import AccountTransport

if TYPE_CHECKING:
    from core.douyin.douyin_account_model import DouyinAccount
    from core.douyin.douyin_rule_model import DouyinRule
    from core.douyin.runtime.inbox import ScannedMessage

logger = logging.getLogger(__name__)


class BrowserTransport(AccountTransport):
    """
    默认 transport：基于 Playwright DOM 扫描和文本框输入。

    - scan_inbox: 直接调用 inbox.scan_inbox（DOM 扫描）
    - send_reply: 直接调用 sender.send_reply（DOM 输入）
    - inbound_events: 不产出事件（保持轮询行为）

    一个进程内可以共享同一个 BrowserTransport 实例（无账号本地状态）。
    """

    name = "browser"

    async def scan_inbox(
        self,
        account: "DouyinAccount",
        *,
        max_conversations: int = 15,
        include_recent_without_unread: bool = False,
    ) -> List["ScannedMessage"]:
        return await scan_inbox(
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
        return await send_reply(
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
        normalized = text.strip()
        if not normalized:
            raise ValueError("send_text 不能发送空文本")
        await _send_one(page, normalized)
        confirmed = await confirm_text_present_in_recent_messages(page, normalized)
        if not confirmed:
            # 不静默吞，由上层负责落证据 + 推送失败事件
            raise RuntimeError("平台侧未确认发送成功：最近消息列表中未看到刚发送的文本")
        return await write_manual_out_message(str(account.id), conversation_id, normalized)
