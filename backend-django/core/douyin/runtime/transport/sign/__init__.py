#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: core/douyin/runtime/transport/sign/__init__.py
@Desc: 抖音 web 协议签名层（无浏览器）

  - abogus.py        : vendored 自 f2 (Johnserf-Seed/f2, Apache-2.0)，纯 Python 生成 a_bogus
  - _gmssl_compat.py : 内置纯 Python SM3，替代 gmssl 外部依赖（abogus 仅用到 SM3）
  - mstoken.py       : 本地生成 msToken 查询参数
  - signer.py        : 统一签名入口 sign_url(url, body, ua, fp) -> 带签名的 url + 头

设计：a_bogus 是 (url 参数 + body + UA + 浏览器指纹) 的纯函数，与账号登录态无关，
因此一个进程即可为所有账号签名（共享签名池），不需要每账号一个浏览器。
"""
