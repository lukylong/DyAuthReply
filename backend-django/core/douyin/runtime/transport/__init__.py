#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: transport/__init__.py
@Desc: AccountTransport 工厂

调用方:
    from core.douyin.runtime.transport import build_transport
    transport = build_transport(
        backend=settings.DOUYIN_TRANSPORT_BACKEND,        # 'browser' | 'http_protocol'
        ws_inbound=settings.DOUYIN_TRANSPORT_WS_INBOUND,
    )
    await transport.start(account)
    await transport.scan_inbox(account)

设计上每个账号都拥有自己的一份 transport 实例（WsInboundDecorator 持有账号本地的
事件 queue / signal；HttpProtocolTransport 持有账号本地的 SignProvider）。

backend 选择：
  - 'browser'         : Phase 1/2 默认实现，DOM 扫描 + 文本框输入
  - 'http_protocol'   : Phase 3 hybrid 协议化，浏览器只做签名，HTTP 走 httpx；
                        协议路径未实现 / 失败时自动 fallback 到 BrowserTransport

ws_inbound 装饰：
  - 任意 backend 都可以叠 WsInboundDecorator 做 "WS 帧立即唤醒下一轮 scan"
"""
from __future__ import annotations

import logging

from core.douyin.runtime.transport.base import AccountTransport, InboundEvent
from core.douyin.runtime.transport.browser import BrowserTransport
from core.douyin.runtime.transport.dual_run import DualRunDecorator
from core.douyin.runtime.transport.http_protocol import HttpProtocolTransport
from core.douyin.runtime.transport.sign_provider import SignProvider, SignerUnavailable
from core.douyin.runtime.transport.ws_decorator import WsInboundDecorator

logger = logging.getLogger(__name__)

__all__ = [
    "AccountTransport",
    "InboundEvent",
    "BrowserTransport",
    "DualRunDecorator",
    "HttpProtocolTransport",
    "SignProvider",
    "SignerUnavailable",
    "WsInboundDecorator",
    "build_transport",
    "TRANSPORT_BACKEND_BROWSER",
    "TRANSPORT_BACKEND_HTTP_PROTOCOL",
]


TRANSPORT_BACKEND_BROWSER = "browser"
TRANSPORT_BACKEND_HTTP_PROTOCOL = "http_protocol"

_VALID_BACKENDS = (TRANSPORT_BACKEND_BROWSER, TRANSPORT_BACKEND_HTTP_PROTOCOL)


def build_transport(
    *,
    backend: str = TRANSPORT_BACKEND_BROWSER,
    ws_inbound: bool = False,
    dual_run: bool = False,
) -> AccountTransport:
    """
    构造一份 AccountTransport。

    Args:
        backend: 'browser' | 'http_protocol'。未知值会回退到 'browser' 并打 warning。
        ws_inbound: 是否启用 WS 帧快路径。任意 backend 都可叠加。
        dual_run: 是否启用"影子编码 + 真路径真发"对账装饰。每次发送会**额外**
            编码一份 protobuf 落到日志，但不真发。配合 sniffer 抓的真实 IM 流量
            做事后字段映射对账，是 Phase 3 协议格式验证的最稳手段。零真发风险。

    装饰顺序（外层先执行）：
        WsInboundDecorator → DualRunDecorator → HttpProtocolTransport / BrowserTransport

    Returns:
        AccountTransport 实例（worker 每账号持一份）。
    """
    if backend not in _VALID_BACKENDS:
        logger.warning(
            f"[transport.factory] 未知 backend={backend!r}，回退到 'browser'。"
            f"合法取值: {_VALID_BACKENDS}"
        )
        backend = TRANSPORT_BACKEND_BROWSER

    base: AccountTransport
    if backend == TRANSPORT_BACKEND_HTTP_PROTOCOL:
        # hybrid 模式：协议路径未启用的 verb 自动 fallback 到 BrowserTransport
        base = HttpProtocolTransport()
    else:
        base = BrowserTransport()

    if dual_run:
        base = DualRunDecorator(base)

    if ws_inbound:
        # 脱浏览器（http_protocol）用主动连 frontier-im WS；browser 仍用监听浏览器 WS
        if backend == TRANSPORT_BACKEND_HTTP_PROTOCOL:
            from core.douyin.runtime.transport.frontier_ws import FrontierWsDecorator
            return FrontierWsDecorator(base)
        return WsInboundDecorator(base)
    return base
