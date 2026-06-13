#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: __init__.py
@Desc: Douyin Runtime - 抖音纯 HTTP 协议运行时层
        私信收发与会话扫描的协议实现都放在这里，由独立的 douyin_worker 进程加载，
        无浏览器依赖（签名走 Node.js + dy_ab.js）。
"""
