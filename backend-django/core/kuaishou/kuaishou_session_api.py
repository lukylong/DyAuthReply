#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""快手并发会话 API（含 worker 心跳上报、后台控制指令）"""
from typing import List

from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Query, Router
from ninja.errors import HttpError
from ninja.pagination import paginate

from common.fu_pagination import MyPagination
from common.fu_crud import retrieve
from core.kuaishou.kuaishou_account_model import KuaishouAccount
from core.kuaishou.kuaishou_session_model import KuaishouSession
from core.kuaishou.kuaishou_session_schema import (
    KuaishouSessionControlIn,
    KuaishouSessionFilters,
    KuaishouSessionHeartbeatIn,
    KuaishouSessionSchemaOut,
)

router = Router()

_VALID_ACTIONS = {'pause', 'resume', 'stop', 'restart'}


@router.get("/session", response=List[KuaishouSessionSchemaOut], summary="并发会话列表（分页）")
@paginate(MyPagination)
def list_session(request, filters: KuaishouSessionFilters = Query(...)):
    return retrieve(request, KuaishouSession, filters)


@router.get("/session/{session_id}", response=KuaishouSessionSchemaOut, summary="会话详情")
def get_session(request, session_id: str):
    return get_object_or_404(KuaishouSession, id=session_id)


@router.post("/session/heartbeat", response=KuaishouSessionSchemaOut, summary="worker 上报心跳 + 指标")
def heartbeat(request, data: KuaishouSessionHeartbeatIn):
    if not KuaishouAccount.objects.filter(id=data.account_id).exists():
        raise HttpError(400, "账号不存在")
    now = timezone.now()
    defaults = {
        'worker_id': data.worker_id,
        'context_id': data.context_id,
        'status': data.status,
        'messages_today': data.messages_today,
        'replies_today': data.replies_today,
        'errors_today': data.errors_today,
        'cpu_percent': data.cpu_percent,
        'memory_mb': data.memory_mb,
        'proxy_url': data.proxy_url,
        'error_message': data.error_message,
        'last_heartbeat': now,
    }
    session, created = KuaishouSession.objects.update_or_create(
        account_id=data.account_id,
        defaults=defaults,
    )
    if created or not session.started_at:
        session.started_at = now
        session.save(update_fields=['started_at'])
    KuaishouAccount.objects.filter(id=data.account_id).update(last_heartbeat=now)
    return session


@router.post("/session/{session_id}/control", summary="后台下发会话控制指令")
def control_session(request, session_id: str, data: KuaishouSessionControlIn):
    if data.action not in _VALID_ACTIONS:
        raise HttpError(400, f"不支持的指令: {data.action}；可选 {sorted(_VALID_ACTIONS)}")
    session = get_object_or_404(KuaishouSession, id=session_id)
    # 协议/worker 接入后，这里会向 Redis 频道发布控制指令（kuaishou:cmd:session:*）。
    # 骨架阶段先更新 DB 状态占位。
    status_map = {'pause': 'paused', 'resume': 'running', 'stop': 'stopped', 'restart': 'running'}
    session.status = status_map[data.action]
    session.save(update_fields=['status', 'sys_update_datetime'])
    return {"success": True, "action": data.action, "status": session.status}
