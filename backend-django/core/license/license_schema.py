#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""License authorization schemas."""
from datetime import datetime
from typing import Any, Optional, List

from ninja import Field, ModelSchema, Schema
from pydantic import field_validator

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.license.license_model import (
    LicensePlan,
    LicenseKey,
    ClientDevice,
    LicenseActivation,
    LicenseEvent,
)


class LicensePlanFilters(FuFilters):
    code: Optional[str] = Field(None, q="code__icontains", alias="code")
    name: Optional[str] = Field(None, q="name__icontains", alias="name")
    is_active: Optional[bool] = Field(None, q="is_active", alias="is_active")


class LicenseKeyFilters(FuFilters):
    plan_id: Optional[str] = Field(None, q="plan_id", alias="plan_id")
    status: Optional[str] = Field(None, q="status", alias="status")
    batch_no: Optional[str] = Field(None, q="batch_no__icontains", alias="batch_no")
    masked_code: Optional[str] = Field(None, q="masked_code__icontains", alias="masked_code")


class ClientDeviceFilters(FuFilters):
    device_fingerprint: Optional[str] = Field(None, q="device_fingerprint__icontains", alias="device_fingerprint")
    os_type: Optional[str] = Field(None, q="os_type__icontains", alias="os_type")
    status: Optional[str] = Field(None, q="status", alias="status")


class LicenseActivationFilters(FuFilters):
    license_key_id: Optional[str] = Field(None, q="license_key_id", alias="license_key_id")
    client_device_id: Optional[str] = Field(None, q="client_device_id", alias="client_device_id")
    status: Optional[str] = Field(None, q="status", alias="status")


class LicensePlanIn(ModelSchema):
    @field_validator("code", check_fields=False)
    @classmethod
    def validate_code(cls, value: str) -> str:
        code = (value or "").strip().lower()
        if not code:
            raise ValueError("套餐编码不能为空")
        return code

    @field_validator("max_devices", "valid_days", "heartbeat_interval_minutes", "grace_period_minutes", check_fields=False)
    @classmethod
    def validate_numbers(cls, value: int) -> int:
        if value is not None and value < 0:
            raise ValueError("数值不能为负数")
        return value

    class Config:
        model = LicensePlan
        model_exclude = (*exclude_fields,)


class LicensePlanPatch(Schema):
    name: Optional[str] = None
    description: Optional[str] = None
    feature_flags: Optional[dict[str, Any]] = None
    max_devices: Optional[int] = None
    valid_days: Optional[int] = None
    heartbeat_interval_minutes: Optional[int] = None
    grace_period_minutes: Optional[int] = None
    is_active: Optional[bool] = None

    @field_validator("max_devices", "valid_days", "heartbeat_interval_minutes", "grace_period_minutes")
    @classmethod
    def validate_numbers(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value < 0:
            raise ValueError("数值不能为负数")
        return value


class LicensePlanOut(ModelSchema):
    id: str

    class Config:
        model = LicensePlan
        model_fields = "__all__"

    @staticmethod
    def resolve_id(obj):
        return str(obj.id) if obj.id else None


class LicenseKeyGenerateIn(Schema):
    plan_id: str
    count: int = Field(default=1, description="生成数量")
    issued_to: Optional[str] = None
    batch_no: Optional[str] = None
    expires_at: Optional[datetime] = None
    max_devices_override: Optional[int] = None
    valid_days_override: Optional[int] = None
    notes: Optional[str] = None

    @field_validator("count")
    @classmethod
    def validate_count(cls, value: int) -> int:
        if value < 1 or value > 200:
            raise ValueError("生成数量必须在 1-200 之间")
        return value

    @field_validator("max_devices_override", "valid_days_override")
    @classmethod
    def validate_overrides(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value < 0:
            raise ValueError("覆盖值不能为负数")
        return value


class LicenseKeyPatch(Schema):
    status: Optional[str] = None
    issued_to: Optional[str] = None
    batch_no: Optional[str] = None
    expires_at: Optional[datetime] = None
    max_devices_override: Optional[int] = None
    valid_days_override: Optional[int] = None
    notes: Optional[str] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in {choice[0] for choice in LicenseKey.STATUS_CHOICES}:
            raise ValueError("无效的卡密状态")
        return value


class LicenseKeyOut(ModelSchema):
    id: str
    plan_id: Optional[str] = Field(None, alias="plan_id")
    plan_name: Optional[str] = None
    effective_max_devices: Optional[int] = None
    effective_valid_days: Optional[int] = None

    class Config:
        model = LicenseKey
        model_fields = "__all__"

    @staticmethod
    def resolve_id(obj):
        return str(obj.id) if obj.id else None

    @staticmethod
    def resolve_plan_id(obj):
        return str(obj.plan_id) if obj.plan_id else None

    @staticmethod
    def resolve_plan_name(obj):
        return obj.plan.name if obj.plan_id and obj.plan else None

    @staticmethod
    def resolve_effective_max_devices(obj):
        return obj.get_effective_max_devices()

    @staticmethod
    def resolve_effective_valid_days(obj):
        return obj.get_effective_valid_days()


class GeneratedLicenseKeyOut(Schema):
    id: str
    code: str
    masked_code: str
    plan_id: str
    expires_at: Optional[datetime]
    max_devices: int


class LicenseKeyGenerateOut(Schema):
    count: int
    items: List[GeneratedLicenseKeyOut]


class ClientDeviceOut(ModelSchema):
    id: str

    class Config:
        model = ClientDevice
        model_fields = "__all__"

    @staticmethod
    def resolve_id(obj):
        return str(obj.id) if obj.id else None


class LicenseActivationOut(ModelSchema):
    id: str
    license_key_id: Optional[str] = Field(None, alias="license_key_id")
    client_device_id: Optional[str] = Field(None, alias="client_device_id")
    masked_code: Optional[str] = None
    device_fingerprint: Optional[str] = None

    class Config:
        model = LicenseActivation
        model_fields = "__all__"

    @staticmethod
    def resolve_id(obj):
        return str(obj.id) if obj.id else None

    @staticmethod
    def resolve_license_key_id(obj):
        return str(obj.license_key_id) if obj.license_key_id else None

    @staticmethod
    def resolve_client_device_id(obj):
        return str(obj.client_device_id) if obj.client_device_id else None

    @staticmethod
    def resolve_masked_code(obj):
        return obj.license_key.masked_code if obj.license_key_id and obj.license_key else None

    @staticmethod
    def resolve_device_fingerprint(obj):
        return obj.client_device.device_fingerprint if obj.client_device_id and obj.client_device else None


class LicenseEventOut(ModelSchema):
    id: str
    license_key_id: Optional[str] = Field(None, alias="license_key_id")
    client_device_id: Optional[str] = Field(None, alias="client_device_id")
    activation_id: Optional[str] = Field(None, alias="activation_id")

    class Config:
        model = LicenseEvent
        model_fields = "__all__"

    @staticmethod
    def resolve_id(obj):
        return str(obj.id) if obj.id else None

    @staticmethod
    def resolve_license_key_id(obj):
        return str(obj.license_key_id) if obj.license_key_id else None

    @staticmethod
    def resolve_client_device_id(obj):
        return str(obj.client_device_id) if obj.client_device_id else None

    @staticmethod
    def resolve_activation_id(obj):
        return str(obj.activation_id) if obj.activation_id else None


class LicenseRevokeIn(Schema):
    reason: str = Field(default="管理员撤销")


class LicenseUnbindIn(Schema):
    client_device_id: str
    reason: str = Field(default="管理员解绑")


class LicenseStatsOut(Schema):
    plans_total: int
    keys_total: int
    keys_active: int
    keys_pending: int
    keys_revoked: int
    activations_active: int
    devices_total: int


class ClientAuthActivateIn(Schema):
    license_code: str
    device_fingerprint: str
    device_name: Optional[str] = None
    os_type: Optional[str] = None
    os_version: Optional[str] = None
    app_version: Optional[str] = None
    machine_meta: dict[str, Any] = Field(default_factory=dict)


class ClientAuthCheckInIn(Schema):
    activation_id: str
    activation_token: Optional[str] = None
    refresh_token: Optional[str] = None
    app_version: Optional[str] = None
    machine_meta: dict[str, Any] = Field(default_factory=dict)


class ClientAuthDeactivateIn(Schema):
    activation_id: str
    activation_token: str
    reason: str = Field(default="客户端主动解绑")


# ---- 客户端卡片同步（阶段②：客户端为真源，推送到公网托管落地页/封面）----
class ClientCardUpsertIn(Schema):
    activation_id: str
    activation_token: str
    id: str = Field(..., description="客户端本地卡片 UUID，作为公网卡片 id")
    title: str
    description: Optional[str] = ""
    cover_file_id: Optional[str] = None
    target_url: str
    remark: Optional[str] = None
    status: bool = True


class ClientCardDeleteIn(Schema):
    activation_id: str
    activation_token: str
    id: str


class ClientCardSyncOut(Schema):
    id: str
    landing_url: str
    ok: bool = True


class ClientCardCoverOut(Schema):
    cover_file_id: str
    cover_url: str


class ClientAuthPlanSummary(Schema):
    id: str
    code: str
    name: str
    feature_flags: dict[str, Any]
    heartbeat_interval_minutes: int
    grace_period_minutes: int
    max_devices: int


class ClientAuthStateOut(Schema):
    activation_id: str
    activation_token: str
    refresh_token: str
    license_key_id: str
    masked_code: str
    status: str
    expires_at: Optional[datetime] = None
    last_valid_until: Optional[datetime] = None
    lease_token: str = ""
    lease_expires_at: Optional[datetime] = None
    lease_sequence: int = 0
    heartbeat_interval_minutes: int
    grace_period_minutes: int
    plan: ClientAuthPlanSummary


class ClientAuthDeactivateOut(Schema):
    ok: bool
    status: str


class AppVersionOut(Schema):
    version: str
    mandatory: bool = False
    notes: str = ""
    macos_url: str = ""
    windows_url: str = ""
    release_page: str = ""
