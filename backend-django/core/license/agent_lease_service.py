"""Account ownership authority. A signed batch is the only lease grant to a Rust agent."""
from __future__ import annotations
import hashlib
import hmac
import json
from datetime import timedelta

import jwt
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from ninja.errors import HttpError

from core.license.lease_token import get_lease_private_key
from core.license.license_model import AgentAccountLease, AgentSyncState, LicenseActivation, LicenseKey, ClientDevice
from core.license.license_service import get_activation_by_token, hash_activation_token, ensure_license_usable
from core.license.agent_lease_schema import AgentLeaseSyncIn, MAX_EPOCH

ISSUER = 'dyauthreply-account-lease'
AUDIENCE = 'dy-agent'
TTL_SECONDS = 45
MAX_BOOT_RECORDS = 256
MAX_LEASE_ROWS = 2048
RETENTION_HOURS = 24


def _authorize(data, now, license_key_id):
    # Taking a write lock before transactional reads also serializes SQLite.
    LicenseKey.objects.filter(pk=license_key_id).update(agent_fence_counter=F('agent_fence_counter'))
    license_key = LicenseKey.objects.select_for_update().select_related('plan').get(pk=license_key_id)
    activation = LicenseActivation.objects.select_for_update().select_related('client_device').get(pk=data.activation_id)
    if not hmac.compare_digest(activation.activation_token_hash, hash_activation_token(data.activation_token)):
        raise HttpError(401, '激活令牌已变更')
    if activation.license_key_id != license_key.pk:
        raise HttpError(409, '授权归属已变更，请重试')
    ensure_license_usable(license_key)
    if license_key.status != LicenseKey.STATUS_ACTIVE:
        raise HttpError(403, '授权尚未生效')
    if license_key.is_deleted or not license_key.plan.is_active or license_key.plan.is_deleted:
        raise HttpError(403, '授权套餐不可用')
    if activation.is_deleted or activation.status != LicenseActivation.STATUS_ACTIVE:
        raise HttpError(403, '激活状态不可用')
    device = activation.client_device
    if device.is_deleted or device.status != ClientDevice.STATUS_ACTIVE:
        raise HttpError(403, '设备不可用')
    if activation.expires_at and activation.expires_at <= now:
        raise HttpError(403, '激活已过期')
    if not activation.lease_expires_at or activation.lease_expires_at <= now:
        raise HttpError(403, '请先续签客户端授权')
    until = min([now + timedelta(seconds=TTL_SECONDS), activation.lease_expires_at]
                + [t for t in (license_key.expires_at, activation.expires_at) if t])
    return activation, license_key, until


def _request_hash(data):
    raw = data.model_dump(mode='json', exclude={'activation_token'})
    return hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _sign(claims, key):
    # PostgreSQL JSONB reorders object keys on read. Sign one canonical ordering
    # both before persistence and on replay so identical requests retain the
    # original token as well as its claims/deadline on every supported backend.
    canonical = json.loads(json.dumps(claims, sort_keys=True))
    return {'token': jwt.encode(canonical, key, algorithm='EdDSA')}


def _prune(activation, license_key, now):
    cutoff = now - timedelta(hours=RETENTION_HOURS)
    ids = list(AgentSyncState.objects.filter(activation=activation, updated_at__lt=cutoff).values_list('pk', flat=True)[:64])
    if ids:
        AgentSyncState.objects.filter(pk__in=ids).delete()
    # Epoch uniqueness lives on LicenseKey, not in these disposable tombstones.
    ids = list(AgentAccountLease.objects.filter(license_key=license_key, lease_until__lt=now - timedelta(seconds=2 * TTL_SECONDS)).values_list('pk', flat=True)[:64])
    if ids:
        AgentAccountLease.objects.filter(pk__in=ids).delete()


def _one_operation(row, item, activation, data, now, until, counter, capacity, can_create=True):
    result = {'platform': item.platform, 'platform_account_id': item.platform_account_id,
              'local_account_id': item.local_account_id, 'action': item.action,
              'status': 'stale', 'fence_epoch': 0, 'lease_until_ms': 0}
    live = bool(row and not row.released and row.lease_until > now)
    owner = bool(row and row.owner_activation_id == activation.pk and row.owner_instance_id == data.instance_id
                 and row.owner_boot_id == data.boot_id and row.local_account_id == item.local_account_id)
    if item.action != 'acquire':
        if not live or not owner or row.fence_epoch != item.expected_epoch:
            return result, row, counter, capacity, False
        if item.action == 'release':
            row.released = True
            row.lease_until = now
            result.update(status='released', fence_epoch=row.fence_epoch)
            return result, row, counter, capacity + 1, True
    elif live and not owner:
        result['status'] = 'busy'
        return result, row, counter, capacity, False
    elif not live:
        if capacity <= 0 or (row is None and not can_create):
            result['status'] = 'quota'
            return result, row, counter, capacity, False
        if counter >= MAX_EPOCH:
            raise HttpError(503, '账号租约序号耗尽')
        counter += 1
        capacity -= 1
        if row is None:
            row = AgentAccountLease(license_key_id=activation.license_key_id,
                                    platform=item.platform, platform_account_id=item.platform_account_id)
        row.owner_activation = activation
        row.owner_instance_id = data.instance_id
        row.owner_boot_id = data.boot_id
        row.local_account_id = item.local_account_id
        row.fence_epoch = counter
    row.released = False
    row.lease_until = until
    result.update(status='owned', fence_epoch=row.fence_epoch, lease_until_ms=int(until.timestamp() * 1000))
    return result, row, counter, capacity, True


def sync_account_leases(data: AgentLeaseSyncIn):
    # Fail before any durable mutation when the deployment has no signing key.
    private_key = get_lease_private_key()
    if not private_key:
        raise HttpError(503, '账号租约签名未配置')
    hint = get_activation_by_token(str(data.activation_id), data.activation_token)
    return _sync_locked(data, hint.license_key_id, private_key)


@transaction.atomic
def _sync_locked(data, license_key_id, private_key):
    now = timezone.now()
    activation, license_key, until = _authorize(data, now, license_key_id)
    digest = _request_hash(data)
    state = AgentSyncState.objects.filter(activation=activation, instance_id=data.instance_id, boot_id=data.boot_id).first()
    if state and data.sequence <= state.sequence:
        if data.sequence == state.sequence and data.request_id == state.request_id and digest == state.request_hash:
            return _sign(state.response_claims, private_key)
        raise HttpError(409, '账号同步请求已过时或重放内容不一致')
    _prune(activation, license_key, now)
    if state is None and AgentSyncState.objects.filter(activation=activation).count() >= MAX_BOOT_RECORDS:
        raise HttpError(429, '客户端启动会话过多，请稍后重试')
    flags = license_key.plan.feature_flags or {}
    quota = flags.get('max_accounts', 512)
    if type(quota) is not int or not 1 <= quota <= 10000:
        raise HttpError(403, '账号配额配置无效')
    live_rows = AgentAccountLease.objects.filter(license_key=license_key, released=False, lease_until__gt=now)
    live_mapping = dict(live_rows.filter(owner_activation=activation, owner_instance_id=data.instance_id,
        owner_boot_id=data.boot_id).values_list('local_account_id', 'platform_account_id'))
    for item in data.accounts:
        if item.local_account_id in live_mapping and live_mapping[item.local_account_id] != item.platform_account_id:
            raise HttpError(409, '本地账号仍绑定其他平台身份，请先释放原租约')
    # Live quota and durable metadata are independent budgets: release frees only
    # the former; an existing expired row needs no additional metadata slot.
    capacity = max(0, min(quota, 512) - live_rows.count())
    available_rows = max(0, MAX_LEASE_ROWS - AgentAccountLease.objects.filter(license_key=license_key).count())
    rows = {(r.platform, r.platform_account_id): r for r in AgentAccountLease.objects.filter(
        license_key=license_key, platform_account_id__in=[a.platform_account_id for a in data.accounts])}
    counter = license_key.agent_fence_counter
    results, creates, updates = [], [], []
    for item in data.accounts:
        row = rows.get((item.platform, item.platform_account_id))
        exists = row is not None
        result, row, counter, capacity, changed = _one_operation(row, item, activation, data, now, until,
            counter, capacity, can_create=len(creates) < available_rows)
        results.append(result)
        if changed:
            (updates if exists else creates).append(row)
    if creates:
        AgentAccountLease.objects.bulk_create(creates)
    if updates:
        AgentAccountLease.objects.bulk_update(updates, ['owner_activation', 'owner_instance_id', 'owner_boot_id',
                                                       'local_account_id', 'fence_epoch', 'lease_until', 'released'])
    if counter != license_key.agent_fence_counter:
        license_key.agent_fence_counter = counter
        license_key.save(update_fields=['agent_fence_counter'])
    claims = {'iss': ISSUER, 'aud': AUDIENCE, 'sub': str(activation.pk), 'ver': 1,
              'instance_id': str(data.instance_id), 'boot_id': str(data.boot_id),
              'request_id': str(data.request_id), 'sequence': data.sequence,
              'iat': int(now.timestamp()), 'exp': (int(until.timestamp() * 1000) + 999) // 1000,
              'server_time_ms': int(now.timestamp() * 1000), 'results': results,
              'allow_manual': flags.get('manual_reply', True) is True, 'allow_auto': flags.get('auto_reply', False) is True}
    signed = _sign(claims, private_key)  # signing failures roll back the whole transaction
    AgentSyncState.objects.update_or_create(activation=activation, instance_id=data.instance_id, boot_id=data.boot_id,
        defaults={'sequence': data.sequence, 'request_id': data.request_id, 'request_hash': digest,
                  'response_claims': claims, 'updated_at': now})
    return signed
