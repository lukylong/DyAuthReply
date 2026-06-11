#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: core/kuaishou/runtime/transport/base.py
@Desc: 快手传输层抽象接口

定义 worker 与"快手私信通道"之间的统一契约。具体实现（HTTP 协议 / 浏览器）只要实现这些方法，
worker 主循环即可平台无关地收发消息。接口对齐抖音 transport，便于复用 worker 调度逻辑。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class InboundMessage:
    """一条入向私信（worker 扫描收件箱得到）"""
    external_msg_id: str
    peer_user_id: str
    peer_nickname: Optional[str] = None
    peer_avatar: Optional[str] = None
    text: str = ''
    content_type: str = 'text'
    received_at: Optional[datetime] = None
    platform_conversation_id: Optional[str] = None
    raw_payload: dict = field(default_factory=dict)


@dataclass
class SendResult:
    """一次发送的结果"""
    success: bool
    error_message: Optional[str] = None
    external_msg_id: Optional[str] = None
    raw_payload: dict = field(default_factory=dict)


class KuaishouTransport:
    """快手传输层抽象基类。

    所有方法默认抛 NotImplementedError；协议逆向完成后在 HttpProtocolTransport 中覆写实现。
    """

    def __init__(self, account):
        self.account = account

    async def ensure_login(self) -> bool:
        """确保登录态有效（必要时刷新 Cookie/签名）。返回是否在线。"""
        raise NotImplementedError("快手登录态维持待实现")

    async def scan_inbox(self, *, limit: int = 20) -> List[InboundMessage]:
        """拉取最新未读/入向消息列表。"""
        raise NotImplementedError("快手收件箱扫描待实现（HTTP 协议逆向）")

    async def send_text(self, peer_user_id: str, text: str, *, conversation_id: Optional[str] = None) -> SendResult:
        """向指定用户发送纯文本。"""
        raise NotImplementedError("快手发送文本待实现（HTTP 协议逆向）")

    async def send_reply(
        self,
        peer_user_id: str,
        text: str,
        links: Optional[List[dict]] = None,
        *,
        send_mode: str = 'multi_message',
        conversation_id: Optional[str] = None,
    ) -> SendResult:
        """按 send_mode 发送文本 + 链接组合回复。"""
        raise NotImplementedError("快手发送回复待实现（HTTP 协议逆向）")

    async def close(self) -> None:
        """释放资源（连接 / 浏览器上下文等）。"""
        return None
