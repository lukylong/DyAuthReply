#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""桌面客户端管理员认证（本地 PIN + 短期 Token）。"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

from ninja.errors import HttpError

DEFAULT_ADMIN_PASSWORD = 'admin123'
TOKEN_TTL_SECONDS = 4 * 3600

_lock = threading.Lock()
_tokens: dict[str, float] = {}


def _admin_config_path() -> Path:
    from env import CLIENT_DATA_DIR

    return Path(CLIENT_DATA_DIR) / '.admin.json'


def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256(f'{salt}:{password}'.encode('utf-8')).hexdigest()


def _load_or_init_config() -> dict:
    path = _admin_config_path()
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            pass
    salt = secrets.token_hex(16)
    cfg = {
        'salt': salt,
        'password_hash': _hash_password(DEFAULT_ADMIN_PASSWORD, salt),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
    return cfg


def verify_admin_password(password: str) -> bool:
    cfg = _load_or_init_config()
    salt = str(cfg.get('salt') or '')
    expected = str(cfg.get('password_hash') or '')
    if not salt or not expected:
        return False
    return secrets.compare_digest(_hash_password(password or '', salt), expected)


def issue_admin_token() -> dict:
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + TOKEN_TTL_SECONDS
    with _lock:
        _purge_expired_tokens()
        _tokens[token] = expires_at
    return {
        'token': token,
        'expires_in': TOKEN_TTL_SECONDS,
        'expires_at': int(expires_at),
    }


def issue_local_admin_token(request) -> dict:
    from common.local_desktop_auth import _is_loopback

    if not _is_loopback(request):
        raise HttpError(403, '客户端 API 仅允许本机访问')
    return issue_admin_token()


def _purge_expired_tokens() -> None:
    now = time.time()
    expired = [k for k, exp in _tokens.items() if exp <= now]
    for key in expired:
        _tokens.pop(key, None)


def verify_admin_token(token: Optional[str]) -> bool:
    if not token:
        return False
    with _lock:
        _purge_expired_tokens()
        exp = _tokens.get(token)
        if exp is None or exp <= time.time():
            _tokens.pop(token, None)
            return False
        return True


def revoke_admin_token(token: Optional[str]) -> None:
    if not token:
        return
    with _lock:
        _tokens.pop(token, None)


def require_admin(request) -> None:
    token = request.headers.get('X-Admin-Token') or request.META.get('HTTP_X_ADMIN_TOKEN')
    if not verify_admin_token(token):
        raise HttpError(401, '需要管理员认证，请重新登录')
