#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: ws_notify.py
@Desc: WebSocket Notification Helper - 向 DouyinConsumer 推送事件

worker 进程（非 Django 请求上下文）通过 channel_layer 向用户所在组发消息，
前端 DouyinConsumer 订阅 `douyin_user_{user_id}` 分组后即可实时接收。

事件类型约定（data.event）：
  qr_image          二维码 base64（供扫码登录弹窗显示）
  login_success     登录成功
  login_failed      登录失败 / 二维码过期
  new_message       收到入向消息
  reply_sent        已回复
  reply_failed      回复失败
  event             通用运行时事件（对应 DouyinEvent）
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def _group_name(user_id: str | int) -> str:
    return f"douyin_user_{user_id}"


async def push_to_user(user_id: str | int, event: str, payload: Optional[dict[str, Any]] = None) -> None:
    """
    向指定平台用户推送一条抖音事件。worker 进程可直接 await 本函数。

    前端 DouyinConsumer 会在 `douyin_event` 处理器里把它转发为 WebSocket 消息。
    """
    layer = get_channel_layer()
    if layer is None:
        logger.warning("channel_layer 为空，跳过推送（通常是 Channels 未启用）")
        return
    try:
        await layer.group_send(
            _group_name(user_id),
            {
                "type": "douyin.event",   # 对应 DouyinConsumer.douyin_event
                "event": event,
                "payload": payload or {},
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"[ws_notify] 推送失败 user={user_id} event={event} err={e}")


async def push_event_log(user_id: str | int, level: str, title: str, detail: str = '', payload: Optional[dict] = None) -> None:
    """快捷封装：推送一条"事件日志"类型消息"""
    await push_to_user(user_id, "event", {
        "level": level,
        "title": title,
        "detail": detail,
        **(payload or {}),
    })
