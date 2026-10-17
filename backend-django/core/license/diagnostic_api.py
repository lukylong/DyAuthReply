#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Admin APIs for user-controllable client runtime diagnostics."""
from typing import List

from ninja import Router
from ninja.pagination import paginate

from common.fu_pagination import MyPagination
from core.license.diagnostic_schema import DiagnosticDeviceDetailOut, DiagnosticDeviceOut, DiagnosticEventOut
from core.license.diagnostic_service import device_detail, device_events, list_devices

router = Router()


@router.get("/license/diagnostics/device", response=List[DiagnosticDeviceOut], summary="诊断设备列表")
@paginate(MyPagination)
def list_diagnostic_devices(request, app_version: str = "", os: str = ""):
    return list_devices(app_version=app_version, os=os)


@router.get("/license/diagnostics/device/{device_id}", response=DiagnosticDeviceDetailOut, summary="诊断设备详情")
def get_diagnostic_device(request, device_id: str):
    return device_detail(device_id)


@router.get("/license/diagnostics/device/{device_id}/events", response=List[DiagnosticEventOut], summary="诊断事件")
@paginate(MyPagination)
def list_diagnostic_events(request, device_id: str, level: str = ""):
    return device_events(device_id, level)
