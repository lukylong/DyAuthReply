#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: core/kuaishou/runtime/__init__.py
@Desc: 快手 worker 运行时

与 core.douyin.runtime 平行的运行时层。当前阶段提供：
  - transport 抽象接口（base）与 HTTP 协议占位实现（http_protocol，待逆向）
  - rule_loader：从共用层 core.social 加载规则 / 黑名单
  - worker：常驻主循环骨架

快手私信 HTTP 协议逆向完成后，在 transport/http_protocol.py 中填充真实收发逻辑即可。
"""
