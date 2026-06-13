#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: sign/mstoken.py
@Desc: msToken 处理

抖音 web 请求的查询参数 `msToken`：
  - 服务端会通过 Set-Cookie 下发一个 msToken（cookie），前端 msSDK 也会本地生成/刷新一个
    用于查询参数的 msToken。两者相关但不要求逐字节相等。
  - 实践中，imapi 等接口对查询参数 msToken 的校验较宽松：能在 cookie 中拿到就复用 cookie 的；
    拿不到则本地生成一个长度/字符集合法的随机串即可（与社区实现一致）。

策略：优先复用账号 cookie 里的 msToken（最接近真实），否则随机生成。
"""
from __future__ import annotations

import random
import string
from typing import Mapping, Optional

# msToken 字符集（base64url 风格，与抖音 web 观察到的取值域一致）
_MSTOKEN_CHARS = string.ascii_letters + string.digits + "-_"
_DEFAULT_LEN = 126


def random_mstoken(length: int = _DEFAULT_LEN) -> str:
    """生成一个长度/字符集合法的随机 msToken。"""
    return "".join(random.choice(_MSTOKEN_CHARS) for _ in range(length))


def resolve_mstoken(
    cookies: Optional[Mapping[str, str]] = None,
    *,
    fallback_length: int = _DEFAULT_LEN,
) -> str:
    """
    解析本次请求要用的 msToken：优先用 cookie 里的 msToken，否则随机生成。

    Args:
        cookies: 账号 cookie 字典（name -> value）。
        fallback_length: 随机生成时的长度。
    """
    if cookies:
        token = cookies.get("msToken") or cookies.get("msToken_ss")
        if token:
            return token
    return random_mstoken(fallback_length)
