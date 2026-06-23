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
from core.douyin.douyin_conversation_model import DouyinConversation
from core.douyin.douyin_message_model import DouyinMessage
from core.douyin.douyin_session_model import DouyinSession
from core.douyin.runtime import command_publisher
from core.douyin.douyin_session_schema import (
    DouyinAutoReplyTestIn,
    DouyinConversationListOut,
    DouyinManualReplyIn,
    DouyinMessageItemOut,
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


@router.get(
    "/session/{session_id}/conversations",
    response=DouyinConversationListOut,
    summary="获取该账号最近会话列表",
)
def list_session_conversations(
    request,
    session_id: str,
    page: int = 1,
    page_size: int = 50,
    keyword: str = '',
):
    from core.douyin.douyin_conversation_utils import paginate_account_conversations

    session = get_object_or_404(DouyinSession, id=session_id)
    items, total, has_more = paginate_account_conversations(
        session.account_id,
        page=page,
        page_size=page_size,
        keyword=keyword,
    )
    page = max(1, int(page or 1))
    page_size = min(max(1, int(page_size or 50)), 100)
    return {
        'items': items,
        'total': total,
        'page': page,
        'page_size': page_size,
        'has_more': has_more,
    }


@router.get(
    "/session/{session_id}/conversation/{conversation_id}/messages",
    response=List[DouyinMessageItemOut],
    summary="获取某会话最近消息",
)
def list_session_messages(request, session_id: str, conversation_id: str):
    session = get_object_or_404(DouyinSession, id=session_id)
    conv = get_object_or_404(DouyinConversation, id=conversation_id, account_id=session.account_id)
    messages = list(
        DouyinMessage.objects
        .filter(conversation_id=conv.id)
        .order_by('-received_at', '-sys_create_datetime')[:100]
    )

    # 增强消息数据：添加发送者信息
    result = []
    for msg in messages:
        msg_dict = {
            'id': str(msg.id),
            'direction': msg.direction,
            'content_type': msg.content_type,
            'content': msg.content,
            'received_at': msg.received_at,
            'processed': msg.processed,
        }

        # 根据消息方向填充发送者信息
        if msg.direction == 'in':
            # 对方发来的消息
            msg_dict['sender_name'] = conv.peer_nickname or conv.peer_sec_uid
            msg_dict['sender_avatar'] = conv.peer_avatar
        else:
            # 我方发出的消息
            try:
                msg_dict['sender_name'] = session.account.nickname if session.account else '我'
                msg_dict['sender_avatar'] = session.account.avatar if session.account else None
            except Exception:
                msg_dict['sender_name'] = '我'
                msg_dict['sender_avatar'] = None

        result.append(msg_dict)

    return result


@router.post(
    "/session/{session_id}/manual-reply",
    response=DouyinSessionControlOut,
    summary="手动发送一条测试回复",
)
def manual_reply(request, session_id: str, data: DouyinManualReplyIn):
    session = get_object_or_404(DouyinSession, id=session_id)
    conv = get_object_or_404(DouyinConversation, id=data.conversation_id, account_id=session.account_id)
    text = (data.text or '').strip()
    if not text:
        return DouyinSessionControlOut(success=False, message="回复内容不能为空")
    ok, command_id = command_publisher.send_manual_reply(str(session.account_id), str(conv.id), text)
    if not ok:
        return DouyinSessionControlOut(success=False, message="Redis 不可用，未能下发手动回复指令")
    return DouyinSessionControlOut(
        success=True,
        message="手动回复指令已下发",
        command_id=command_id,
    )


@router.post(
    "/session/{session_id}/auto-reply-test",
    response=DouyinSessionControlOut,
    summary="手动触发一条自动回复测试",
)
def auto_reply_test(request, session_id: str, data: DouyinAutoReplyTestIn):
    session = get_object_or_404(DouyinSession, id=session_id)
    conv = get_object_or_404(DouyinConversation, id=data.conversation_id, account_id=session.account_id)
    text = (data.text or '').strip()
    if not text:
        return DouyinSessionControlOut(success=False, message="模拟消息不能为空")
    ok = command_publisher.send_manual_auto_reply_test(str(session.account_id), str(conv.id), text)
    if not ok:
        return DouyinSessionControlOut(success=False, message="Redis 不可用，未能下发自动回复测试指令")
    return DouyinSessionControlOut(success=True, message=f"已下发自动回复测试指令（会话：{conv.peer_nickname or conv.peer_sec_uid}）")


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
