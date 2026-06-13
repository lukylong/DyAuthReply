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
    update_fields = ['status', 'sys_update_datetime']
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
