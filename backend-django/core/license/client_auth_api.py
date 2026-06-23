#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Hosted client authorization APIs."""
from django.conf import settings
from ninja import Router

from core.license.license_schema import (
    ClientAuthActivateIn,
    ClientAuthStateOut,
    ClientAuthCheckInIn,
    ClientAuthDeactivateIn,
    ClientAuthDeactivateOut,
    AppVersionOut,
)
from core.license.license_service import (
    activate_license,
    check_in_activation,
    deactivate_activation,
    get_client_ip,
)

router = Router()


@router.get("/app-version", response=AppVersionOut, auth=None, summary="客户端最新版本信息")
def app_version(request):
    """对外公开的客户端升级通道：返回最新版本号、下载链接与更新说明。"""
    return {
        "version": settings.DOWNLOAD_LATEST_VERSION,
        "mandatory": settings.DOWNLOAD_FORCE_UPDATE,
        "notes": settings.DOWNLOAD_RELEASE_NOTES,
        "macos_url": settings.DOWNLOAD_MACOS_URL,
        "windows_url": settings.DOWNLOAD_WINDOWS_URL,
        "release_page": settings.DOWNLOAD_RELEASE_PAGE,
    }


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
