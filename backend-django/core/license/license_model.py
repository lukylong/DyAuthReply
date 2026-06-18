#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""License authorization models."""
from django.db import models

from common.fu_model import RootModel


class LicensePlan(RootModel):
    """Fixed entitlement package for license keys."""

    code = models.CharField(max_length=64, unique=True, db_index=True, help_text="套餐编码")
    name = models.CharField(max_length=128, db_index=True, help_text="套餐名称")
    description = models.TextField(blank=True, null=True, help_text="套餐描述")
    feature_flags = models.JSONField(default=dict, blank=True, help_text="功能开关")
    max_devices = models.IntegerField(default=1, help_text="最大设备数")
    valid_days = models.IntegerField(default=365, help_text="默认有效天数，0 表示不按天数限制")
    heartbeat_interval_minutes = models.IntegerField(default=30, help_text="心跳间隔分钟")
    grace_period_minutes = models.IntegerField(default=2880, help_text="离线宽限期分钟")
    is_active = models.BooleanField(default=True, db_index=True, help_text="是否启用")

    class Meta:
        db_table = "core_license_plan"
        ordering = ("sort", "-sys_create_datetime")

    def __str__(self):
        return f"{self.name} ({self.code})"


class LicenseKey(RootModel):
    """Issued license key record."""

    STATUS_PENDING = "pending"
    STATUS_ACTIVE = "active"
    STATUS_REVOKED = "revoked"
    STATUS_EXPIRED = "expired"

    STATUS_CHOICES = [
        (STATUS_PENDING, "未激活"),
        (STATUS_ACTIVE, "生效中"),
        (STATUS_REVOKED, "已撤销"),
        (STATUS_EXPIRED, "已过期"),
    ]

    plan = models.ForeignKey(
        to="core.LicensePlan",
        on_delete=models.PROTECT,
        db_constraint=False,
        related_name="license_keys",
        help_text="授权套餐",
    )
    code_hash = models.CharField(max_length=64, unique=True, db_index=True, help_text="卡密哈希")
    masked_code = models.CharField(max_length=64, db_index=True, help_text="脱敏卡密")
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
        help_text="授权状态",
    )
    issued_to = models.CharField(max_length=128, blank=True, null=True, help_text="发放对象")
    batch_no = models.CharField(max_length=64, blank=True, null=True, db_index=True, help_text="批次号")
    expires_at = models.DateTimeField(blank=True, null=True, db_index=True, help_text="到期时间")
    activated_at = models.DateTimeField(blank=True, null=True, help_text="首次激活时间")
    last_check_in_at = models.DateTimeField(blank=True, null=True, db_index=True, help_text="最后心跳时间")
    max_devices_override = models.IntegerField(blank=True, null=True, help_text="设备数覆盖")
    valid_days_override = models.IntegerField(blank=True, null=True, help_text="有效天数覆盖")
    notes = models.TextField(blank=True, null=True, help_text="备注")

    class Meta:
        db_table = "core_license_key"
        ordering = ("-sys_create_datetime",)
        indexes = [
            models.Index(fields=["status", "plan"]),
            models.Index(fields=["expires_at", "status"]),
        ]

    def __str__(self):
        return f"{self.masked_code} ({self.status})"

    def get_effective_max_devices(self) -> int:
        return max(1, self.max_devices_override or self.plan.max_devices or 1)

    def get_effective_valid_days(self) -> int:
        return self.valid_days_override if self.valid_days_override is not None else self.plan.valid_days

    def get_effective_grace_period_minutes(self) -> int:
        return self.plan.grace_period_minutes

    def get_effective_heartbeat_interval_minutes(self) -> int:
        return self.plan.heartbeat_interval_minutes


class ClientDevice(RootModel):
    """Registered client installation or device."""

    STATUS_ACTIVE = "active"
    STATUS_BLOCKED = "blocked"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "活跃"),
        (STATUS_BLOCKED, "已封禁"),
    ]

    device_fingerprint = models.CharField(max_length=128, unique=True, db_index=True, help_text="设备指纹")
    device_name = models.CharField(max_length=128, blank=True, null=True, help_text="设备名称")
    os_type = models.CharField(max_length=32, blank=True, null=True, db_index=True, help_text="系统类型")
    os_version = models.CharField(max_length=64, blank=True, null=True, help_text="系统版本")
    app_version = models.CharField(max_length=64, blank=True, null=True, help_text="客户端版本")
    machine_meta = models.JSONField(default=dict, blank=True, help_text="设备元信息")
    first_seen_at = models.DateTimeField(blank=True, null=True, help_text="首次出现时间")
    last_seen_at = models.DateTimeField(blank=True, null=True, db_index=True, help_text="最后出现时间")
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        db_index=True,
        help_text="设备状态",
    )

    class Meta:
        db_table = "core_client_device"
        ordering = ("-last_seen_at", "-sys_create_datetime")

    def __str__(self):
        return self.device_name or self.device_fingerprint


class LicenseActivation(RootModel):
    """Activation binding between a key and a device."""

    STATUS_ACTIVE = "active"
    STATUS_REVOKED = "revoked"
    STATUS_DEACTIVATED = "deactivated"
    STATUS_EXPIRED = "expired"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "活跃"),
        (STATUS_REVOKED, "已撤销"),
        (STATUS_DEACTIVATED, "已解绑"),
        (STATUS_EXPIRED, "已过期"),
    ]

    license_key = models.ForeignKey(
        to="core.LicenseKey",
        on_delete=models.CASCADE,
        db_constraint=False,
        related_name="activations",
        help_text="关联卡密",
    )
    client_device = models.ForeignKey(
        to="core.ClientDevice",
        on_delete=models.CASCADE,
        db_constraint=False,
        related_name="activations",
        help_text="关联设备",
    )
    activation_token_hash = models.CharField(max_length=64, unique=True, db_index=True, help_text="激活令牌哈希")
    lease_refresh_token_hash = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        blank=True,
        null=True,
        help_text="续签令牌哈希",
    )
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        db_index=True,
        help_text="激活状态",
    )
    activated_at = models.DateTimeField(help_text="激活时间")
    expires_at = models.DateTimeField(blank=True, null=True, db_index=True, help_text="到期时间")
    last_heartbeat_at = models.DateTimeField(blank=True, null=True, db_index=True, help_text="最后心跳时间")
    last_valid_until = models.DateTimeField(blank=True, null=True, db_index=True, help_text="离线可用截止")
    lease_expires_at = models.DateTimeField(blank=True, null=True, db_index=True, help_text="当前租约到期时间")
    lease_sequence = models.IntegerField(default=0, help_text="租约版本序号")
    revoked_at = models.DateTimeField(blank=True, null=True, help_text="撤销时间")
    revoked_reason = models.CharField(max_length=255, blank=True, null=True, help_text="撤销原因")

    class Meta:
        db_table = "core_license_activation"
        ordering = ("-activated_at",)
        unique_together = [["license_key", "client_device"]]
        indexes = [
            models.Index(fields=["license_key", "status"]),
            models.Index(fields=["client_device", "status"]),
        ]

    def __str__(self):
        return f"{self.license_key.masked_code} -> {self.client_device.device_fingerprint}"


class LicenseEvent(RootModel):
    """Immutable audit event for authorization activity."""

    license_key = models.ForeignKey(
        to="core.LicenseKey",
        on_delete=models.SET_NULL,
        db_constraint=False,
        related_name="events",
        null=True,
        blank=True,
        help_text="关联卡密",
    )
    client_device = models.ForeignKey(
        to="core.ClientDevice",
        on_delete=models.SET_NULL,
        db_constraint=False,
        related_name="events",
        null=True,
        blank=True,
        help_text="关联设备",
    )
    activation = models.ForeignKey(
        to="core.LicenseActivation",
        on_delete=models.SET_NULL,
        db_constraint=False,
        related_name="events",
        null=True,
        blank=True,
        help_text="关联激活",
    )
    event_type = models.CharField(max_length=64, db_index=True, help_text="事件类型")
    payload = models.JSONField(default=dict, blank=True, help_text="事件载荷")
    ip = models.CharField(max_length=64, blank=True, null=True, help_text="来源 IP")

    class Meta:
        db_table = "core_license_event"
        ordering = ("-sys_create_datetime",)
        indexes = [
            models.Index(fields=["event_type", "sys_create_datetime"]),
        ]

    def __str__(self):
        return f"{self.event_type} @ {self.sys_create_datetime}"
