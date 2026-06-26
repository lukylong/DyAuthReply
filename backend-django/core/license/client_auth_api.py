#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Hosted client authorization APIs."""
from django.conf import settings
from ninja import File, Form, Router
from ninja.files import UploadedFile

from core.license.license_schema import (
    ClientAuthActivateIn,
    ClientAuthStateOut,
    ClientAuthCheckInIn,
    ClientAuthDeactivateIn,
    ClientAuthDeactivateOut,
    ClientCardCoverOut,
    ClientCardDeleteIn,
    ClientCardSyncOut,
    ClientCardUpsertIn,
    AppVersionOut,
)
from core.license.license_service import (
    activate_license,
    check_in_activation,
    deactivate_activation,
    get_activation_by_token,
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


# ---- 客户端卡片同步（客户端为真源，推送到公网托管落地页/封面）----

@router.post("/cards/upsert", response=ClientCardSyncOut, auth=None, summary="客户端卡片上行同步")
def card_upsert(request, data: ClientCardUpsertIn):
    """客户端创建/更新卡片后推送到公网。复用 activation 鉴权，按客户端本地 id upsert。"""
    from core.douyin.douyin_card_api import _validate_target_url
    from core.douyin.douyin_card_model import DouyinCard
    from core.douyin.douyin_card_schema import build_landing_url

    from ninja.errors import HttpError

    activation = get_activation_by_token(data.activation_id, data.activation_token)
    device = activation.client_device
    fingerprint = str(device.device_fingerprint) if device else None
    target_url = _validate_target_url(data.target_url)

    # 归属校验（防 IDOR）：若同 id 卡片已存在且来源是别的设备，拒绝覆盖。
    # 否则任意合法激活客户端都能用别人的 card id upsert 覆盖其 target_url（钓鱼重定向）。
    existing = DouyinCard.objects.filter(id=data.id).first()
    if (
        existing is not None
        and existing.source_device_id
        and fingerprint
        and existing.source_device_id != fingerprint
    ):
        raise HttpError(403, "无权覆盖其它设备的卡片")

    DouyinCard.objects.update_or_create(
        id=data.id,
        defaults={
            'title': data.title,
            'description': data.description or '',
            'cover_file_id': data.cover_file_id or None,
            'target_url': target_url,
            'remark': data.remark,
            'status': data.status,
            'source_device_id': fingerprint,
            'source_activation_id': str(activation.id),
            'sync_state': 'synced',
            'is_deleted': False,
        },
    )
    return {"id": data.id, "landing_url": build_landing_url(data.id), "ok": True}


@router.post("/cards/delete", response=ClientCardSyncOut, auth=None, summary="客户端卡片删除同步")
def card_delete(request, data: ClientCardDeleteIn):
    """客户端删除卡片后，停用/软删公网对应卡片。仅允许删除本设备来源的卡片（防越权）。"""
    from core.douyin.douyin_card_model import DouyinCard
    from core.douyin.douyin_card_schema import build_landing_url

    activation = get_activation_by_token(data.activation_id, data.activation_token)
    device = activation.client_device
    fingerprint = str(device.device_fingerprint) if device else None

    card = DouyinCard.objects.filter(id=data.id, is_deleted=False).first()
    if card is not None:
        # 归属校验：只能删除本设备同步上来的卡片
        if card.source_device_id and fingerprint and card.source_device_id != fingerprint:
            from ninja.errors import HttpError

            raise HttpError(403, "无权删除其它设备的卡片")
        card.status = False
        card.is_deleted = True
        card.save(update_fields=['status', 'is_deleted', 'sys_update_datetime'])
    return {"id": data.id, "landing_url": build_landing_url(data.id), "ok": True}


@router.post("/cards/cover", response=ClientCardCoverOut, auth=None, summary="客户端卡片封面上传")
def card_cover(
    request,
    file: UploadedFile = File(...),
    activation_id: str = Form(...),
    activation_token: str = Form(...),
):
    """客户端经 license 通道转发封面图，存公网 file_manager，返回公网 cover_file_id + cover_url。"""
    from ninja.errors import HttpError

    from core.douyin.douyin_card_schema import build_cover_url
    from core.file_manager.service import save_uploaded_public_file

    get_activation_by_token(activation_id, activation_token)

    # 安全限制：auth=None 公开上传，必须约束为图片且限制大小，
    # 否则任意合法激活客户端可往公网存储投放任意文件（如 .html 经 proxy 以 text/html 提供 → 存储型 XSS / 滥用托管）。
    _MAX_COVER_BYTES = 2 * 1024 * 1024  # 2MB（与 design 风险表一致）
    _ALLOWED_COVER_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'}
    size = getattr(file, 'size', 0) or 0
    if size > _MAX_COVER_BYTES:
        raise HttpError(400, "封面图过大（上限 2MB）")
    import os as _os

    ext = _os.path.splitext(file.name or '')[1].lower()
    content_type = (getattr(file, 'content_type', '') or '').lower()
    if ext not in _ALLOWED_COVER_EXT or not content_type.startswith('image/'):
        raise HttpError(400, "封面仅支持图片格式（jpg/png/webp/gif/bmp）")

    file_obj = save_uploaded_public_file(file, file.name)
    return {
        "cover_file_id": str(file_obj.id),
        "cover_url": build_cover_url(str(file_obj.id)) or "",
    }
