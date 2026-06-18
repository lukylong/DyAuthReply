#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Admin APIs for license authorization."""
from typing import List

from django.shortcuts import get_object_or_404
from ninja import Query, Router
from ninja.pagination import paginate
from ninja.errors import HttpError

from common.fu_pagination import MyPagination
from core.license.license_model import LicensePlan, LicenseKey, ClientDevice, LicenseActivation, LicenseEvent
from core.license.license_schema import (
    LicensePlanFilters,
    LicensePlanIn,
    LicensePlanOut,
    LicensePlanPatch,
    LicenseKeyGenerateIn,
    LicenseKeyGenerateOut,
    GeneratedLicenseKeyOut,
    LicenseKeyFilters,
    LicenseKeyOut,
    LicenseKeyPatch,
    ClientDeviceFilters,
    ClientDeviceOut,
    LicenseActivationFilters,
    LicenseActivationOut,
    LicenseEventOut,
    LicenseRevokeIn,
    LicenseUnbindIn,
    LicenseStatsOut,
)
from core.license.license_service import (
    generate_license_keys,
    revoke_license_key,
    unbind_device,
    get_license_stats,
    get_license_key_detail_queryset,
)

router = Router()


@router.post("/license/plan", response=LicensePlanOut, summary="创建授权套餐")
def create_license_plan(request, data: LicensePlanIn):
    payload = data.dict()
    payload["sys_creator"] = request.auth
    if LicensePlan.objects.filter(code=payload.get("code"), is_deleted=False).exists():
        raise HttpError(409, "套餐编码已存在")
    return LicensePlan.objects.create(**payload)


@router.get("/license/plan", response=List[LicensePlanOut], summary="获取授权套餐列表")
@paginate(MyPagination)
def list_license_plans(request, filters: LicensePlanFilters = Query(...)):
    return filters.filter(LicensePlan.objects.filter(is_deleted=False))


@router.get("/license/plan/{plan_id}", response=LicensePlanOut, summary="获取授权套餐详情")
def get_license_plan(request, plan_id: str):
    return get_object_or_404(LicensePlan, id=plan_id, is_deleted=False)


@router.patch("/license/plan/{plan_id}", response=LicensePlanOut, summary="更新授权套餐")
def patch_license_plan(request, plan_id: str, data: LicensePlanPatch):
    plan = get_object_or_404(LicensePlan, id=plan_id, is_deleted=False)
    for attr, value in data.dict(exclude_unset=True).items():
        setattr(plan, attr, value)
    plan.sys_modifier = request.auth
    plan.save()
    return plan


@router.post("/license/key/generate", response=LicenseKeyGenerateOut, summary="批量生成卡密")
def generate_license_key_batch(request, data: LicenseKeyGenerateIn):
    generated = generate_license_keys(request, data)
    items = [
        GeneratedLicenseKeyOut(
            id=str(item.instance.id),
            code=item.code,
            masked_code=item.instance.masked_code,
            plan_id=str(item.instance.plan_id),
            expires_at=item.instance.expires_at,
            max_devices=item.instance.get_effective_max_devices(),
        )
        for item in generated
    ]
    return LicenseKeyGenerateOut(count=len(items), items=items)


@router.get("/license/key", response=List[LicenseKeyOut], summary="获取卡密列表")
@paginate(MyPagination)
def list_license_keys(request, filters: LicenseKeyFilters = Query(...)):
    queryset = get_license_key_detail_queryset().filter(is_deleted=False)
    return filters.filter(queryset)


@router.get("/license/key/{license_key_id}", response=LicenseKeyOut, summary="获取卡密详情")
def get_license_key(request, license_key_id: str):
    return get_object_or_404(get_license_key_detail_queryset(), id=license_key_id, is_deleted=False)


@router.patch("/license/key/{license_key_id}", response=LicenseKeyOut, summary="更新卡密")
def patch_license_key(request, license_key_id: str, data: LicenseKeyPatch):
    license_key = get_object_or_404(LicenseKey.objects.select_related("plan"), id=license_key_id, is_deleted=False)
    for attr, value in data.dict(exclude_unset=True).items():
        setattr(license_key, attr, value)
    license_key.sys_modifier = request.auth
    license_key.save()
    return license_key


@router.post("/license/key/{license_key_id}/revoke", response=LicenseKeyOut, summary="撤销卡密")
def revoke_license(request, license_key_id: str, data: LicenseRevokeIn):
    license_key = get_object_or_404(LicenseKey.objects.select_related("plan"), id=license_key_id, is_deleted=False)
    return revoke_license_key(
        license_key=license_key,
        reason=(data.reason or "").strip(),
        ip=request.META.get("REMOTE_ADDR") or "",
    )


@router.post("/license/key/{license_key_id}/unbind-device", response=LicenseActivationOut, summary="解绑设备")
def unbind_license_device(request, license_key_id: str, data: LicenseUnbindIn):
    license_key = get_object_or_404(LicenseKey, id=license_key_id, is_deleted=False)
    return unbind_device(
        license_key=license_key,
        client_device_id=data.client_device_id,
        reason=(data.reason or "").strip(),
        ip=request.META.get("REMOTE_ADDR") or "",
    )


@router.get("/license/device", response=List[ClientDeviceOut], summary="获取设备列表")
@paginate(MyPagination)
def list_client_devices(request, filters: ClientDeviceFilters = Query(...)):
    return filters.filter(ClientDevice.objects.filter(is_deleted=False))


@router.get("/license/activation", response=List[LicenseActivationOut], summary="获取激活记录列表")
@paginate(MyPagination)
def list_license_activations(request, filters: LicenseActivationFilters = Query(...)):
    queryset = LicenseActivation.objects.select_related("license_key", "client_device").filter(is_deleted=False)
    return filters.filter(queryset)


@router.get("/license/event", response=List[LicenseEventOut], summary="获取授权事件列表")
@paginate(MyPagination)
def list_license_events(request):
    return LicenseEvent.objects.select_related("license_key", "client_device", "activation").filter(is_deleted=False)


@router.get("/license/stats", response=LicenseStatsOut, summary="获取授权统计")
def license_stats(request):
    return get_license_stats()
