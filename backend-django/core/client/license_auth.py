#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Desktop client local license lease validation and hosted renew proxy."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import random
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from django.conf import settings
from django.utils import timezone
from ninja.errors import HttpError

from core.license.lease_token import decode_signed_lease, lease_renew_skew_seconds, lease_verification_enabled

logger = logging.getLogger(__name__)

STATE_UNACTIVATED = "unactivated"
STATE_ACTIVE = "active"
STATE_GRACE = "grace"
STATE_EXPIRED = "expired"
STATE_REVOKED = "revoked"
STATE_INVALID = "invalid"

STATE_LABELS = {
    STATE_UNACTIVATED: "未激活",
    STATE_ACTIVE: "已激活",
    STATE_GRACE: "宽限期",
    STATE_EXPIRED: "已过期",
    STATE_REVOKED: "已撤销",
    STATE_INVALID: "无效",
}

BUSINESS_ALLOWED_STATES = {STATE_ACTIVE, STATE_GRACE}
LICENSE_STATE_VERSION = 2

_lease_renewer_lock = threading.Lock()
_lease_renewer_started = False


def _state_file_path() -> Path:
    from env import CLIENT_DATA_DIR

    return Path(CLIENT_DATA_DIR) / ".license-state.json"


def _client_app_version() -> str:
    """客户端版本号：优先环境变量，其次打包 defaults / tauri.conf（与 UI 左下角一致）。"""
    for key in ('CLIENT_APP_VERSION', 'APP_VERSION'):
        value = (os.environ.get(key) or '').strip()
        if value:
            return value

    import sys
    from pathlib import Path

    search_roots: list[Path] = []
    if getattr(sys, 'frozen', False) and getattr(sys, '_MEIPASS', None):
        search_roots.append(Path(sys._MEIPASS))
    # backend-django/core/client/license_auth.py -> repo root
    search_roots.append(Path(__file__).resolve().parents[3])

    defaults_rel = (
        'launcher/generated_defaults.json',
        'dyauthreply-client/launcher/generated_defaults.json',
    )
    tauri_rel = (
        'dyauthreply-client/desktop/src-tauri/tauri.conf.json',
        'desktop/src-tauri/tauri.conf.json',
    )
    for root in search_roots:
        for rel in defaults_rel:
            path = root / rel
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                version = str(data.get('client_app_version') or '').strip()
                if version:
                    return version
            except Exception:
                pass
        for rel in tauri_rel:
            path = root / rel
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                version = str(data.get('version') or '').strip()
                if version:
                    return version
            except Exception:
                pass
    return 'unknown'


def _now() -> datetime:
    return timezone.now()


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        if getattr(settings, "USE_TZ", False):
            return value if timezone.is_aware(value) else timezone.make_aware(value)
        return timezone.localtime(value).replace(tzinfo=None) if timezone.is_aware(value) else value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if getattr(settings, "USE_TZ", False):
        return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)
    if timezone.is_aware(parsed):
        return timezone.localtime(parsed).replace(tzinfo=None)
    return parsed


def _to_iso(value: Any) -> str | None:
    dt = _parse_datetime(value)
    return dt.isoformat() if dt else None


def _safe_json_load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[client.license] failed to read state file path=%s err=%s", path, exc)
        return {}


def _safe_json_dump(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _normalize_server_url(server_url: str) -> str:
    value = (server_url or "").strip()
    if not value:
        try:
            from env import CLIENT_LICENSE_SERVER_URL

            value = (CLIENT_LICENSE_SERVER_URL or "").strip()
        except Exception:  # noqa: BLE001
            value = ""
    if not value:
        raise HttpError(400, "授权服务地址不能为空")
    return value.rstrip("/")


def _build_client_auth_endpoint(server_url: str, path: str) -> str:
    base = _normalize_server_url(server_url)
    suffix = path if path.startswith("/") else f"/{path}"
    if base.endswith("/api/client-auth"):
        return f"{base}{suffix}"
    return urljoin(f"{base}/", f"api/client-auth{suffix}")


def get_device_profile() -> dict[str, Any]:
    hostname = socket.gethostname()
    system = platform.system().lower() or "unknown"
    release = platform.release() or ""
    machine = platform.machine() or ""
    mac = f"{uuid.getnode():012x}"
    raw = "|".join([hostname, system, release, machine, mac])
    fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return {
        "device_fingerprint": fingerprint,
        "device_name": hostname,
        "os_type": system,
        "os_version": release,
        "app_version": _client_app_version(),
        "machine_meta": {
            "hostname": hostname,
            "machine": machine,
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
    }


def _default_state() -> dict[str, Any]:
    profile = get_device_profile()
    return {
        "version": LICENSE_STATE_VERSION,
        "server_url": "",
        "local_state": STATE_UNACTIVATED,
        "activation_status": "",
        "activation_id": "",
        "activation_token": "",
        "refresh_token": "",
        "lease_token": "",
        "lease_sequence": 0,
        "lease_expires_at": None,
        "license_key_id": "",
        "masked_code": "",
        "activated_at": None,
        "last_check_in_at": None,
        "next_check_in_at": None,
        "last_valid_until": None,
        "expires_at": None,
        "heartbeat_interval_minutes": 0,
        "grace_period_minutes": 0,
        "plan": None,
        "lease_payload": None,
        "last_error": "",
        **profile,
    }


def load_license_state() -> dict[str, Any]:
    payload = _default_state()
    payload.update(_safe_json_load(_state_file_path()))
    for key in (
        "activated_at",
        "last_check_in_at",
        "next_check_in_at",
        "last_valid_until",
        "expires_at",
        "lease_expires_at",
    ):
        payload[key] = _to_iso(payload.get(key))
    payload["server_url"] = (payload.get("server_url") or "").strip()
    payload["last_error"] = (payload.get("last_error") or "").strip()
    payload["lease_token"] = (payload.get("lease_token") or "").strip()
    payload["refresh_token"] = (payload.get("refresh_token") or "").strip()
    payload["activation_token"] = (payload.get("activation_token") or "").strip()
    payload["license_key_id"] = (payload.get("license_key_id") or "").strip()
    payload["activation_id"] = (payload.get("activation_id") or "").strip()
    payload["masked_code"] = (payload.get("masked_code") or "").strip()
    payload["lease_sequence"] = int(payload.get("lease_sequence") or 0)
    return payload


def save_license_state(payload: dict[str, Any]) -> dict[str, Any]:
    merged = _default_state()
    merged.update(payload)
    merged["version"] = LICENSE_STATE_VERSION
    for key in (
        "activated_at",
        "last_check_in_at",
        "next_check_in_at",
        "last_valid_until",
        "expires_at",
        "lease_expires_at",
    ):
        merged[key] = _to_iso(merged.get(key))
    _safe_json_dump(_state_file_path(), merged)
    return merged


def _extract_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        text = (response.text or "").strip()
        return text or f"HTTP {response.status_code}"

    detail = payload.get("detail")
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    if isinstance(detail, list):
        parts: list[str] = []
        for item in detail:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                msg = str(item.get("msg") or "").strip()
                if msg:
                    parts.append(msg)
        if parts:
            return "；".join(parts)
    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return f"HTTP {response.status_code}"


def _post_hosted_client_auth(server_url: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    endpoint = _build_client_auth_endpoint(server_url, path)
    try:
        with httpx.Client(timeout=12.0) as client:
            response = client.post(endpoint, json=payload)
    except httpx.RequestError as exc:
        raise HttpError(503, f"授权服务不可达：{exc}") from exc

    if response.status_code >= 400:
        raise HttpError(response.status_code, _extract_error_message(response))

    try:
        return response.json()
    except ValueError as exc:
        raise HttpError(502, "授权服务返回了无效响应") from exc


def _resolve_error_state(message: str, fallback_state: str) -> str:
    text = (message or "").strip()
    if "撤销" in text:
        return STATE_REVOKED
    if "过期" in text:
        return STATE_EXPIRED
    if "令牌无效" in text or "续签令牌无效" in text or "不存在" in text or "不可用" in text:
        return STATE_INVALID
    return fallback_state


def _compute_next_check_in_at(now: datetime, heartbeat_interval_minutes: int) -> datetime:
    minutes = max(1, int(heartbeat_interval_minutes or 30))
    jitter_seconds = random.randint(15, 90)
    return now + timedelta(minutes=minutes) - timedelta(seconds=jitter_seconds)


def _verify_lease_payload(payload: dict[str, Any], current: dict[str, Any]) -> tuple[bool, str]:
    token = (payload.get("lease_token") or "").strip()
    if not token:
        payload["lease_payload"] = None
        return True, ""
    if not lease_verification_enabled():
        return True, ""
    try:
        decoded = decode_signed_lease(token, verify_exp=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[client.license] lease verify failed err=%s", exc)
        return False, "invalid_lease"

    expected_device = current.get("device_fingerprint") or payload.get("device_fingerprint") or ""
    if decoded.get("device_fingerprint") != expected_device:
        return False, "device_mismatch"
    if decoded.get("activation_id") != payload.get("activation_id"):
        return False, "activation_mismatch"
    payload["lease_payload"] = decoded
    if not payload.get("lease_expires_at") and decoded.get("exp"):
        payload["lease_expires_at"] = datetime.fromtimestamp(int(decoded["exp"])).isoformat()
    if decoded.get("lease_sequence") is not None:
        payload["lease_sequence"] = int(decoded["lease_sequence"])
    return True, ""


def _apply_remote_state(
    current: dict[str, Any],
    *,
    server_url: str,
    remote: dict[str, Any],
    local_state: str,
    error_message: str = "",
) -> dict[str, Any]:
    now = _now()
    heartbeat_interval = int(remote.get("heartbeat_interval_minutes") or current.get("heartbeat_interval_minutes") or 0)
    grace_period = int(remote.get("grace_period_minutes") or current.get("grace_period_minutes") or 0)
    next_state = {
        **current,
        "server_url": server_url,
        "local_state": local_state,
        "activation_status": remote.get("status") or current.get("activation_status") or "",
        "activation_id": remote.get("activation_id") or current.get("activation_id") or "",
        "activation_token": remote.get("activation_token") or current.get("activation_token") or "",
        "refresh_token": remote.get("refresh_token") or current.get("refresh_token") or "",
        "lease_token": remote.get("lease_token") or current.get("lease_token") or "",
        "lease_sequence": int(remote.get("lease_sequence") or current.get("lease_sequence") or 0),
        "lease_expires_at": remote.get("lease_expires_at") or current.get("lease_expires_at"),
        "license_key_id": remote.get("license_key_id") or current.get("license_key_id") or "",
        "masked_code": remote.get("masked_code") or current.get("masked_code") or "",
        "activated_at": current.get("activated_at") or now,
        "last_check_in_at": now,
        "next_check_in_at": _compute_next_check_in_at(now, heartbeat_interval),
        "last_valid_until": remote.get("last_valid_until") or current.get("last_valid_until"),
        "expires_at": remote.get("expires_at") or current.get("expires_at"),
        "heartbeat_interval_minutes": heartbeat_interval,
        "grace_period_minutes": grace_period,
        "plan": remote.get("plan") or current.get("plan"),
        "last_error": error_message or "",
    }
    valid_lease, lease_error = _verify_lease_payload(next_state, current)
    if not valid_lease:
        next_state["local_state"] = STATE_INVALID
        next_state["last_error"] = lease_error or "lease_invalid"
    return save_license_state(next_state)


def _derive_local_state(payload: dict[str, Any]) -> tuple[str, str]:
    now = _now()
    last_valid_until = _parse_datetime(payload.get("last_valid_until"))
    lease_expires_at = _parse_datetime(payload.get("lease_expires_at"))
    local_state = payload.get("local_state") or STATE_UNACTIVATED
    last_error = (payload.get("last_error") or "").strip()

    if not payload.get("activation_id"):
        return STATE_UNACTIVATED, ""

    if local_state == STATE_REVOKED:
        return local_state, last_error
    if local_state == STATE_INVALID and last_error in {
        "invalid_lease",
        "device_mismatch",
        "activation_mismatch",
        "令牌无效",
        "续签令牌无效",
    }:
        return STATE_INVALID, last_error

    valid_lease, lease_error = _verify_lease_payload(payload, payload)
    if not valid_lease:
        if lease_error in {"invalid_lease", "device_mismatch", "activation_mismatch"}:
            return STATE_INVALID, lease_error
        if last_valid_until and now <= last_valid_until:
            return STATE_GRACE, lease_error
        return STATE_INVALID, lease_error

    if lease_expires_at and now <= lease_expires_at:
        return STATE_ACTIVE, ""
    if last_valid_until and now <= last_valid_until:
        return STATE_GRACE, ""
    return STATE_EXPIRED, payload.get("last_error") or ""


def _build_public_status(payload: dict[str, Any]) -> dict[str, Any]:
    local_state, lease_error = _derive_local_state(payload)
    normalized_error = lease_error or ""
    if payload.get("local_state") != local_state or payload.get("last_error") != normalized_error:
        payload = save_license_state(
            {
                **payload,
                "local_state": local_state,
                "last_error": normalized_error,
            }
        )
    can_use_business = local_state in BUSINESS_ALLOWED_STATES
    return {
        "state": local_state,
        "state_label": STATE_LABELS.get(local_state, local_state),
        "can_use_business": can_use_business,
        "needs_activation": local_state == STATE_UNACTIVATED,
        "device_fingerprint": payload.get("device_fingerprint") or "",
        "device_name": payload.get("device_name") or "",
        "os_type": payload.get("os_type") or "",
        "os_version": payload.get("os_version") or "",
        "app_version": payload.get("app_version") or "",
        "activation_status": payload.get("activation_status") or "",
        "license_key_id": payload.get("license_key_id") or "",
        "masked_code": payload.get("masked_code") or "",
        "activated_at": payload.get("activated_at"),
        "last_check_in_at": payload.get("last_check_in_at"),
        "next_check_in_at": payload.get("next_check_in_at"),
        "last_valid_until": payload.get("last_valid_until"),
        "expires_at": payload.get("expires_at"),
        "lease_expires_at": payload.get("lease_expires_at"),
        "lease_sequence": int(payload.get("lease_sequence") or 0),
        "heartbeat_interval_minutes": int(payload.get("heartbeat_interval_minutes") or 0),
        "grace_period_minutes": int(payload.get("grace_period_minutes") or 0),
        "last_error": payload.get("last_error") or "",
        "plan": payload.get("plan"),
    }


def get_public_license_status() -> dict[str, Any]:
    return _build_public_status(load_license_state())


def client_can_use_business() -> bool:
    """客户端模式下是否允许执行业务（托管/自动回复/写操作）。"""
    from env import ENV

    if ENV != "client":
        return True
    return bool(get_public_license_status().get("can_use_business"))


def activate_remote_license(*, server_url: str = "", license_code: str) -> dict[str, Any]:
    code = (license_code or "").strip()
    if not code:
        raise HttpError(400, "卡密不能为空")

    current = load_license_state()
    profile = get_device_profile()
    remote = _post_hosted_client_auth(
        server_url,
        "/activate",
        {
            "license_code": code,
            "device_fingerprint": profile["device_fingerprint"],
            "device_name": profile["device_name"],
            "os_type": profile["os_type"],
            "os_version": profile["os_version"],
            "app_version": profile["app_version"],
            "machine_meta": profile["machine_meta"],
        },
    )
    state = _apply_remote_state(
        {**current, **profile},
        server_url=_normalize_server_url(server_url),
        remote=remote,
        local_state=STATE_ACTIVE,
    )
    ensure_license_renewer_started()
    return _build_public_status(state)


def refresh_remote_license(*, force: bool = False) -> dict[str, Any]:
    current = load_license_state()
    activation_id = current.get("activation_id") or ""
    refresh_token = current.get("refresh_token") or ""
    activation_token = current.get("activation_token") or ""
    server_url = current.get("server_url") or ""
    if not activation_id or not server_url or not (refresh_token or activation_token):
        return _build_public_status(current)

    now = _now()
    next_check_in_at = _parse_datetime(current.get("next_check_in_at"))
    lease_expires_at = _parse_datetime(current.get("lease_expires_at"))
    renew_skew = timedelta(seconds=lease_renew_skew_seconds())
    if (
        not force
        and next_check_in_at
        and now < next_check_in_at
        and lease_expires_at
        and now + renew_skew < lease_expires_at
    ):
        return _build_public_status(current)

    profile = get_device_profile()
    payload = {
        "activation_id": activation_id,
        "refresh_token": refresh_token,
        "activation_token": activation_token if not refresh_token else "",
        "app_version": profile["app_version"],
        "machine_meta": profile["machine_meta"],
    }
    try:
        remote = _post_hosted_client_auth(server_url, "/check-in", payload)
        state = _apply_remote_state(
            {**current, **profile},
            server_url=server_url,
            remote=remote,
            local_state=STATE_ACTIVE,
        )
        return _build_public_status(state)
    except HttpError as exc:
        fallback_state = STATE_INVALID
        last_valid_until = _parse_datetime(current.get("last_valid_until"))
        if exc.status_code >= 500 or exc.status_code == 503:
            fallback_state = STATE_GRACE if last_valid_until and now <= last_valid_until else STATE_EXPIRED
        elif last_valid_until and now <= last_valid_until:
            fallback_state = STATE_GRACE
        resolved_state = _resolve_error_state(str(exc.message), fallback_state)
        persisted_profile = {
            **profile,
            "device_fingerprint": current.get("device_fingerprint") or profile.get("device_fingerprint") or "",
        }
        state = save_license_state(
            {
                **current,
                **persisted_profile,
                "local_state": resolved_state,
                "last_error": str(exc.message),
            }
        )
        return _build_public_status(state)


def deactivate_remote_license(*, reason: str = "") -> dict[str, Any]:
    current = load_license_state()
    activation_id = current.get("activation_id") or ""
    activation_token = current.get("activation_token") or ""
    server_url = current.get("server_url") or ""
    if activation_id and activation_token and server_url:
        try:
            _post_hosted_client_auth(
                server_url,
                "/deactivate",
                {
                    "activation_id": activation_id,
                    "activation_token": activation_token,
                    "reason": (reason or "").strip() or "客户端主动解绑",
                },
            )
        except HttpError as exc:
            logger.warning("[client.license] deactivate remote failed err=%s", exc)

    state = save_license_state({**_default_state(), "local_state": STATE_UNACTIVATED})
    return _build_public_status(state)


def _lease_renewer_loop() -> None:
    while True:
        try:
            status = refresh_remote_license(force=False)
            interval_minutes = max(1, int(status.get("heartbeat_interval_minutes") or 5))
            sleep_seconds = max(20, min(interval_minutes * 30, 300))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[client.license] renewer tick failed err=%s", exc)
            sleep_seconds = 60
        time.sleep(sleep_seconds)


def ensure_license_renewer_started() -> None:
    global _lease_renewer_started
    if _lease_renewer_started:
        return
    with _lease_renewer_lock:
        if _lease_renewer_started:
            return
        thread = threading.Thread(target=_lease_renewer_loop, name="client-license-renewer", daemon=True)
        thread.start()
        _lease_renewer_started = True


def require_client_business_access(action_label: str = "当前操作") -> dict[str, Any]:
    from env import ENV

    if ENV != "client":
        return {}

    status = get_public_license_status()
    if status["can_use_business"]:
        if status["state"] == STATE_GRACE:
            ensure_license_renewer_started()
            threading.Thread(target=refresh_remote_license, kwargs={"force": True}, daemon=True).start()
        return status

    ensure_license_renewer_started()
    if status["state"] == STATE_UNACTIVATED:
        raise HttpError(403, f"当前设备尚未激活，无法执行{action_label}")
    raise HttpError(403, f"当前授权状态为「{status['state_label']}」，无法执行{action_label}")
