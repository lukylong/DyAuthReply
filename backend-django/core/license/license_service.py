#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""License authorization service functions."""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
import string
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from ninja.errors import HttpError

from core.license.lease_token import (
    build_lease_payload,
    default_lease_expiry,
    issue_signed_lease,
    lease_signing_enabled,
)
from core.license.license_model import (
    LicensePlan,
    LicenseKey,
    ClientDevice,
    LicenseActivation,
    LicenseEvent,
)

logger = logging.getLogger(__name__)

LICENSE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


@dataclass
class GeneratedLicenseKey:
    instance: LicenseKey
    code: str


def normalize_license_code(code: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", (code or "")).upper()


def hash_license_code(code: str) -> str:
    normalized = normalize_license_code(code)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def mask_license_code(code: str) -> str:
    normalized = normalize_license_code(code)
    if len(normalized) <= 8:
        return normalized
    return f"{normalized[:4]}****{normalized[-4:]}"


def hash_activation_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def generate_license_code(prefix: str = "DYA", groups: int = 4, chars_per_group: int = 4) -> str:
    parts = [
        "".join(secrets.choice(LICENSE_ALPHABET) for _ in range(chars_per_group))
        for _ in range(groups)
    ]
    return "-".join([prefix, *parts])


def build_plan_summary(plan: LicensePlan, license_key: LicenseKey) -> dict[str, Any]:
    return {
        "id": str(plan.id),
        "code": plan.code,
        "name": plan.name,
        "feature_flags": plan.feature_flags or {},
        "heartbeat_interval_minutes": license_key.get_effective_heartbeat_interval_minutes(),
        "grace_period_minutes": license_key.get_effective_grace_period_minutes(),
        "max_devices": license_key.get_effective_max_devices(),
    }


def is_license_expired(license_key: LicenseKey) -> bool:
    return bool(license_key.expires_at and license_key.expires_at <= timezone.now())


def resolve_license_expiration(license_key: LicenseKey, now=None):
    now = now or timezone.now()
    if license_key.expires_at:
        return license_key.expires_at

    valid_days = license_key.get_effective_valid_days()
    if valid_days and valid_days > 0:
        return now + timedelta(days=valid_days)
    return None


def log_license_event(
    event_type: str,
    *,
    license_key: Optional[LicenseKey] = None,
    client_device: Optional[ClientDevice] = None,
    activation: Optional[LicenseActivation] = None,
    ip: str = "",
    payload: Optional[dict[str, Any]] = None,
) -> None:
    LicenseEvent.objects.create(
        license_key=license_key,
        client_device=client_device,
        activation=activation,
        event_type=event_type,
        payload=payload or {},
        ip=ip or "",
    )


def get_client_ip(request) -> str:
    forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
    return forwarded or request.META.get("REMOTE_ADDR") or ""


def generate_license_keys(request, data) -> list[GeneratedLicenseKey]:
    plan = LicensePlan.objects.filter(id=data.plan_id, is_deleted=False).first()
    if not plan:
        raise HttpError(404, "套餐不存在")
    if not plan.is_active:
        raise HttpError(400, "套餐已停用，无法发卡")

    generated: list[GeneratedLicenseKey] = []
    batch_no = (data.batch_no or "").strip() or timezone.now().strftime("B%Y%m%d%H%M%S")

    for _ in range(data.count):
        code = generate_license_code()
        key = LicenseKey.objects.create(
            plan=plan,
            code_hash=hash_license_code(code),
            masked_code=mask_license_code(code),
            issued_to=(data.issued_to or "").strip() or None,
            batch_no=batch_no,
            expires_at=data.expires_at,
            max_devices_override=data.max_devices_override,
            valid_days_override=data.valid_days_override,
            notes=(data.notes or "").strip() or None,
            sys_creator=request.auth,
        )
        generated.append(GeneratedLicenseKey(instance=key, code=code))
        log_license_event(
            "license_key_generated",
            license_key=key,
            payload={"plan_id": str(plan.id), "batch_no": batch_no},
        )
    return generated


def ensure_license_usable(license_key: LicenseKey) -> None:
    if license_key.status == LicenseKey.STATUS_REVOKED:
        raise HttpError(403, "卡密已被撤销")
    if license_key.status == LicenseKey.STATUS_EXPIRED or is_license_expired(license_key):
        if license_key.status != LicenseKey.STATUS_EXPIRED:
            license_key.status = LicenseKey.STATUS_EXPIRED
            license_key.save(update_fields=["status", "sys_update_datetime"])
        raise HttpError(403, "卡密已过期")
    if not license_key.plan.is_active:
        raise HttpError(403, "卡密所属套餐已停用")


def _upsert_device(*, device_fingerprint: str, device_name: str, os_type: str, os_version: str, app_version: str, machine_meta: dict[str, Any]):
    now = timezone.now()
    device, created = ClientDevice.objects.get_or_create(
        device_fingerprint=device_fingerprint,
        defaults={
            "device_name": device_name or None,
            "os_type": os_type or None,
            "os_version": os_version or None,
            "app_version": app_version or None,
            "machine_meta": machine_meta or {},
            "first_seen_at": now,
            "last_seen_at": now,
        },
    )
    if not created:
        device.device_name = device_name or device.device_name
        device.os_type = os_type or device.os_type
        device.os_version = os_version or device.os_version
        device.app_version = app_version or device.app_version
        device.machine_meta = machine_meta or device.machine_meta or {}
        if not device.first_seen_at:
            device.first_seen_at = now
        device.last_seen_at = now
        device.save()
    return device


def _issue_client_credentials(
    *,
    activation: LicenseActivation,
    license_key: LicenseKey,
    activation_token: str,
    refresh_token: str,
    now,
) -> dict[str, Any]:
    plan_summary = build_plan_summary(license_key.plan, license_key)
    lease_expires_at = activation.lease_expires_at or default_lease_expiry(now)
    if activation.lease_expires_at != lease_expires_at:
        activation.lease_expires_at = lease_expires_at
        activation.save(update_fields=["lease_expires_at", "sys_update_datetime"])

    lease_payload = build_lease_payload(
        activation_id=str(activation.id),
        license_key_id=str(license_key.id),
        device_fingerprint=activation.client_device.device_fingerprint,
        plan=plan_summary,
        feature_flags=plan_summary.get("feature_flags") or {},
        lease_sequence=activation.lease_sequence,
        now=now,
        lease_expires_at=lease_expires_at,
        grace_until=activation.last_valid_until,
    )
    lease_token = issue_signed_lease(lease_payload) if lease_signing_enabled() else ""

    return {
        "activation_id": str(activation.id),
        "activation_token": activation_token,
        "refresh_token": refresh_token,
        "license_key_id": str(license_key.id),
        "masked_code": license_key.masked_code,
        "status": activation.status,
        "expires_at": activation.expires_at,
        "last_valid_until": activation.last_valid_until,
        "lease_token": lease_token,
        "lease_expires_at": lease_expires_at,
        "lease_sequence": activation.lease_sequence,
        "heartbeat_interval_minutes": license_key.get_effective_heartbeat_interval_minutes(),
        "grace_period_minutes": license_key.get_effective_grace_period_minutes(),
        "plan": plan_summary,
    }


@transaction.atomic
def activate_license(*, license_code: str, device_fingerprint: str, device_name: str = "", os_type: str = "", os_version: str = "", app_version: str = "", machine_meta: Optional[dict[str, Any]] = None, ip: str = "") -> dict[str, Any]:
    normalized = normalize_license_code(license_code)
    if not normalized:
        raise HttpError(400, "卡密不能为空")

    license_key = LicenseKey.objects.select_related("plan").filter(
        code_hash=hash_license_code(normalized),
        is_deleted=False,
    ).first()
    if not license_key:
        raise HttpError(404, "卡密不存在")

    ensure_license_usable(license_key)

    device = _upsert_device(
        device_fingerprint=device_fingerprint,
        device_name=device_name,
        os_type=os_type,
        os_version=os_version,
        app_version=app_version,
        machine_meta=machine_meta or {},
    )

    if device.status != ClientDevice.STATUS_ACTIVE:
        raise HttpError(403, "设备已被禁用")

    active_qs = LicenseActivation.objects.select_for_update().filter(
        license_key=license_key,
        status=LicenseActivation.STATUS_ACTIVE,
        is_deleted=False,
    )
    activation = active_qs.filter(client_device=device).first()

    if activation is None:
        active_devices = active_qs.values("client_device_id").distinct().count()
        if active_devices >= license_key.get_effective_max_devices():
            raise HttpError(409, "该卡密已达到设备绑定上限，请先解绑旧设备")

    now = timezone.now()
    token = secrets.token_urlsafe(32)
    refresh_token = secrets.token_urlsafe(48)
    expires_at = resolve_license_expiration(license_key, now)
    last_valid_until = now + timedelta(minutes=license_key.get_effective_grace_period_minutes())
    lease_expires_at = default_lease_expiry(now)

    if activation is None:
        activation = LicenseActivation.objects.create(
            license_key=license_key,
            client_device=device,
            activation_token_hash=hash_activation_token(token),
            lease_refresh_token_hash=hash_refresh_token(refresh_token),
            status=LicenseActivation.STATUS_ACTIVE,
            activated_at=now,
            expires_at=expires_at,
            last_heartbeat_at=now,
            last_valid_until=last_valid_until,
            lease_expires_at=lease_expires_at,
            lease_sequence=1,
        )
        event_type = "license_activated"
    else:
        activation.activation_token_hash = hash_activation_token(token)
        activation.lease_refresh_token_hash = hash_refresh_token(refresh_token)
        activation.status = LicenseActivation.STATUS_ACTIVE
        activation.expires_at = expires_at
        activation.last_heartbeat_at = now
        activation.last_valid_until = last_valid_until
        activation.lease_expires_at = lease_expires_at
        activation.lease_sequence = max(1, int(activation.lease_sequence or 0) + 1)
        activation.revoked_at = None
        activation.revoked_reason = None
        activation.save()
        event_type = "license_reactivated"

    if license_key.status == LicenseKey.STATUS_PENDING:
        license_key.status = LicenseKey.STATUS_ACTIVE
    if not license_key.activated_at:
        license_key.activated_at = now
    license_key.expires_at = expires_at
    license_key.last_check_in_at = now
    license_key.save()

    log_license_event(
        event_type,
        license_key=license_key,
        client_device=device,
        activation=activation,
        ip=ip,
        payload={"app_version": app_version or "", "device_name": device_name or ""},
    )

    return _issue_client_credentials(
        activation=activation,
        license_key=license_key,
        activation_token=token,
        refresh_token=refresh_token,
        now=now,
    )


def get_activation_by_token(activation_id: str, activation_token: str) -> LicenseActivation:
    activation = LicenseActivation.objects.select_related("license_key__plan", "client_device").filter(
        id=activation_id,
        is_deleted=False,
    ).first()
    if not activation:
        raise HttpError(404, "激活记录不存在")
    if activation.activation_token_hash != hash_activation_token(activation_token):
        raise HttpError(401, "激活令牌无效")
    return activation


def get_activation_by_refresh_token(activation_id: str, refresh_token: str) -> LicenseActivation:
    activation = LicenseActivation.objects.select_related("license_key__plan", "client_device").filter(
        id=activation_id,
        is_deleted=False,
    ).first()
    if not activation:
        raise HttpError(404, "激活记录不存在")
    if activation.lease_refresh_token_hash != hash_refresh_token(refresh_token):
        raise HttpError(401, "续签令牌无效")
    return activation


@transaction.atomic
def check_in_activation(
    *,
    activation_id: str,
    activation_token: str = "",
    refresh_token: str = "",
    app_version: str = "",
    machine_meta: Optional[dict[str, Any]] = None,
    ip: str = "",
) -> dict[str, Any]:
    if refresh_token:
        activation = get_activation_by_refresh_token(activation_id, refresh_token)
    elif activation_token:
        activation = get_activation_by_token(activation_id, activation_token)
    else:
        raise HttpError(400, "缺少续签凭证")
    license_key = activation.license_key
    ensure_license_usable(license_key)

    if activation.status != LicenseActivation.STATUS_ACTIVE:
        raise HttpError(403, "激活状态不可用")

    now = timezone.now()
    if activation.expires_at and activation.expires_at <= now:
        activation.status = LicenseActivation.STATUS_EXPIRED
        activation.save(update_fields=["status", "sys_update_datetime"])
        raise HttpError(403, "授权已过期")

    next_activation_token = secrets.token_urlsafe(32)
    next_refresh_token = secrets.token_urlsafe(48)
    activation.last_heartbeat_at = now
    activation.last_valid_until = now + timedelta(minutes=license_key.get_effective_grace_period_minutes())
    activation.lease_expires_at = default_lease_expiry(now)
    activation.lease_sequence = max(1, int(activation.lease_sequence or 0) + 1)
    activation.activation_token_hash = hash_activation_token(next_activation_token)
    activation.lease_refresh_token_hash = hash_refresh_token(next_refresh_token)
    activation.save(
        update_fields=[
            "last_heartbeat_at",
            "last_valid_until",
            "lease_expires_at",
            "lease_sequence",
            "activation_token_hash",
            "lease_refresh_token_hash",
            "sys_update_datetime",
        ]
    )

    device = activation.client_device
    device.last_seen_at = now
    if app_version:
        device.app_version = app_version
    if machine_meta:
        device.machine_meta = machine_meta
    device.save(update_fields=["last_seen_at", "app_version", "machine_meta", "sys_update_datetime"])

    license_key.last_check_in_at = now
    license_key.save(update_fields=["last_check_in_at", "sys_update_datetime"])

    log_license_event(
        "license_check_in",
        license_key=license_key,
        client_device=device,
        activation=activation,
        ip=ip,
        payload={"app_version": app_version or ""},
    )

    return _issue_client_credentials(
        activation=activation,
        license_key=license_key,
        activation_token=next_activation_token,
        refresh_token=next_refresh_token,
        now=now,
    )


@transaction.atomic
def deactivate_activation(*, activation_id: str, activation_token: str, reason: str = "", ip: str = "") -> dict[str, Any]:
    activation = get_activation_by_token(activation_id, activation_token)
    activation.status = LicenseActivation.STATUS_DEACTIVATED
    activation.revoked_at = timezone.now()
    activation.revoked_reason = reason or "客户端主动解绑"
    activation.lease_refresh_token_hash = None
    activation.save()
    log_license_event(
        "license_deactivated",
        license_key=activation.license_key,
        client_device=activation.client_device,
        activation=activation,
        ip=ip,
        payload={"reason": activation.revoked_reason},
    )
    return {"ok": True, "status": activation.status}


@transaction.atomic
def revoke_license_key(*, license_key: LicenseKey, reason: str = "", ip: str = "") -> LicenseKey:
    now = timezone.now()
    license_key.status = LicenseKey.STATUS_REVOKED
    license_key.save(update_fields=["status", "sys_update_datetime"])
    LicenseActivation.objects.filter(
        license_key=license_key,
        status=LicenseActivation.STATUS_ACTIVE,
        is_deleted=False,
    ).update(
        status=LicenseActivation.STATUS_REVOKED,
        revoked_at=now,
        revoked_reason=reason or "管理员撤销",
        sys_update_datetime=now,
    )
    log_license_event(
        "license_key_revoked",
        license_key=license_key,
        ip=ip,
        payload={"reason": reason or "管理员撤销"},
    )
    return license_key


@transaction.atomic
def delete_license_key(*, license_key: LicenseKey, ip: str = "") -> None:
    """软删除单张卡密，同时撤销其活跃激活。"""
    now = timezone.now()
    LicenseActivation.objects.filter(
        license_key=license_key,
        status=LicenseActivation.STATUS_ACTIVE,
        is_deleted=False,
    ).update(
        status=LicenseActivation.STATUS_REVOKED,
        revoked_at=now,
        revoked_reason="卡密删除",
        sys_update_datetime=now,
    )
    license_key.is_deleted = True
    license_key.save(update_fields=["is_deleted", "sys_update_datetime"])
    log_license_event(
        "license_key_deleted",
        license_key=license_key,
        ip=ip,
        payload={"masked_code": license_key.masked_code},
    )


@transaction.atomic
def delete_license_plan(*, plan: LicensePlan, ip: str = "") -> dict[str, int]:
    """软删除套餐，并级联软删除该套餐下的卡密、撤销其活跃激活。"""
    now = timezone.now()
    keys = LicenseKey.objects.filter(plan=plan, is_deleted=False)
    key_ids = list(keys.values_list("id", flat=True))

    revoked_activations = 0
    if key_ids:
        revoked_activations = LicenseActivation.objects.filter(
            license_key_id__in=key_ids,
            status=LicenseActivation.STATUS_ACTIVE,
            is_deleted=False,
        ).update(
            status=LicenseActivation.STATUS_REVOKED,
            revoked_at=now,
            revoked_reason="套餐删除",
            sys_update_datetime=now,
        )

    deleted_keys = keys.update(is_deleted=True, sys_update_datetime=now)

    plan.is_deleted = True
    plan.save(update_fields=["is_deleted", "sys_update_datetime"])
    log_license_event(
        "license_plan_deleted",
        ip=ip,
        payload={
            "plan_id": str(plan.id),
            "plan_code": plan.code,
            "deleted_keys": deleted_keys,
            "revoked_activations": revoked_activations,
        },
    )
    return {"deleted_keys": deleted_keys, "revoked_activations": revoked_activations}


@transaction.atomic
def unbind_device(*, license_key: LicenseKey, client_device_id: str, reason: str = "", ip: str = "") -> LicenseActivation:
    activation = LicenseActivation.objects.select_related("client_device").filter(
        license_key=license_key,
        client_device_id=client_device_id,
        is_deleted=False,
    ).first()
    if not activation:
        raise HttpError(404, "未找到绑定关系")

    activation.status = LicenseActivation.STATUS_DEACTIVATED
    activation.revoked_at = timezone.now()
    activation.revoked_reason = reason or "管理员解绑"
    activation.save()
    log_license_event(
        "license_device_unbound",
        license_key=license_key,
        client_device=activation.client_device,
        activation=activation,
        ip=ip,
        payload={"reason": activation.revoked_reason},
    )
    return activation


def get_license_stats() -> dict[str, int]:
    status_counts = LicenseKey.objects.filter(is_deleted=False).values("status").annotate(total=Count("id"))
    status_map = {item["status"]: item["total"] for item in status_counts}
    return {
        "plans_total": LicensePlan.objects.filter(is_deleted=False).count(),
        "keys_total": LicenseKey.objects.filter(is_deleted=False).count(),
        "keys_active": status_map.get(LicenseKey.STATUS_ACTIVE, 0),
        "keys_pending": status_map.get(LicenseKey.STATUS_PENDING, 0),
        "keys_revoked": status_map.get(LicenseKey.STATUS_REVOKED, 0),
        "activations_active": LicenseActivation.objects.filter(
            status=LicenseActivation.STATUS_ACTIVE,
            is_deleted=False,
        ).count(),
        "devices_total": ClientDevice.objects.filter(is_deleted=False).count(),
    }


def get_license_key_detail_queryset():
    return LicenseKey.objects.select_related("plan").annotate(
        activation_count=Count("activations", distinct=True),
        active_device_count=Count(
            "activations",
            filter=Q(activations__status=LicenseActivation.STATUS_ACTIVE, activations__is_deleted=False),
            distinct=True,
        ),
    )
