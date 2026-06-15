#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: runtime/account_status.py
@Desc: 账号登录态的 DB 操作 —— 中性模块，不依赖浏览器/Playwright。

历史上 mark_account_login_invalid 定义在 login.py（浏览器扫码登录模块）里，worker
需要它把失效账号打回。脱浏览器后 worker 不应再 import login.py（浏览器栈），故把这个
纯 DB 函数下沉到本模块。
"""
from __future__ import annotations

from typing import Optional

from asgiref.sync import sync_to_async
from django.utils import timezone


@sync_to_async
def _mark_login_failed(
    account_id: str,
    reason: str,
    *,
    keep_pending_verification: bool = False,
) -> Optional[str]:
    from core.douyin.douyin_account_model import DouyinAccount
    from core.douyin.douyin_event_model import DouyinEvent

    acc = DouyinAccount.objects.filter(id=account_id).first()
    if acc is None:
        return None
    acc.status = 2
    acc.credential_state = 'invalid'
    update_fields = ['status', 'credential_state', 'sys_update_datetime']
    if not keep_pending_verification:
        acc.pending_verification_type = None
        acc.pending_verification_at = None
        acc.pending_verification_until = None
        update_fields.extend([
            'pending_verification_type',
            'pending_verification_at',
            'pending_verification_until',
        ])
    acc.save(update_fields=update_fields)

    # 派发掉线告警站内信给管理员和所有者
    recipients = set()
    if acc.owner:
        recipients.add(acc.owner)
    try:
        from core.user.user_model import User
        superusers = User.objects.filter(is_superuser=True)
        for u in superusers:
            recipients.add(u)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[account_status] 查找超级管理员失败: {e}")

    if recipients:
        try:
            title = f"抖音账号【{acc.nickname}】监测到掉线，请及时排查"
            from django.utils.timezone import is_aware
            now_dt = timezone.now()
            if is_aware(now_dt):
                now_dt = timezone.localtime(now_dt)
            local_time_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
            content = (
                f"系统于 {local_time_str} 监测到托管抖音账号【{acc.nickname}】(ID: {acc.id}) 登录状态已失效。\n"
                f"失效原因：{reason}\n"
                f"目前系统已自动更新该账号状态为「登录失效」、凭证状态为「已失效」。"
                f"请尽快前往管理后台更新或重新导入凭证（Cookie/keys），避免自动回复业务中断。"
            )
            from core.message.message_service import NotifyService
            recipient_ids = [str(r.id) for r in recipients]
            NotifyService.send(
                recipient_ids=recipient_ids,
                title=title,
                content=content,
                channels=['site'],
                msg_type='system',
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[account_status] 发送掉线站内信失败: {e}")

    DouyinEvent.objects.create(
        account=acc,
        event_type='login_expired',
        level='warning',
        title=f'账号 {acc.nickname} 登录失败',
        detail=reason,
        occurred_at=timezone.now(),
    )
    return str(acc.owner_id)


async def mark_account_login_invalid(
    account_id: str,
    reason: str,
    *,
    keep_pending_verification: bool = False,
) -> Optional[str]:
    """供 worker 统一将账号打回重新登录（纯 DB 操作）。"""
    return await _mark_login_failed(
        account_id,
        reason,
        keep_pending_verification=keep_pending_verification,
    )
