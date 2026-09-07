"""Real ORM/HTTP lease-authority gates, isolated from user accounts."""
import os
import json
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import django
from django.test import TestCase
from django.utils import timezone
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import jwt
from ninja.errors import HttpError
from ninja.testing import TestClient

os.environ.setdefault('ZQ_ENV', 'client')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'application.settings')
django.setup()

from core.license.license_model import LicensePlan, LicenseKey, LicenseActivation, AgentAccountLease, AgentSyncState
from core.license.license_service import activate_license, generate_license_code, hash_license_code
from core.license.agent_lease_schema import AgentLeaseSyncIn
from core.license.agent_lease_service import sync_account_leases, ISSUER, AUDIENCE
from core.license.client_auth_api import router


class AgentLeaseTests(TestCase):
    def setUp(self):
        key = Ed25519PrivateKey.generate()
        self.public = key.public_key()
        private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        self.environment = patch.dict(os.environ, {'LICENSE_LEASE_PRIVATE_KEY': private})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        plan = LicensePlan.objects.create(code='agent', name='Agent', max_devices=2, valid_days=30,
                                          feature_flags={'auto_reply': True, 'max_accounts': 300})
        code = generate_license_code()
        self.license = LicenseKey.objects.create(plan=plan, code_hash=hash_license_code(code), masked_code='TEST')
        self.activation = activate_license(license_code=code, device_fingerprint='device-a')
        self.peer = activate_license(license_code=code, device_fingerprint='device-b')
        self.instance, self.boot = uuid4(), uuid4()

    def request(self, sequence=1, action='acquire', epoch=0, activation=None, instance=None, local='local-a', uid='platform-user'):
        auth = activation or self.activation
        return AgentLeaseSyncIn(activation_id=auth['activation_id'], activation_token=auth['activation_token'],
            instance_id=instance or self.instance, boot_id=self.boot, request_id=uuid4(), sequence=sequence,
            accounts=[{'platform': 'douyin', 'platform_account_id': uid, 'local_account_id': local, 'action': action, 'expected_epoch': epoch}])

    def decode(self, response):
        return jwt.decode(response['token'], self.public, algorithms=['EdDSA'], issuer=ISSUER, audience=AUDIENCE)

    def test_batch_http_grant_is_signed_and_bound(self):
        data = self.request()
        response = TestClient(router).post('/agent/leases/sync', json=data.model_dump(mode='json'))
        self.assertEqual(response.status_code, 200, response.content)
        payload = self.decode(response.json())
        self.assertEqual(payload['sub'], self.activation['activation_id'])
        self.assertEqual(payload['instance_id'], str(self.instance))
        self.assertEqual(payload['request_id'], str(data.request_id))
        self.assertEqual(payload['results'][0]['status'], 'owned')
        self.assertGreater(payload['results'][0]['fence_epoch'], 0)
        self.assertEqual(AgentAccountLease.objects.count(), 1)
        self.assertNotIn('activation_token', payload)

    def test_same_sequence_replay_never_extends_ttl(self):
        data = self.request()
        original = sync_account_leases(data)
        until = AgentAccountLease.objects.get().lease_until
        self.assertEqual(original, sync_account_leases(data))
        self.assertEqual(AgentAccountLease.objects.get().lease_until, until)
        self.assertEqual(AgentSyncState.objects.count(), 1)
        with self.assertRaises(HttpError) as error:
            sync_account_leases(self.request())
        self.assertEqual(error.exception.status_code, 409)

    def test_other_activation_or_local_alias_cannot_share_live_lease(self):
        epoch = self.decode(sync_account_leases(self.request()))['results'][0]['fence_epoch']
        denied = self.decode(sync_account_leases(self.request(activation=self.peer, instance=uuid4())))
        self.assertEqual(denied['results'][0]['status'], 'busy')
        denied = self.decode(sync_account_leases(self.request(sequence=2, local='different-local-id')))
        self.assertEqual(denied['results'][0]['status'], 'busy')
        self.assertEqual(AgentAccountLease.objects.get().fence_epoch, epoch)

    def test_jsonb_style_key_reordering_preserves_exact_replayed_token(self):
        request = self.request()
        original = sync_account_leases(request)
        state = AgentSyncState.objects.get()
        state.response_claims = json.loads(json.dumps(state.response_claims, sort_keys=True))
        state.save(update_fields=['response_claims'])
        self.assertEqual(original, sync_account_leases(request))

    def test_renew_release_and_transfer_fence_old_owner(self):
        first = self.decode(sync_account_leases(self.request()))['results'][0]
        epoch = first['fence_epoch']
        renewed = self.decode(sync_account_leases(self.request(2, 'renew', epoch)))['results'][0]
        self.assertEqual(renewed['fence_epoch'], epoch)
        released = self.decode(sync_account_leases(self.request(3, 'release', epoch)))['results'][0]
        self.assertEqual(released['status'], 'released')
        new = self.decode(sync_account_leases(self.request(activation=self.peer, instance=uuid4())))['results'][0]
        self.assertGreater(new['fence_epoch'], epoch)
        stale = self.decode(sync_account_leases(self.request(4, 'renew', epoch)))['results'][0]
        self.assertEqual(stale['status'], 'stale')

    def test_pruning_does_not_reset_epoch(self):
        first = self.decode(sync_account_leases(self.request()))['results'][0]['fence_epoch']
        AgentAccountLease.objects.update(lease_until=timezone.now() - timedelta(days=2))
        new = self.decode(sync_account_leases(self.request(2)))['results'][0]['fence_epoch']
        self.assertGreater(new, first)
        self.assertEqual(AgentAccountLease.objects.count(), 1)

    def test_revoked_activation_and_expired_license_lease_are_rejected(self):
        activation = LicenseActivation.objects.get(id=self.activation['activation_id'])
        activation.status = LicenseActivation.STATUS_REVOKED
        activation.save(update_fields=['status'])
        with self.assertRaises(HttpError) as error:
            sync_account_leases(self.request())
        self.assertEqual(error.exception.status_code, 403)
        activation.status = LicenseActivation.STATUS_ACTIVE
        activation.lease_expires_at = timezone.now() - timedelta(seconds=1)
        activation.save(update_fields=['status', 'lease_expires_at'])
        with self.assertRaises(HttpError):
            sync_account_leases(self.request())
        self.assertEqual(AgentAccountLease.objects.count(), 0)

    def test_no_signing_key_fails_without_grant(self):
        with patch('core.license.agent_lease_service.get_lease_private_key', return_value=''):
            with self.assertRaises(HttpError) as error:
                sync_account_leases(self.request())
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(AgentAccountLease.objects.count(), 0)

    def test_duplicate_input_and_batch_limit_are_schema_errors(self):
        data = self.request().model_dump(mode='json')
        data['accounts'] *= 2
        response = TestClient(router).post('/agent/leases/sync', json=data)
        self.assertEqual(response.status_code, 422)
        data['accounts'] *= 151
        response = TestClient(router).post('/agent/leases/sync', json=data)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(AgentAccountLease.objects.count(), 0)

    def test_signing_failure_rolls_back_counter_rows_and_sequence(self):
        with patch('core.license.agent_lease_service._sign', side_effect=ValueError('synthetic signing failure')):
            with self.assertRaises(ValueError):
                sync_account_leases(self.request())
        self.license.refresh_from_db()
        self.assertEqual(self.license.agent_fence_counter, 0)
        self.assertEqual(AgentAccountLease.objects.count(), 0)
        self.assertEqual(AgentSyncState.objects.count(), 0)

    def test_live_local_mapping_cannot_switch_platform_identity_between_batches(self):
        sync_account_leases(self.request())
        with self.assertRaises(HttpError) as error:
            sync_account_leases(self.request(sequence=2, uid='different-platform-user'))
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(AgentAccountLease.objects.count(), 1)
        self.assertEqual(AgentSyncState.objects.get().sequence, 1)

    def test_release_does_not_free_metadata_capacity_in_same_batch(self):
        first = self.decode(sync_account_leases(self.request()))['results'][0]
        request = self.request(sequence=2, action='release', epoch=first['fence_epoch']).model_dump(mode='json')
        request['accounts'].append(self.request(uid='second-user', local='local-b').model_dump(mode='json')['accounts'][0])
        with patch('core.license.agent_lease_service.MAX_LEASE_ROWS', 1):
            result = self.decode(sync_account_leases(AgentLeaseSyncIn(**request)))
        self.assertEqual([item['status'] for item in result['results']], ['released', 'quota'])
        self.assertEqual(AgentAccountLease.objects.count(), 1)

    def test_existing_expired_row_reacquires_without_new_metadata_capacity(self):
        first = self.decode(sync_account_leases(self.request()))['results'][0]
        AgentAccountLease.objects.update(lease_until=timezone.now() - timedelta(seconds=1))
        with patch('core.license.agent_lease_service.MAX_LEASE_ROWS', 1):
            result = self.decode(sync_account_leases(self.request(sequence=2)))['results'][0]
        self.assertEqual(result['status'], 'owned')
        self.assertGreater(result['fence_epoch'], first['fence_epoch'])

    def test_three_hundred_accounts_use_one_bounded_signed_batch(self):
        request = self.request().model_dump(mode='json')
        request['accounts'] = [dict(platform='douyin', platform_account_id=f'user-{n}',
            local_account_id=f'local-{n}', action='acquire', expected_epoch=0) for n in range(300)]
        response = sync_account_leases(AgentLeaseSyncIn(**request))
        result = self.decode(response)
        self.assertEqual(len(result['results']), 300)
        self.assertTrue(all(item['status'] == 'owned' for item in result['results']))
        self.assertLess(len(response['token']), 512 * 1024)
        self.assertEqual(AgentSyncState.objects.count(), 1)
        self.assertEqual(AgentAccountLease.objects.count(), 300)

    def test_quota_and_bounded_latest_response(self):
        self.license.plan.feature_flags = {'max_accounts': 1}
        self.license.plan.save(update_fields=['feature_flags'])
        sync_account_leases(self.request())
        for sequence in range(2, 12):
            result = self.decode(sync_account_leases(self.request(sequence=sequence, uid='second-user', local='local-b')))
            self.assertEqual(result['results'][0]['status'], 'quota')
        self.assertEqual(AgentSyncState.objects.count(), 1)
        self.assertEqual(AgentAccountLease.objects.count(), 1)


from django.test import TransactionTestCase
from django.db import close_old_connections, OperationalError
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import time


class AgentLeaseConcurrencyTests(TransactionTestCase):
    setUp = AgentLeaseTests.setUp
    request = AgentLeaseTests.request
    decode = AgentLeaseTests.decode

    def test_concurrent_checkins_cannot_publish_two_tokens_for_the_same_sequence(self):
        from core.license.license_service import check_in_activation, hash_activation_token, get_activation_by_refresh_token
        from threading import local
        from django.db import connection
        barrier = Barrier(2)
        start_gate = Barrier(2)
        calls = local()
        def synchronized_lookup(*args, **kwargs):
            result = get_activation_by_refresh_token(*args, **kwargs)
            calls.count = getattr(calls, 'count', 0) + 1
            # PostgreSQL can keep both old reads alive while waiting for a row
            # lock. SQLite shared-cache readers instead block a writer upgrade;
            # its retry path is exercised without forcing that artificial wait.
            if calls.count == 1 and connection.vendor != 'sqlite':
                barrier.wait(timeout=5)
            return result
        def attempt(_):
            close_old_connections()
            try:
                start_gate.wait(timeout=5)
                for index in range(20):
                    try:
                        response = check_in_activation(activation_id=self.activation['activation_id'],
                            refresh_token=self.activation['refresh_token'])
                        return ('ok', hash_activation_token(response['activation_token']))
                    except OperationalError:
                        time.sleep(0.005 * (index + 1))
                    except HttpError as error:
                        return (error.status_code, None)
                return ('retry_exhausted', None)
            finally:
                close_old_connections()
        with patch('core.license.license_service.get_activation_by_refresh_token', side_effect=synchronized_lookup):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(attempt, range(2)))
        accepted = [value for status, value in results if status == 'ok']
        self.assertEqual(len(accepted), 1, [status for status, _ in results])
        self.assertEqual([status for status, _ in results].count(401), 1)
        activation = LicenseActivation.objects.get(pk=self.activation['activation_id'])
        self.assertEqual(activation.activation_token_hash, accepted[0])

    def test_simultaneous_owners_never_both_receive_live_grants(self):
        barrier = Barrier(2)
        requests = [self.request(), self.request(activation=self.peer, instance=uuid4())]
        def attempt(data):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                # SQLite may reject both first attempts while their authorization
                # reads overlap a writer. Exercise the specified same-request
                # retry contract rather than assuming one attempt always wins.
                for attempt_number in range(20):
                    try:
                        return self.decode(sync_account_leases(data))['results'][0]['status']
                    except OperationalError:
                        time.sleep(0.005 * (attempt_number + 1))
                return 'retry_exhausted'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, requests))
        self.assertEqual(results.count('owned'), 1, results)
        self.assertEqual(results.count('busy'), 1, results)
        self.assertEqual(AgentAccountLease.objects.filter(released=False, lease_until__gt=timezone.now()).count(), 1)
