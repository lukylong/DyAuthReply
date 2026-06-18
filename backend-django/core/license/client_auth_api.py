#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Hosted client authorization APIs."""
from ninja import Router

from core.license.license_schema import (
    ClientAuthActivateIn,
    ClientAuthStateOut,
    ClientAuthCheckInIn,
    ClientAuthDeactivateIn,
    ClientAuthDeactivateOut,
)
from core.license.license_service import (
    activate_license,
    check_in_activation,
    deactivate_activation,
    get_client_ip,
)

router = Router()


@router.post("/activate", response=ClientAuthStateOut, auth=None, summary="客户端激活卡密")
def activate(request, data: ClientAuthActivateIn):
    return activate_license(
        license_code=data.license_code,
        device_fingerprint=data.device_fingerprint,
        device_name=data.device_name or "",
        os_type=data.os_type or "",
        os_version=data.os_version or "",
        app_version=data.app_version or "",
        machine_meta=data.machine_meta or {},
        ip=get_client_ip(request),
    )


@router.post("/check-in", response=ClientAuthStateOut, auth=None, summary="客户端心跳校验")
def check_in(request, data: ClientAuthCheckInIn):
    return check_in_activation(
        activation_id=data.activation_id,
        activation_token=data.activation_token or "",
        refresh_token=data.refresh_token or "",
        app_version=data.app_version or "",
        machine_meta=data.machine_meta or {},
        ip=get_client_ip(request),
    )


@router.post("/deactivate", response=ClientAuthDeactivateOut, auth=None, summary="客户端主动解绑")
def deactivate(request, data: ClientAuthDeactivateIn):
    return deactivate_activation(
        activation_id=data.activation_id,
        activation_token=data.activation_token,
        reason=(data.reason or "").strip(),
        ip=get_client_ip(request),
    )
