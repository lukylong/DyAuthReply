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
from datetime import datetime, timedelta
from typing import Optional

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone


def _to_db_datetime(dt: datetime | None) -> datetime | None:
    """SQLite + USE_TZ=False 时须用 naive datetime，避免落库失败。

    USE_TZ=False 时全库约定为「naive 本地时间」（与 timezone.now() / auto_now 一致）。
    入向消息的 create_time_us 是 UTC aware，必须先换算到本地时区再去掉 tzinfo，
    否则会比本地慢 TIME_ZONE 偏移（如东八区 8 小时），导致：
      1) 前端按本地解析 ISO 时显示时间偏移；
      2) 入向(UTC) 与 出向/手动回复(timezone.now()=本地) 混排时排序错乱。
    """
    if dt is None:
        return None
    if settings.USE_TZ:
        if timezone.is_naive(dt):
            return timezone.make_aware(dt, timezone.get_current_timezone())
        return dt
    if timezone.is_aware(dt):
        return timezone.make_naive(dt, timezone.get_current_timezone())
    return dt


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


# -------------------- 互关系统提示 --------------------
# 抖音在双方互相关注后，会以**普通 text 消息**下发一条提示（如"我们已互相关注，可以开始聊天了"）。
#   - 入向（对方关注我）：sender=对方，按普通消息正常触发回复，无需特殊处理。
#   - 出向（我先关注、对方回关）：sender=我自己，正常会被"跳过己方出站消息"丢弃 → 漏回。
# 这里用子串匹配（兼容措辞/标点变化）识别该提示，出向场景交 build_mutual_follow_trigger 兜底放出。
MUTUAL_FOLLOW_NOTICE_KEYWORD = "互相关注"


def is_mutual_follow_notice(text: Optional[str]) -> bool:
    """识别互关系统提示文案。"""
    return bool(text) and MUTUAL_FOLLOW_NOTICE_KEYWORD in text


@sync_to_async
def build_mutual_follow_trigger(
    account_id: str,
    platform_conversation_id: str,
    server_message_id,
    text: str,
) -> Optional["ScannedMessage"]:
    """为"出向互关系统提示"构造一条兜底触发消息（不另外落库）。

    - message_id 用合成稳定值 `mf_<server_message_id>`：回复日志按 trigger_message_id 去重，
      同一互关事件跨重启不会重复回复。
    - 需要 DB 已有该平台会话映射：发送协议要求本地会话 ID，黑名单/昵称也依赖对方 sec_uid，
      而出向消息只带"我自己"的 sec_uid。查不到本地会话则返回 None（调用方记 warning 跳过），
      避免在缺对方 sec_uid 时伪造会话、产生重复脏会话。
    - raw.mutual_follow_outbound=True 作为 worker 的"互关兜底"分支开关；
      raw.conversation_id 为平台会话 ID，供 worker 直发。
    """
    plat = (platform_conversation_id or "").strip()
    if not plat:
        return None
    from core.douyin.douyin_conversation_model import DouyinConversation

    conv = DouyinConversation.objects.filter(
        account_id=account_id,
        platform_conversation_id=plat,
    ).first()
    if conv is None:
        return None
    return ScannedMessage(
        message_id=f"mf_{server_message_id}",
        conversation_id=str(conv.id),
        peer_sec_uid=conv.peer_sec_uid or "",
        peer_nickname=conv.peer_nickname or None,
        text=text or "",
        received_at=timezone.now().isoformat(),
        raw={
            "mutual_follow_outbound": True,
            "conversation_id": plat,
            "server_message_id": server_message_id,
        },
    )


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
    peer_unique_id: Optional[str] = None,
    platform_conversation_id: Optional[str] = None,
    direction: str = 'in',
    mark_processed: bool = False,
    content_type: str = 'text',
    media: Optional[dict] = None,
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

    received_at = _to_db_datetime(received_at)
    if received_at is None:
        received_at = _to_db_datetime(timezone.now())

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
    unique_id = (peer_unique_id or '').strip()
    if unique_id:
        defaults['peer_unique_id'] = unique_id
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
        if unique_id:
            update_fields['peer_unique_id'] = unique_id
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
    # 富媒体结构化字段并入 raw_payload，前端按 raw_payload.media 渲染
    raw_payload = raw
    if media:
        raw_payload = {**(raw or {}), 'media': media}
    msg, msg_created = DouyinMessage.objects.get_or_create(
        conversation=conv,
        external_msg_id=ext_id,
        defaults={
            'direction': direction,
            'content_type': content_type or 'text',
            'content': text or '',
            'raw_payload': raw_payload,
            'received_at': received_at,
            'processed': mark_processed or direction != 'in',
        },
    )

    # 自愈：如果 direction 存储错误则更新
    if not msg_created and msg.direction != direction:
        msg.direction = direction
        msg.save(update_fields=['direction'])

    return (str(conv.id), str(msg.id)) if msg_created or (not msg_created and msg.direction != direction) else None


@sync_to_async
def fetch_pending_inbound_messages(account_id: str, *, limit: int = 30) -> list[ScannedMessage]:
    """拉取已入库但未处理（processed=False）的入向消息，供 worker 补跑自动回复。"""
    from core.douyin.douyin_message_model import DouyinMessage

    rows = (
        DouyinMessage.objects.filter(
            conversation__account_id=account_id,
            direction='in',
            processed=False,
        )
        .exclude(reply_logs__result='success')
        .select_related('conversation')
        .order_by('received_at')[:limit]
    )
    out: list[ScannedMessage] = []
    for msg in rows:
        conv = msg.conversation
        received_at = msg.received_at.isoformat() if msg.received_at else ''
        out.append(
            ScannedMessage(
                message_id=str(msg.id),
                conversation_id=str(conv.id),
                peer_sec_uid=str(conv.peer_sec_uid or ''),
                peer_nickname=conv.peer_nickname,
                text=msg.content or '',
                received_at=received_at,
                raw=msg.raw_payload if isinstance(msg.raw_payload, dict) else {},
            )
        )
    return out
