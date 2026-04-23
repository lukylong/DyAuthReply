#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音并发会话 API

- 后台：列出 worker 的实时会话状态、暂停/恢复/停止某会话
- worker → 后端：心跳上报（POST /session/heartbeat）
"""
from typing import List

from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Query, Router
from ninja.pagination import paginate

from common.fu_crud import retrieve
from common.fu_pagination import MyPagination
from core.douyin.douyin_account_model import DouyinAccount
from core.douyin.douyin_session_model import DouyinSession
from core.douyin.runtime import command_publisher
from core.douyin.douyin_session_schema import (
    DouyinSessionBatchIdsIn,
    DouyinSessionControlIn,
    DouyinSessionControlOut,
    DouyinSessionFilters,
    DouyinSessionHeartbeatIn,
    DouyinSessionSchemaOut,
)

router = Router()


@router.get("/session", response=List[DouyinSessionSchemaOut], summary="会话列表（分页）")
@paginate(MyPagination)
def list_session(request, filters: DouyinSessionFilters = Query(...)):
    return retrieve(request, DouyinSession, filters)


@router.get("/session/live", response=List[DouyinSessionSchemaOut], summary="实时在线会话（60s 内有心跳）")
def list_live_session(request):
    """前端轮询或 WebSocket 订阅的简化接口"""
    from datetime import timedelta
    threshold = timezone.now() - timedelta(seconds=60)
    return (
        DouyinSession.objects
        .filter(last_heartbeat__gte=threshold)
        .order_by('-last_heartbeat')
    )


@router.get("/session/{session_id}", response=DouyinSessionSchemaOut, summary="会话详情")
def get_session(request, session_id: str):
    return get_object_or_404(DouyinSession, id=session_id)


@router.post("/session/heartbeat", response=DouyinSessionControlOut, summary="worker 心跳上报")
def heartbeat(request, data: DouyinSessionHeartbeatIn):
    """
    worker 每 10-30 秒调用一次，upsert 会话快照。
    使用 OneToOne(account) 保证一账号一会话记录。
    """
    account = get_object_or_404(DouyinAccount, id=data.account_id)
    payload = data.dict()
    payload.pop('account_id', None)
    obj, created = DouyinSession.objects.update_or_create(
        account=account,
        defaults={
            **payload,
            'last_heartbeat': timezone.now(),
            'started_at': timezone.now() if data.status == 'running' and payload.get('status') else None,
        },
    )
    # 同步更新账号心跳
    DouyinAccount.objects.filter(id=account.id).update(
        last_heartbeat=timezone.now(),
        status=1 if data.status in ('idle', 'running') else 2 if data.status == 'error' else account.status,
    )
    return DouyinSessionControlOut(
        success=True,
        message=f"heartbeat ok ({'created' if created else 'updated'})",
    )


@router.post("/session/{session_id}/control", response=DouyinSessionControlOut, summary="控制会话（暂停/恢复/停止/重启）")
def control_session(request, session_id: str, data: DouyinSessionControlIn):
    """
    控制指令只写入 Redis pubsub（M2 之后由 worker 消费）。
    M1 阶段直接更新 status 字段作为占位。
    """
    session = get_object_or_404(DouyinSession, id=session_id)
    status_map = {
        'pause': 'paused',
        'resume': 'running',
        'stop': 'stopped',
        'restart': 'running',
    }
    new_status = status_map.get(data.action)
    if not new_status:
        return DouyinSessionControlOut(success=False, message=f"未知指令 {data.action}")
    session.status = new_status
    session.save(update_fields=['status', 'sys_update_datetime'])
    command_publisher.send_session_control(str(session.account_id), data.action)
    return DouyinSessionControlOut(success=True, message=f"指令 {data.action} 已下发")


@router.post("/session/batch/stop", response=DouyinSessionControlOut, summary="批量停止会话")
def batch_stop(request, data: DouyinSessionBatchIdsIn):
    count = DouyinSession.objects.filter(id__in=data.ids).update(status='stopped')
    return DouyinSessionControlOut(success=True, message=f"已停止 {count} 个会话", )
