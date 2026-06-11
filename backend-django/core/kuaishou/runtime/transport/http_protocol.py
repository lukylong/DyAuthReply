#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: core/kuaishou/runtime/transport/http_protocol.py
@Desc: 快手私信 HTTP 协议传输实现（占位骨架）

目标：参考抖音 core.douyin.runtime.transport.http_protocol 的思路，
     用账号 Cookie + 动态签名直连快手私信 IM HTTP 接口，避免浏览器频繁刷新触发风控。

待逆向完成的关键点（协议调研产出后在此填充）：
  1. 收件箱拉取接口（会话列表 / 未读消息）与分页游标
  2. 发送私信接口（文本 / 链接）与必填参数
  3. 签名算法（快手常见 __NS_sig3 / sig / kpf / kpn 等）
  4. 长连接方案（快手 IM 多走 WebSocket）用于入向消息快路径
  5. Cookie/token 维持与失效检测

在协议就绪前，所有方法继承基类抛 NotImplementedError，worker 会捕获并降级为"等待"，不会崩溃。
"""
from __future__ import annotations

import logging

from core.kuaishou.runtime.transport.base import KuaishouTransport

logger = logging.getLogger(__name__)


class HttpProtocolTransport(KuaishouTransport):
    """快手私信 HTTP 协议传输（占位）。

    协议逆向完成后，覆写 ensure_login / scan_inbox / send_text / send_reply 即可。
    """

    BASE_URL = "https://im.kuaishou.com"  # TODO: 抓包确认真实域名/路径

    def __init__(self, account):
        super().__init__(account)
        self._logged_placeholder_warning = False

    def _warn_once(self, verb: str) -> None:
        if not self._logged_placeholder_warning:
            logger.warning(
                "[kuaishou.http] 协议未实现：%s（account=%s）。"
                "请先完成快手私信 HTTP 协议逆向，再填充 http_protocol.py。",
                verb, getattr(self.account, 'id', '?'),
            )
            self._logged_placeholder_warning = True

    # 以下方法暂未实现，沿用基类的 NotImplementedError；
    # _warn_once 仅用于在 worker 侧打印一次友好提示（worker 调用前可先调用）。
