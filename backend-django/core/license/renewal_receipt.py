"""Hosted authority: one encrypted idempotent renewal receipt per activation.

This is remote Django server logic, not a local Python client dependency.
"""
import base64
import hashlib
import hmac
import json
from uuid import UUID
from cryptography.fernet import Fernet
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from ninja.errors import HttpError
from core.license.license_model import LicenseActivation, LicenseKey
from core.license.license_service import check_in_activation, ensure_license_usable


def _cipher():
    key = hashlib.sha256(('license-renewal-receipt-v1\0' + settings.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


@transaction.atomic
def renew_once(*, request_id=None, **payload):
    if request_id is None:
        return check_in_activation(**payload)
    nonce = str(UUID(str(request_id)))
    canonical = json.dumps({k: v for k, v in payload.items() if k != 'ip'}, sort_keys=True, separators=(',', ':'))
    if len(canonical.encode()) > 32 * 1024:
        raise HttpError(400, '续签请求超过大小限制')
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    activation = LicenseActivation.objects.select_related('license_key', 'client_device').filter(id=payload['activation_id']).first()
    if activation is None:
        raise HttpError(400, '激活记录不存在')
    LicenseKey.objects.filter(pk=activation.license_key_id).update(last_check_in_at=F('last_check_in_at'))
    activation = LicenseActivation.objects.select_for_update().select_related('license_key', 'client_device').get(pk=activation.pk)
    receipt = activation.renewal_receipt or {}
    if receipt.get('request_id') == nonce:
        if not hmac.compare_digest(receipt.get('request_hash', ''), digest):
            raise HttpError(409, '续签请求标识已用于不同内容')
        ensure_license_usable(activation.license_key)
        if activation.status != LicenseActivation.STATUS_ACTIVE or (activation.expires_at and activation.expires_at <= timezone.now()):
            raise HttpError(403, '激活状态不可用')
        if receipt.get('sequence') != activation.lease_sequence:
            raise HttpError(409, '该续签响应已被后续续签替代')
        try:
            response = json.loads(_cipher().decrypt(receipt['encrypted_response'].encode()))
        except Exception as exc:
            raise HttpError(503, '续签恢复记录读取失败') from exc
        return response
    response = check_in_activation(**payload)
    response['request_id'] = nonce
    encoded = json.dumps(response, cls=DjangoJSONEncoder, separators=(',', ':')).encode()
    if len(encoded) > 64 * 1024:
        raise HttpError(500, '续签响应超过大小限制')
    LicenseActivation.objects.filter(pk=activation.pk).update(renewal_receipt={
        'request_id': nonce, 'request_hash': digest, 'sequence': response['lease_sequence'],
        'encrypted_response': _cipher().encrypt(encoded).decode(),
    })
    return response
