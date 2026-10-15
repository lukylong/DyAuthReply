#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Opt-in client diagnostics: bounded ingest, retention and admin queries.

The client already redacts, but the server enforces the same allow-list so an
old or modified client cannot store message text, identities or secrets here.
"""
import json
from datetime import timedelta
from typing import Any, Dict, List

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from ninja.errors import HttpError

from core.license.diagnostic_model import ClientDiagnosticEvent, ClientDiagnosticState
from core.license.license_model import ClientDevice, LicenseActivation

RETENTION_DAYS = 30
MIN_REPORT_INTERVAL = timedelta(seconds=60)
NEXT_REPORT_SECONDS = 900
MAX_EVENTS = 500
MAX_SNAPSHOT_BYTES = 64 * 1024
MAX_VALUE_CHARS = 300
LEVELS = {"INFO", "WARN", "ERROR"}
# Every field a client may attach to an event. Anything else is dropped.
EVENT_FIELDS = {
    "account", "step", "status", "body", "classification", "response_codes", "error",
    "rule", "retry_attempt", "inserted", "candidates", "ignored", "deferred",
    "reconciled_sends", "members", "host", "source", "result", "reason", "phase",
    "attempt", "elapsed_ms", "version", "count",
}


def _clean_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    return str(value)[:MAX_VALUE_CHARS]


def _clean_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
    return {key: _clean_value(value) for key, value in fields.items() if key in EVENT_FIELDS}


def _server_time(value):
    """Client times are UTC with an offset; store them in the server's convention
    (this project runs with USE_TZ=False, i.e. naive local time)."""
    if settings.USE_TZ:
        return value if timezone.is_aware(value) else timezone.make_aware(value)
    return timezone.make_naive(value) if timezone.is_aware(value) else value


@transaction.atomic
def ingest(activation: LicenseActivation, data) -> Dict[str, Any]:
    device: ClientDevice = activation.client_device
    if device is None:
        raise HttpError(403, "激活记录未绑定设备")
    if device.status != ClientDevice.STATUS_ACTIVE:
        raise HttpError(403, "设备已被禁用")
    if len(data.events) > MAX_EVENTS:
        raise HttpError(413, "诊断事件过多")
    if len(json.dumps(data.snapshot, ensure_ascii=False)) > MAX_SNAPSHOT_BYTES:
        raise HttpError(413, "诊断快照过大")

    now = timezone.now()
    state, _ = ClientDiagnosticState.objects.select_for_update().get_or_create(device=device)
    if state.last_report_at and now - state.last_report_at < MIN_REPORT_INTERVAL:
        raise HttpError(429, "诊断上报过于频繁")

    earliest = now - timedelta(days=RETENTION_DAYS)
    latest = now + timedelta(minutes=5)
    events: List[ClientDiagnosticEvent] = []
    for item in data.events:
        level = item.level.upper()
        occurred_at = _server_time(item.at)
        if level not in LEVELS or not (earliest <= occurred_at <= latest):
            continue
        events.append(
            ClientDiagnosticEvent(
                device=device,
                occurred_at=occurred_at,
                level=level,
                target=item.target[:128],
                message=item.message[:512],
                fields=_clean_fields(item.fields),
                app_version=data.app_version,
            )
        )
    ClientDiagnosticEvent.objects.bulk_create(events)

    state.activation = activation
    state.app_version = data.app_version
    state.os = data.os
    state.arch = data.arch
    state.snapshot = data.snapshot
    state.last_report_at = now
    state.report_count += 1
    state.save()
    ClientDiagnosticEvent.objects.filter(device=device, occurred_at__lt=earliest).delete()
    return {"ok": True, "accepted": len(events), "next_after_seconds": NEXT_REPORT_SECONDS}


def purge_expired() -> int:
    """Removes events past retention for every device (including silent ones)."""
    cutoff = timezone.now() - timedelta(days=RETENTION_DAYS)
    deleted, _ = ClientDiagnosticEvent.objects.filter(occurred_at__lt=cutoff).delete()
    return deleted


def _device_row(state: ClientDiagnosticState, warn: int, error: int) -> Dict[str, Any]:
    snapshot = state.snapshot if isinstance(state.snapshot, dict) else {}
    runtime = snapshot.get("runtime") if isinstance(snapshot.get("runtime"), dict) else {}
    accounts = snapshot.get("accounts") if isinstance(snapshot.get("accounts"), list) else []
    return {
        "device_id": str(state.device_id),
        "device_name": state.device.device_name,
        "device_fingerprint": state.device.device_fingerprint,
        "app_version": state.app_version,
        "os": state.os,
        "arch": state.arch,
        "last_report_at": state.last_report_at,
        "report_count": state.report_count,
        "warn_24h": warn,
        "error_24h": error,
        "runtime_phase": runtime.get("phase"),
        "accounts": accounts,
    }


def list_devices(app_version: str = "", os: str = "") -> List[Dict[str, Any]]:
    since = timezone.now() - timedelta(hours=24)
    queryset = (
        ClientDiagnosticState.objects.select_related("device")
        .filter(is_deleted=False)
        .annotate(
            warn_24h=Count(
                "device__diagnostic_events",
                filter=Q(device__diagnostic_events__level="WARN", device__diagnostic_events__occurred_at__gte=since),
            ),
            error_24h=Count(
                "device__diagnostic_events",
                filter=Q(device__diagnostic_events__level="ERROR", device__diagnostic_events__occurred_at__gte=since),
            ),
        )
        .order_by("-last_report_at")
    )
    if app_version:
        queryset = queryset.filter(app_version=app_version)
    if os:
        queryset = queryset.filter(os=os)
    return [_device_row(state, state.warn_24h, state.error_24h) for state in queryset]


def device_detail(device_id: str) -> Dict[str, Any]:
    state = ClientDiagnosticState.objects.select_related("device").filter(device_id=device_id, is_deleted=False).first()
    if state is None:
        raise HttpError(404, "该设备没有诊断数据")
    since = timezone.now() - timedelta(hours=24)
    recent = ClientDiagnosticEvent.objects.filter(device_id=device_id, occurred_at__gte=since)
    row = _device_row(state, recent.filter(level="WARN").count(), recent.filter(level="ERROR").count())
    row["snapshot"] = state.snapshot
    return row


def device_events(device_id: str, level: str = ""):
    queryset = ClientDiagnosticEvent.objects.filter(device_id=device_id)
    if level:
        queryset = queryset.filter(level=level.upper())
    return queryset.order_by("-occurred_at", "-id")
