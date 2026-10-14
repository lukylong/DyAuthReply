#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Opt-in client runtime diagnostics (off by default on the client).

Only allow-listed, already-redacted fields are accepted from the client: no
message text, nicknames, sec_uids, cookies, tokens or URLs with parameters.
"""
from django.db import models

from common.fu_model import RootModel
from core.license.license_model import ClientDevice, LicenseActivation


class ClientDiagnosticState(RootModel):
    """Latest diagnostic snapshot per device."""

    device = models.OneToOneField(
        ClientDevice,
        on_delete=models.CASCADE,
        related_name="diagnostic_state",
        help_text="设备",
    )
    activation = models.ForeignKey(
        LicenseActivation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="上报时的激活记录",
    )
    app_version = models.CharField(max_length=32, blank=True, default="", db_index=True, help_text="客户端版本")
    os = models.CharField(max_length=32, blank=True, default="", db_index=True, help_text="操作系统")
    arch = models.CharField(max_length=32, blank=True, default="", help_text="CPU 架构")
    snapshot = models.JSONField(default=dict, blank=True, help_text="最近一次运行快照")
    last_report_at = models.DateTimeField(null=True, blank=True, db_index=True, help_text="最近上报时间")
    report_count = models.PositiveIntegerField(default=0, help_text="累计上报次数")

    class Meta:
        db_table = "core_client_diagnostic_state"
        ordering = ("-last_report_at",)


class ClientDiagnosticEvent(models.Model):
    """One redacted runtime event (warnings, errors and key business milestones)."""

    LEVEL_CHOICES = [("INFO", "INFO"), ("WARN", "WARN"), ("ERROR", "ERROR")]

    id = models.BigAutoField(primary_key=True)
    device = models.ForeignKey(
        ClientDevice,
        on_delete=models.CASCADE,
        related_name="diagnostic_events",
        help_text="设备",
    )
    occurred_at = models.DateTimeField(db_index=True, help_text="客户端发生时间")
    received_at = models.DateTimeField(auto_now_add=True, help_text="服务器接收时间")
    level = models.CharField(max_length=8, choices=LEVEL_CHOICES, help_text="级别")
    target = models.CharField(max_length=128, help_text="模块")
    message = models.CharField(max_length=512, help_text="事件")
    fields = models.JSONField(default=dict, blank=True, help_text="白名单字段")
    app_version = models.CharField(max_length=32, blank=True, default="", help_text="客户端版本")

    class Meta:
        db_table = "core_client_diagnostic_event"
        ordering = ("-occurred_at", "-id")
        indexes = [
            models.Index(fields=["device", "-occurred_at"], name="diag_event_device_time"),
            models.Index(fields=["level", "-occurred_at"], name="diag_event_level_time"),
        ]
