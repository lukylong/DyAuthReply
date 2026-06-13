#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: transport/__init__.py
@Desc: AccountTransport 工厂（纯协议，脱浏览器）

调用方:
    from core.douyin.runtime.transport import build_transport
    transport = build_transport(
        backend='http_protocol',
        ws_inbound=settings.DOUYIN_TRANSPORT_WS_INBOUND,
    )
    await transport.start(account)
    await transport.scan_inbox(account)

设计上每个账号都拥有自己的一份 transport 实例（HttpProtocolTransport 持有账号本地的
JsSignProvider；FrontierWsDecorator 持有账号本地的 WS 连接 / 事件队列）。

历史的 'browser' backend（BrowserTransport + WsInboundDecorator）已随浏览器子系统
一并移除；backend 仅保留 'http_protocol'。
"""
from __future__ import annotations

import logging

from core.douyin.runtime.transport.base import AccountTransport, InboundEvent
from core.douyin.runtime.transport.dual_run import DualRunDecorator
from core.douyin.runtime.transport.http_protocol import HttpProtocolTransport
from core.douyin.runtime.transport.sign_types import SignerUnavailable

logger = logging.getLogger(__name__)

__all__ = [
    "AccountTransport",
    "InboundEvent",
    "DualRunDecorator",
    "HttpProtocolTransport",
    "SignerUnavailable",
    "build_transport",
    "TRANSPORT_BACKEND_HTTP_PROTOCOL",
]


TRANSPORT_BACKEND_HTTP_PROTOCOL = "http_protocol"


def build_transport(
    *,
    backend: str = TRANSPORT_BACKEND_HTTP_PROTOCOL,
    ws_inbound: bool = False,
    dual_run: bool = False,
) -> AccountTransport:
    """
    构造一份纯协议 AccountTransport。

    Args:
        backend: 仅支持 'http_protocol'；其它值会 warning 后按 http_protocol 处理。
        ws_inbound: 是否启用 frontier-im WS 帧快路径（FrontierWsDecorator）。
        dual_run: 是否启用“影子编码 + 真路径真发”对账装饰（每次发送额外编码一份
            protobuf 落日志，不真发；零真发风险）。

    装饰顺序（外层先执行）：
        FrontierWsDecorator → DualRunDecorator → HttpProtocolTransport
    """
    if backend != TRANSPORT_BACKEND_HTTP_PROTOCOL:
        logger.warning(
            f"[transport.factory] backend={backend!r} 已不支持（仅 'http_protocol'），按 http_protocol 处理"
        )

    base: AccountTransport = HttpProtocolTransport()

    if dual_run:
        base = DualRunDecorator(base)

    if ws_inbound:
        from core.douyin.runtime.transport.frontier_ws import FrontierWsDecorator
        return FrontierWsDecorator(base)
    return base
