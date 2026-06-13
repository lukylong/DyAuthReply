#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: sign/signer.py
@Desc: 统一签名入口（纯函数，无 Django / 无网络 / 无浏览器）

把"给一个抖音 web 请求补齐 msToken + a_bogus"收敛成一个函数：

    result = sign_params(
        params="device_platform=webapp&aid=6383&...",   # 不含 a_bogus 的查询串
        body="",                                          # POST body（protobuf 见下方说明）
        user_agent=account_ua,
        fp=account_fp,                                    # 浏览器指纹，按账号固定
        cookies=account_cookies,                          # 取 msToken；可为空
    )
    final_query = result.query          # 形如 "...&msToken=..&a_bogus=.."

设计要点：
  - a_bogus 必须对"最终要发送的查询串(已含 msToken、不含 a_bogus)"计算，顺序固定：
        ① 确定 msToken（cookie 优先，否则随机）
        ② 把 msToken 拼进 params
        ③ 对 params 计算 a_bogus
        ④ 追加 a_bogus
  - a_bogus 与账号登录态无关，是 (params+body+UA+fp) 的纯函数 ⇒ 一个进程可服务所有账号。

protobuf body 的 body-hash 是否参与 a_bogus，需以真实抓包为准（关键闸门）。
当前默认 body="" 不参与；待抓包确认后在此处统一调整，全链路自动生效。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

from core.douyin.runtime.transport.sign.abogus import ABogus
from core.douyin.runtime.transport.sign.mstoken import resolve_mstoken

# GET [0,1,8]；POST [0,1,14]（14 兼容 8，POST 同样可编码 params）
_OPTIONS_GET = [0, 1, 8]
_OPTIONS_POST = [0, 1, 14]


@dataclass
class SignResult:
    """签名结果。"""
    query: str                       # 含 msToken + a_bogus 的完整查询串（不带前导 ?）
    a_bogus: str
    ms_token: str
    user_agent: str
    fp: str
    body: str = ""
    extra_headers: dict = field(default_factory=dict)


def sign_params(
    params: str,
    *,
    body: str = "",
    user_agent: str,
    fp: str,
    cookies: Optional[Mapping[str, str]] = None,
    method: str = "GET",
    ms_token: Optional[str] = None,
) -> SignResult:
    """
    给查询串补齐 msToken + a_bogus。

    Args:
        params: 不含 a_bogus 的查询串（可含或不含 msToken；不含时自动补）。
        body: POST body 字符串（protobuf 二进制是否参与见模块说明）。
        user_agent: 该账号的 UA（与指纹/IP 绑定，勿混用）。
        fp: 该账号的浏览器指纹串（ABogus 的 fp）。
        cookies: 该账号 cookie（用于取 msToken）。
        method: GET / POST。
        ms_token: 显式指定 msToken；不传则按 cookie/随机解析。

    Returns:
        SignResult
    """
    token = ms_token or resolve_mstoken(cookies)

    # ② 把 msToken 拼进 params（若调用方已带则不重复加）
    if "msToken=" not in params:
        sep = "" if params.endswith("&") or params == "" else "&"
        params_with_token = f"{params}{sep}msToken={token}"
    else:
        params_with_token = params

    options = _OPTIONS_POST if method.upper() == "POST" else _OPTIONS_GET
    ab = ABogus(fp=fp, user_agent=user_agent, options=options)

    # ③④ 生成 a_bogus 并追加（generate_abogus 返回已拼好 a_bogus 的 params）
    new_params, a_bogus, used_ua, used_body = ab.generate_abogus(
        params=params_with_token, body=body
    )

    return SignResult(
        query=new_params,
        a_bogus=a_bogus,
        ms_token=token,
        user_agent=used_ua,
        fp=fp,
        body=used_body,
    )
