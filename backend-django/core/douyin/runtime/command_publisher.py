#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: command_publisher.py
@Desc: Command Publisher - 同步接口往 Redis 发布 worker 命令

Django HTTP 视图是同步的，所以这里用 redis-py 的同步客户端
（避免在请求线程里起 asyncio loop）。

频道命名：
    douyin:cmd:login:<account_id>
    douyin:cmd:logout:<account_id>
    douyin:cmd:session:<account_id>:<action>
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)

_redis = None


def _client():
    """延迟初始化 Redis 客户端"""
    global _redis
    if _redis is not None:
        return _redis
    try:
        import redis
        url = getattr(settings, 'REDIS_URL', None)
        if not url:
            logger.warning("REDIS_URL 未配置，抖音 worker 命令发布将回退为 DB-only 模式")
            return None
        _redis = redis.from_url(url, decode_responses=False, socket_connect_timeout=2)
        _redis.ping()
        return _redis
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Redis 连接失败: {e}；命令发布降级为 DB-only")
        _redis = None
        return None


def publish(channel: str, payload: Optional[dict] = None) -> bool:
    """发布一条命令。返回 True 表示成功；False 表示 Redis 不可用。"""
    ok, _ = publish_with_id(channel, payload)
    return ok


def publish_with_id(channel: str, payload: Optional[dict] = None) -> tuple[bool, Optional[str]]:
    """发布命令；DB 模式下返回 (True, command_id)，Redis 模式返回 (True, None)。"""
    backend = getattr(settings, 'DOUYIN_COMMAND_BACKEND', 'redis')
    if backend == 'db':
        cmd_id = _publish_db(channel, payload)
        return (cmd_id is not None, cmd_id)

    client = _client()
    if client is None:
        return False, None
    try:
        data = json.dumps(payload or {}, ensure_ascii=False).encode('utf-8')
        client.publish(channel, data)
        return True, None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"publish 失败 channel={channel} err={e}")
        return False, None


def _publish_db(channel: str, payload: Optional[dict] = None) -> Optional[str]:
    try:
        from core.douyin.douyin_worker_command_model import DouyinWorkerCommand

        cmd = DouyinWorkerCommand.objects.create(channel=channel, payload=payload or {})
        return str(cmd.id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"db publish 失败 channel={channel} err={e}")
        return None


def send_manual_reply(account_id: str, conversation_id: str, text: str) -> tuple[bool, Optional[str]]:
    return publish_with_id(
        f"douyin:cmd:manual_reply:{account_id}",
        {
            "action": "manual_reply",
            "conversation_id": conversation_id,
            "text": text,
        },
    )


def send_manual_auto_reply_test(account_id: str, conversation_id: str, text: str) -> bool:
    return publish(
        f"douyin:cmd:manual_auto_reply:{account_id}",
        {
            "action": "manual_auto_reply",
            "conversation_id": conversation_id,
            "text": text,
        },
    )


def send_logout(account_id: str) -> bool:
    return publish(f"douyin:cmd:logout:{account_id}", {"action": "logout"})


def send_session_control(account_id: str, action: str) -> bool:
    assert action in ('pause', 'resume', 'stop', 'restart'), f"invalid action: {action}"
    return publish(f"douyin:cmd:session:{account_id}:{action}", {"action": action})
