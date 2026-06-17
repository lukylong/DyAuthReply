#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: transport/wire/im_send_pb2.py
@Desc: 基于权威 protobuf（dy_request_pb2，vendored 自 DouYin_Spider/static/Request_pb2）
        的私信「发送」请求体编码器。

为什么单独一个 pb2 编码器（而不是手写 codec im_protocol.py）：
  imapi `/v1/message/send` 是**写接口**，强校验 bd-ticket-guard 鉴权字段
  （envelope.token / ts_sign / sdk_cert / auth_type / biz / access / reuqest_sign）。
  手写 codec 早期从 hex 推断，缺这些字段 → 抖音返回 `empty token`。本模块按权威
  Request.proto 组装，字段 100% 准确，签名复用 js_signer（已对齐 DouYin_Spider）。

对照 DouYin_Spider/builder/proto.py: build_normal_request + build_send_message_request。
"""
from __future__ import annotations

import base64
import json
import random
import time
import uuid
from typing import Optional

from core.douyin.runtime.transport.sign import js_signer
from core.douyin.runtime.transport.wire import dy_request_pb2 as R

SEND_MESSAGE_CMD_ID = 100
# DouYin_Spider build_normal_request 用的常量（平台升级 build_number 可能失效）
IM_SDK_VERSION = "1.1.3"
IM_BUILD_NUMBER = "5fa6ff1:Detached: 5fa6ff1111fd53aafc4c753505d3c93daad74d27"
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _fake_webid(n: int = 19) -> str:
    return "".join(random.choice("0123456789") for _ in range(n))


def _build_envelope(
    cmd: int,
    bd_ticket: dict,
    *,
    s_v_web_id: str = "",
    webid: str = "",
    user_agent: str = _DEFAULT_UA,
) -> "R.Request":
    """组装 Request envelope，填齐 bd-ticket-guard 鉴权字段。

    对照 DouYin_Spider build_normal_request：token=ticket、ts_sign、sdk_cert=base64(client_cert)、
    auth_type=4、biz/access。
    """
    req = R.Request()
    req.cmd = cmd
    req.sequence_id = random.randint(10000, 11000)
    req.sdk_version = IM_SDK_VERSION
    req.token = bd_ticket.get("ticket", "") or ""
    req.refer = 3
    req.inbox_type = 0
    req.build_number = IM_BUILD_NUMBER
    req.device_id = "0"
    req.device_platform = "douyin_pc"
    h = req.headers
    h["session_aid"] = "6383"
    h["session_did"] = "0"
    h["app_name"] = "douyin_pc"
    h["priority_region"] = "cn"
    h["user_agent"] = user_agent
    h["cookie_enabled"] = "true"
    h["browser_language"] = "zh-CN"
    h["browser_platform"] = "Win32"
    h["browser_name"] = "Mozilla"
    h["browser_version"] = user_agent.split("Mozilla/")[-1]
    h["browser_online"] = "true"
    h["screen_width"] = "1707"
    h["screen_height"] = "960"
    h["referer"] = ""
    h["timezone_name"] = "Etc/GMT-8"
    h["deviceId"] = "0"
    h["webid"] = webid or _fake_webid()
    h["fp"] = s_v_web_id
    h["is-retry"] = "0"
    req.auth_type = 4
    req.biz = "douyin_web"
    req.access = "web_sdk"
    req.ts_sign = bd_ticket.get("ts_sign", "") or ""
    client_cert = bd_ticket.get("client_cert", "") or ""
    req.sdk_cert = (
        base64.b64encode(client_cert.encode("utf-8")).decode("utf-8") if client_cert else ""
    )
    return req


def encode_send_message_request_pb2(
    *,
    conversation_id: str,
    text: str,
    bd_ticket: dict,
    conversation_short_id: int = 0,
    ticket: str = "",
    s_v_web_id: str = "",
    user_agent: str = _DEFAULT_UA,
    client_msg_id: Optional[str] = None,
) -> tuple[bytes, str, int]:
    """构造 send_message 的 HTTP protobuf body（含完整 bd-ticket-guard 鉴权）。

    Args:
        conversation_id: 平台会话 id，如 "0:1:80549827440:3061476426516824"。
        text: 纯文本消息内容。
        bd_ticket: {private_key, ticket, ts_sign, client_cert}（JsSignProvider.get_bd_ticket()）。
        conversation_short_id: 会话短 id（int64）；回复已存在会话暂未持久化时传 0。
        ticket: 会话票据（建会话场景才有）；回复老会话传空。
        s_v_web_id: cookie 里的设备指纹，用于 envelope.headers.fp。

    Returns:
        (body_bytes, client_msg_id, sequence_id)

    Raises:
        ValueError: 入参非法 / 缺 private_key。
        js_signer.JsSignerUnavailable: 签名引擎不可用（上层转 fallback）。
    """
    if not conversation_id:
        raise ValueError("conversation_id 不能为空")
    if not text:
        raise ValueError("text 不能为空")
    prik = bd_ticket.get("private_key") or ""
    if not prik:
        raise ValueError("缺少 private_key，无法签 reuqest_sign")

    cm_id = client_msg_id or str(uuid.uuid4())
    short_id = int(conversation_short_id or 0)

    req = _build_envelope(
        SEND_MESSAGE_CMD_ID, bd_ticket, s_v_web_id=s_v_web_id, user_agent=user_agent
    )

    msg_content = {
        "mention_users": [],
        "aweType": 700,
        "richTextInfos": [],
        "text": text,
    }
    content_json = json.dumps(msg_content, ensure_ascii=False, separators=(",", ":"))

    body = req.body.send_message_body
    body.conversation_id = conversation_id
    body.conversation_type = 1
    body.conversation_short_id = short_id
    body.content = content_json
    body.ext.append(R.ExtValue(key="s:client_message_id", value=cm_id))
    body.ext.append(R.ExtValue(key="s:stime", value=str(_now_ms())))
    body.ext.append(R.ExtValue(key="s:mentioned_users", value=""))
    body.message_type = 7
    body.ticket = ticket or ""
    body.client_message_id = cm_id

    # reuqest_sign：对 content + 会话标识做 EC 私钥签名（对照 DouYin_Spider build_send_message_request）
    sign_data = (
        f"content={content_json}"
        f"&conversation_id={conversation_id}"
        f"&conversation_short_id={short_id}"
    )
    req.reuqest_sign = js_signer.get_req_sign(
        {"sign_data": sign_data, "certType": "cookie", "scene": "web_protect"},
        prik,
    )

    return req.SerializeToString(), cm_id, req.sequence_id
