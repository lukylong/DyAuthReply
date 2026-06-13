#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: transport/sign_types.py
@Desc: 签名后端共享类型 —— 中性模块，不依赖浏览器/Playwright。

历史上 SignedResponse / SignerUnavailable 定义在 sign_provider.py（浏览器版签名
后端）里，被 js_sign_provider / local_sign_provider / http_protocol 共用。脱浏览器
后这些消费方不应再 import 浏览器栈，故把共享类型下沉到本模块。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


class SignerUnavailable(RuntimeError):
    """签名后端不可用（引擎未就绪 / cookie 过期 / 签名抛错）。

    上层 (HttpProtocolTransport) 捕获后按 strict 配置决定是报错还是降级。
    """


class LoginExpiredError(RuntimeError):
    """账号登录态已失效（cookie/凭证过期）—— 明确的失效信号。

    scan/send 在响应被 health.classify_signed_response 判定为 login_expired 时抛出，
    worker 捕获后调用 mark_account_login_invalid 把账号打回（status=2）并 WS 推送，
    而不是笼统记成 unknown_error。携带 http_status / proto_status_code 便于排障。
    """

    def __init__(self, message: str, *, http_status: int | None = None,
                 proto_status_code: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.proto_status_code = proto_status_code


@dataclass
class SignedResponse:
    """signed_fetch 的标准化返回。

    body 双形态：
      - text: 当 fetch 成功且能 utf-8 解码时填入，便于 JSON 接口直接 .json()
      - content: 永远填二进制（即便是 JSON 接口也填 utf-8 编码）
        protobuf / 任意二进制接口必须用 .content，不要用 .text
    """

    status: int
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    text: str = ""
    content: bytes = b""

    def json(self) -> Any:
        return json.loads(self.text)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300
