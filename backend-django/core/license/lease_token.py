#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Signed short-lived lease tokens for client authorization."""
from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta
from typing import Any

import jwt
from django.utils import timezone

LEASE_ISSUER = "dyauthreply-license"
LEASE_ALGORITHM = "EdDSA"


def _read_key(name: str, b64_name: str) -> str:
    raw = (os.environ.get(name) or "").strip()
    if raw:
        return raw.replace("\\n", "\n")
    encoded = (os.environ.get(b64_name) or "").strip()
    if not encoded:
        return ""
    return base64.b64decode(encoded).decode("utf-8").strip()


def get_lease_private_key() -> str:
    return _read_key("LICENSE_LEASE_PRIVATE_KEY", "LICENSE_LEASE_PRIVATE_KEY_B64")


def get_lease_public_key() -> str:
    return _read_key("LICENSE_LEASE_PUBLIC_KEY", "LICENSE_LEASE_PUBLIC_KEY_B64")


def lease_signing_enabled() -> bool:
    return bool(get_lease_private_key())


def lease_verification_enabled() -> bool:
    return bool(get_lease_public_key())


def lease_ttl_minutes(default: int = 30) -> int:
    try:
        value = int(os.environ.get("LICENSE_LEASE_TTL_MINUTES") or default)
    except (TypeError, ValueError):
        value = default
    return max(5, value)


def lease_renew_skew_seconds(default: int = 120) -> int:
    try:
        value = int(os.environ.get("LICENSE_LEASE_RENEW_SKEW_SECONDS") or default)
    except (TypeError, ValueError):
        value = default
    return max(15, value)


def build_lease_payload(
    *,
    activation_id: str,
    license_key_id: str,
    device_fingerprint: str,
    plan: dict[str, Any],
    feature_flags: dict[str, Any],
    lease_sequence: int,
    now: datetime,
    lease_expires_at: datetime,
    grace_until: datetime | None,
) -> dict[str, Any]:
    return {
        "iss": LEASE_ISSUER,
        "sub": activation_id,
        "activation_id": activation_id,
        "license_key_id": license_key_id,
        "device_fingerprint": device_fingerprint,
        "plan_code": plan.get("code") or "",
        "feature_flags": feature_flags or {},
        "lease_sequence": int(lease_sequence or 0),
        "iat": int(now.timestamp()),
        "exp": int(lease_expires_at.timestamp()),
        "grace_until": int(grace_until.timestamp()) if grace_until else None,
    }


def issue_signed_lease(payload: dict[str, Any]) -> str:
    private_key = get_lease_private_key()
    if not private_key:
        return ""
    return jwt.encode(payload, private_key, algorithm=LEASE_ALGORITHM)


def decode_signed_lease(token: str, *, verify_exp: bool = False) -> dict[str, Any]:
    public_key = get_lease_public_key()
    if not public_key:
        raise ValueError("lease public key not configured")
    return jwt.decode(
        token,
        public_key,
        algorithms=[LEASE_ALGORITHM],
        issuer=LEASE_ISSUER,
        options={
            "require": ["iss", "sub", "exp", "iat"],
            "verify_exp": verify_exp,
            "verify_aud": False,
        },
    )


def default_lease_expiry(now: datetime | None = None) -> datetime:
    base = now or timezone.now()
    return base + timedelta(minutes=lease_ttl_minutes())
