#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: runtime/message_store.py
@Desc: 入向消息 / 会话的数据结构与 DB 落库辅助 —— 中性模块，不依赖浏览器/Playwright。

入向消息的数据结构（ScannedMessage 等）与会话/消息落库 helper 定义在此，
被纯协议路径（http_protocol / dual_run / frontier_ws / worker）共用。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from asgiref.sync import sync_to_async
from django.utils import timezone


# -------------------- 异常 --------------------
class LoginGateDetected(RuntimeError):
    """IM 页面仍是登录门面，需要 worker 将账号打回重新登录。"""


class AccountIdentityMismatch(LoginGateDetected):
    """
    当前登录的 sec_uid 与 DB 中 DouyinAccount.sec_uid 不一致。

    继承 LoginGateDetected 是因为 worker 已有 except 路径会把账号打回（status=2）。
    """

    def __init__(self, expected: str, actual: str, *, account_id: str):
        self.expected = expected
        self.actual = actual
        self.account_id = account_id
        super().__init__(
            f"账号 {account_id} 身份核对失败：DB 期望 sec_uid={expected[:24]}…，"
            f"实际页面 sec_uid={actual[:24]}…，疑似换号未清干净，已强制下线"
        )


# -------------------- 数据结构 --------------------
@dataclass
class ScannedMessage:
    """本次扫描新增的入向消息（供 worker 后续处理）"""
    message_id: str           # DouyinMessage.id
    conversation_id: str      # DouyinConversation.id
    peer_sec_uid: str
    peer_nickname: Optional[str]
    text: str
    received_at: str          # ISO
    raw: dict = field(default_factory=dict)


# -------------------- DB 辅助 --------------------
def _hash_msg_id(peer_sec_uid: str, received_at_iso: str, content: str) -> str:
    h = hashlib.sha1()
    h.update((peer_sec_uid + '|' + received_at_iso + '|' + (content or '')[:60]).encode('utf-8'))
    return h.hexdigest()[:32]


@sync_to_async
def _silent_mark_read_in_db(conversation_id: str) -> int:
    """
    HTTP 协议路径专用的“静默已读”——只更新本地 DB 的 unread_count=0，
    不打抖音的 mark_read 接口（避免触发额外风控）。

    Returns:
        实际被影响的行数（0 = 没找到 / 本来就是 0）。
    """
    from core.douyin.douyin_conversation_model import DouyinConversation

    if not conversation_id:
        return 0
    return DouyinConversation.objects.filter(id=conversation_id).update(unread_count=0)


@sync_to_async
def _get_conversation_preview(account_id: str, peer_sec_uid: str) -> Optional[str]:
    from core.douyin.douyin_conversation_model import DouyinConversation

    conv = DouyinConversation.objects.filter(account_id=account_id, peer_sec_uid=peer_sec_uid).first()
    if conv is None:
        return None
    return conv.last_message_preview or ''


def _norm_for_compare(text: str) -> str:
    """归一化文本用于回声去重：去掉所有空白和不可见字符并截首段。"""
    if not text:
        return ''
    cleaned = re.sub(r'\s+', '', text)
    return cleaned[:120]


@sync_to_async
def _recent_outbound_texts(
    account_id: str,
    peer_sec_uid: str,
    *,
    window_seconds: int = 90,
) -> list[str]:
    """返回最近 `window_seconds` 内本账号在该会话发出的消息文本（归一化后）。"""
    from core.douyin.douyin_message_model import DouyinMessage

    cutoff = timezone.now() - timedelta(seconds=window_seconds)
    rows = DouyinMessage.objects.filter(
        conversation__account_id=account_id,
        conversation__peer_sec_uid=peer_sec_uid,
        direction='out',
        received_at__gte=cutoff,
    ).values_list('content', flat=True)[:30]
    return [_norm_for_compare(r) for r in rows if r]


@sync_to_async
def _recent_outbound_replies_log(
    account_id: str,
    conversation_id: str,
    *,
    window_seconds: int = 90,
) -> list[str]:
    """DouyinReplyLog 是另一处 outbound 真源（自动回复成功才落 reply_log）。"""
    from core.douyin.douyin_reply_log_model import DouyinReplyLog

    cutoff = timezone.now() - timedelta(seconds=window_seconds)
    qs = DouyinReplyLog.objects.filter(
        account_id=account_id,
        result='success',
        sent_at__gte=cutoff,
    )
    if conversation_id:
        qs = qs.filter(conversation_id=conversation_id)
    return [_norm_for_compare(r) for r in qs.values_list('reply_text', flat=True)[:30] if r]


@sync_to_async
def _upsert_conversation_and_message(
    account_id: str,
    peer_sec_uid: str,
    peer_nickname: Optional[str],
    text: str,
    received_at,
    raw: dict,
    *,
    external_msg_id: Optional[str] = None,
    peer_avatar: Optional[str] = None,
    platform_conversation_id: Optional[str] = None,
    direction: str = 'in',
) -> Optional[tuple]:
    """
    upsert DouyinConversation + DouyinMessage。
    返回 (conversation_id, message_id) 表示新增了一条入向消息；返回 None 表示重复跳过。

    修复说明：使用原子更新避免历史消息覆盖最新时间。
    - 新建会话：直接设置 last_message_at
    - 已存在会话：仅当 received_at >= last_message_at 时才更新时间字段
    """
    from core.douyin.douyin_account_model import DouyinAccount
    from core.douyin.douyin_conversation_model import DouyinConversation
    from core.douyin.douyin_message_model import DouyinMessage

    account = DouyinAccount.objects.filter(id=account_id).first()
    if account is None:
        return None

    resolved_peer_sec_uid = (peer_sec_uid or "").strip()
    account_sec = (account.sec_uid or "").strip()
    platform_conv_id = (platform_conversation_id or "").strip()

    # 校验 peer_sec_uid，防止将自己当成 peer
    if account_sec and resolved_peer_sec_uid == account_sec:
        if platform_conv_id:
            existing_conv = DouyinConversation.objects.filter(
                account=account,
                platform_conversation_id=platform_conv_id
            ).first()
            if existing_conv:
                resolved_peer_sec_uid = existing_conv.peer_sec_uid

    # 步骤1: get_or_create 会话（新建时设置所有字段）
    defaults = {
        'last_message_at': received_at,
        'last_message_preview': (text or '')[:200],
    }
    nick = (peer_nickname or '').strip()
    if nick:
        defaults['peer_nickname'] = nick
    avatar = (peer_avatar or '').strip()
    if avatar:
        defaults['peer_avatar'] = avatar
    if platform_conv_id:
        defaults['platform_conversation_id'] = platform_conv_id

    conv, created = DouyinConversation.objects.get_or_create(
        account=account,
        peer_sec_uid=resolved_peer_sec_uid,
        defaults=defaults,
    )

    # 步骤2: 如果会话已存在，使用原子更新（防止旧消息覆盖新时间）
    if not created:
        # 更新用户资料字段（无条件更新）
        update_fields = {}
        if nick:
            update_fields['peer_nickname'] = nick
        if avatar:
            update_fields['peer_avatar'] = avatar
        if platform_conv_id:
            update_fields['platform_conversation_id'] = platform_conv_id

        if update_fields:
            DouyinConversation.objects.filter(id=conv.id).update(**update_fields)

        # 原子更新时间字段：仅当新消息时间 >= 现有时间时才更新
        DouyinConversation.objects.filter(
            id=conv.id,
            last_message_at__lte=received_at,
        ).update(
            last_message_at=received_at,
            last_message_preview=(text or '')[:200],
        )

    # 步骤3: 创建消息记录
    ext_id = (
        external_msg_id
        if external_msg_id
        else _hash_msg_id(peer_sec_uid, received_at.isoformat(), text)
    )
    msg, msg_created = DouyinMessage.objects.get_or_create(
        conversation=conv,
        external_msg_id=ext_id,
        defaults={
            'direction': direction,
            'content_type': 'text',
            'content': text or '',
            'raw_payload': raw,
            'received_at': received_at,
            'processed': False,
        },
    )
    # 自愈：如果 direction 存储错误则更新
    if not msg_created and msg.direction != direction:
        msg.direction = direction
        msg.save(update_fields=['direction'])

    return (str(conv.id), str(msg.id)) if msg_created or (not msg_created and msg.direction != direction) else None
