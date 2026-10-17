#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Schemas for user-controllable client runtime diagnostics."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from ninja import Field, Schema


class DiagnosticEventIn(Schema):
    at: datetime
    level: str = Field(..., max_length=8)
    target: str = Field(..., max_length=128)
    message: str = Field(..., max_length=512)
    fields: Dict[str, Any] = Field(default_factory=dict)


class DiagnosticIngestIn(Schema):
    activation_id: str = Field(..., max_length=64)
    activation_token: str = Field(..., max_length=256)
    app_version: str = Field("", max_length=32)
    os: str = Field("", max_length=32)
    arch: str = Field("", max_length=32)
    snapshot: Dict[str, Any] = Field(default_factory=dict)
    events: List[DiagnosticEventIn] = Field(default_factory=list)


class DiagnosticIngestOut(Schema):
    ok: bool
    accepted: int
    next_after_seconds: int


class DiagnosticDeviceOut(Schema):
    device_id: str
    device_name: Optional[str] = None
    device_fingerprint: str
    app_version: str
    os: str
    arch: str
    last_report_at: Optional[datetime] = None
    report_count: int
    warn_24h: int
    error_24h: int
    runtime_phase: Optional[str] = None
    accounts: List[Dict[str, Any]] = Field(default_factory=list)
    runtime: Dict[str, Any] = Field(default_factory=dict)
    storage_pressure: Optional[str] = None
    protocol_parity: Optional[bool] = None


class DiagnosticDeviceDetailOut(DiagnosticDeviceOut):
    snapshot: Dict[str, Any] = Field(default_factory=dict)


class DiagnosticEventOut(Schema):
    id: int
    occurred_at: datetime
    received_at: datetime
    level: str
    target: str
    message: str
    fields: Dict[str, Any]
    app_version: str
