#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: message_api.py
@Desc: 消息 API
"""
"""
消息 API
"""
from typing import List

from ninja import Router, Query
from ninja.errors import HttpError
from ninja.pagination import paginate

from common.fu_pagination import MyPagination
from .message_schema import (
    MessageOut,
    MessageListOut,
    UnreadCountOut,
    MarkReadInput,
)
from .message_service import MessageService

router = Router(tags=['消息中心'])


@router.get('/list', response=List[MessageListOut], summary='消息列表')
@paginate(MyPagination)
def list_messages(
        request,
        msg_type: str = Query(None, description='消息类型'),
        status: str = Query(None, description='状态: unread/read'),
):
    """获取当前用户的消息列表"""
    user = request.auth

    items = MessageService.get_list(
        user_id=str(user.id),
        msg_type=msg_type,
        status=status,
    )

    return items


@router.get('/unread-count', response=UnreadCountOut, summary='未读数量')
def get_unread_count(request):
    """获取未读消息数量"""
    user = request.auth
    user_id = str(user.id)

    total = MessageService.get_unread_count(user_id)
    by_type = MessageService.get_unread_count_by_type(user_id)

    return {
        'total': total,
        'by_type': by_type,
    }


@router.post('/read-all', summary='全部已读')
def mark_all_as_read(request, data: MarkReadInput = None):
    """标记所有消息为已读"""
    user = request.auth

    msg_type = data.msg_type if data else None
    count = MessageService.mark_all_as_read(str(user.id), msg_type)

    return {'message': f'已标记 {count} 条消息为已读'}


@router.delete('/clear-read', summary='清空已读')
def clear_read_messages(request):
    """清空所有已读消息"""
    user = request.auth

    count = MessageService.delete_all_read(str(user.id))

    return {'message': f'已删除 {count} 条已读消息'}


# ============ 动态路由放在最后 ============

@router.get('/{message_id}', response=MessageOut, summary='消息详情')
def get_message(request, message_id: str):
    """获取消息详情"""
    user = request.auth

    message = MessageService.get_by_id(message_id, str(user.id))
    if not message:
        raise HttpError(404, "消息不存在")

    return message


@router.post('/{message_id}/read', summary='标记已读')
def mark_as_read(request, message_id: str):
    """标记单条消息为已读"""
    user = request.auth

    success = MessageService.mark_as_read(message_id, str(user.id))
    if not success:
        raise HttpError(404, "消息不存在")

    return {'message': '已标记为已读'}


@router.delete('/{message_id}', summary='删除消息')
def delete_message(request, message_id: str):
    """删除单条消息"""
    user = request.auth

    success = MessageService.delete(message_id, str(user.id))
    if not success:
        raise HttpError(404, "消息不存在")

    return {'message': '删除成功'}


# ============ 辅助函数 ============

def _build_message_out(message) -> dict:
    """构建消息详情输出"""
    return {
        'id': str(message.id),
        'title': message.title,
        'content': message.content,
        'msg_type': message.msg_type,
        'status': message.status,
        'link_type': message.link_type or '',
        'link_id': message.link_id or '',
        'sender_name': message.sender.name if message.sender else '',
        'read_at': message.read_at,
        'created_at': message.sys_create_datetime,
    }
