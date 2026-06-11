#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: core/kuaishou/runtime/transport/__init__.py
@Desc: 快手传输层工厂

build_transport(account) 根据配置返回具体 transport 实现。
当前仅有 HTTP 协议占位实现；协议逆向完成后在此扩展灰度/回退策略（参考抖音 transport）。
"""
from __future__ import annotations

import os

from core.kuaishou.runtime.transport.base import KuaishouTransport
from core.kuaishou.runtime.transport.http_protocol import HttpProtocolTransport


def build_transport(account) -> KuaishouTransport:
    """根据账号 + 环境变量构建 transport。

    预留 KUAISHOU_TRANSPORT_BACKEND 开关（默认 http_protocol），后续可加 browser 回退。
    """
    backend = os.environ.get('KUAISHOU_TRANSPORT_BACKEND', 'http_protocol')
    # 目前仅实现 http_protocol 占位；未知 backend 一律落到 http_protocol。
    return HttpProtocolTransport(account)


__all__ = ['KuaishouTransport', 'HttpProtocolTransport', 'build_transport']
