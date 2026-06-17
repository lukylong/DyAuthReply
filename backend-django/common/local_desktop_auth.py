#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""桌面客户端本地鉴权（M2 前临时方案）：仅 127.0.0.1 + 内置 local 用户，无 JWT/RBAC。"""
from __future__ import annotations

import logging

from ninja.errors import HttpError

logger = logging.getLogger(__name__)


def _client_local_ip(request) -> str:
    meta = request.META
    forwarded = (meta.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip()
    if forwarded:
        return forwarded
    return meta.get('REMOTE_ADDR') or ''


def _is_loopback(request) -> bool:
    ip = _client_local_ip(request)
    return ip in ('127.0.0.1', '::1', 'localhost', '')


class LocalDesktopAuth:
    """客户端模式：本机请求无需 Token，直接使用内置 local 用户。"""

    def __call__(self, request):
        if not _is_loopback(request):
            raise HttpError(403, '客户端 API 仅允许本机访问')

        from core.client.bootstrap import get_or_create_local_user

        user = get_or_create_local_user()
        if not user.is_active:
            raise HttpError(403, '本地用户已禁用')
        return user
