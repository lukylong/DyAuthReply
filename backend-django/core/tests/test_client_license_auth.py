import base64
import os
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import django
from django.utils import timezone
from ninja.errors import HttpError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from core.license.lease_token import build_lease_payload, issue_signed_lease

os.environ.setdefault('CLIENT_DATA_DIR', '/private/tmp/dyauthreply-test-client')
os.environ.setdefault('ZQ_ENV', 'server')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'application.settings')
Path(os.environ['CLIENT_DATA_DIR']).mkdir(parents=True, exist_ok=True)
django.setup()

from core.client.license_auth import (
    STATE_ACTIVE,
    STATE_EXPIRED,
    STATE_GRACE,
    STATE_INVALID,
    STATE_REVOKED,
    get_public_license_status,
    load_license_state,
    refresh_remote_license,
    require_client_business_access,
    save_license_state,
)


class ClientLicenseAuthTests(TestCase):
    def test_license_operation_lock_excludes_a_second_process(self):
        import subprocess
        import sys
        import time
        code = '''
import os,sys,time
from pathlib import Path
os.environ.setdefault('DJANGO_SETTINGS_MODULE','application.settings')
import django; django.setup()
from core.client.license_auth import _license_operation_lock
root=Path(sys.argv[1]); name=sys.argv[2]
(root/('attempt-'+name)).touch()
with _license_operation_lock():
    (root/('inside-'+name)).touch()
    if name=='a':
        while not (root/'release').exists(): time.sleep(0.01)
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = dict(os.environ, CLIENT_DATA_DIR=directory, ZQ_ENV='client',
                       PYTHONPATH=str(Path(__file__).resolve().parents[2]))
            children = []
            def wait_file(name):
                end = time.monotonic() + 10
                while not (root / name).exists() and time.monotonic() < end:
                    time.sleep(0.02)
                self.assertTrue((root / name).exists(), name)
            try:
                children.append(subprocess.Popen([sys.executable, '-c', code, directory, 'a'], env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE))
                wait_file('inside-a')
                children.append(subprocess.Popen([sys.executable, '-c', code, directory, 'b'], env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE))
                wait_file('attempt-b')
                time.sleep(0.2)
                self.assertFalse((root / 'inside-b').exists())
            finally:
                (root / 'release').touch()
                for child in children:
                    _, error = child.communicate(timeout=15)
                    self.assertEqual(child.returncode, 0, error.decode())
            self.assertTrue((root / 'inside-b').exists())

    def test_rotating_state_publish_is_private_atomic_and_removes_temporary_file(self):
        from core.client.license_auth import _safe_json_dump
        import json
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.license-state.json'
            path.write_text('{}')
            if os.name == 'posix':
                path.chmod(0o644)
            _safe_json_dump(path, {'activation_token': 'synthetic-new-token'})
            self.assertEqual(json.loads(path.read_text())['activation_token'], 'synthetic-new-token')
            self.assertEqual(list(Path(directory).iterdir()), [path])
            if os.name == 'posix':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix='dyauthreply-license-auth-')
        self.state_path = Path(self.temp_dir.name) / '.license-state.json'
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        os.environ["LICENSE_LEASE_PRIVATE_KEY_B64"] = base64.b64encode(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        ).decode("ascii")
        os.environ["LICENSE_LEASE_PUBLIC_KEY_B64"] = base64.b64encode(
            public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ).decode("ascii")

    def tearDown(self):
        os.environ.pop("LICENSE_LEASE_PRIVATE_KEY_B64", None)
        os.environ.pop("LICENSE_LEASE_PUBLIC_KEY_B64", None)
        self.temp_dir.cleanup()

    def _state_patch(self):
        return patch('core.client.license_auth._state_file_path', return_value=self.state_path)

    def _lease_token(self, *, activation_id: str, device_fingerprint: str, now, expires_at, grace_until, sequence: int = 1):
        payload = build_lease_payload(
            activation_id=activation_id,
            license_key_id="license-1",
            device_fingerprint=device_fingerprint,
            plan={"code": "basic"},
            feature_flags={"auto_reply": True},
            lease_sequence=sequence,
            now=now,
            lease_expires_at=expires_at,
            grace_until=grace_until,
        )
        return issue_signed_lease(payload)

    def test_refresh_remote_license_enters_grace_on_transient_failure_within_valid_window(self):
        now = timezone.now()
        with self._state_patch(), patch('core.client.license_auth._now', return_value=now):
            save_license_state(
                {
                    'local_state': STATE_ACTIVE,
                    'server_url': 'https://license.example.com',
                    'activation_id': 'act-1',
                    'activation_token': 'token-1',
                    'refresh_token': 'refresh-1',
                    'device_fingerprint': 'device-a',
                    'next_check_in_at': now - timedelta(minutes=1),
                    'last_valid_until': now + timedelta(minutes=10),
                    'lease_expires_at': now - timedelta(minutes=1),
                    'lease_token': self._lease_token(
                        activation_id='act-1',
                        device_fingerprint='device-a',
                        now=now - timedelta(minutes=30),
                        expires_at=now - timedelta(minutes=1),
                        grace_until=now + timedelta(minutes=10),
                    ),
                }
            )

            with patch(
                'core.client.license_auth._post_hosted_client_auth',
                side_effect=HttpError(503, '授权服务暂时不可达'),
            ):
                status = refresh_remote_license(force=True)
                persisted = load_license_state()

        self.assertEqual(status['state'], STATE_GRACE)
        self.assertTrue(status['can_use_business'])
        self.assertEqual(persisted['local_state'], STATE_GRACE)

    def test_refresh_remote_license_marks_revoked_when_server_reports_revocation(self):
        now = timezone.now()
        with self._state_patch(), patch('core.client.license_auth._now', return_value=now):
            save_license_state(
                {
                    'local_state': STATE_ACTIVE,
                    'server_url': 'https://license.example.com',
                    'activation_id': 'act-2',
                    'activation_token': 'token-2',
                    'refresh_token': 'refresh-2',
                    'device_fingerprint': 'device-a',
                    'next_check_in_at': now - timedelta(minutes=1),
                    'last_valid_until': now + timedelta(minutes=10),
                    'lease_expires_at': now + timedelta(minutes=5),
                    'lease_token': self._lease_token(
                        activation_id='act-2',
                        device_fingerprint='device-a',
                        now=now - timedelta(minutes=1),
                        expires_at=now + timedelta(minutes=5),
                        grace_until=now + timedelta(minutes=10),
                    ),
                }
            )

            with patch(
                'core.client.license_auth._post_hosted_client_auth',
                side_effect=HttpError(403, '当前授权已撤销'),
            ):
                status = refresh_remote_license(force=True)

        self.assertEqual(status['state'], STATE_REVOKED)
        self.assertFalse(status['can_use_business'])
        self.assertIn('撤销', status['last_error'])

    def test_refresh_remote_license_marks_invalid_for_bad_token(self):
        now = timezone.now()
        with self._state_patch(), patch('core.client.license_auth._now', return_value=now):
            save_license_state(
                {
                    'local_state': STATE_ACTIVE,
                    'server_url': 'https://license.example.com',
                    'activation_id': 'act-3',
                    'activation_token': 'token-3',
                    'refresh_token': 'refresh-3',
                    'device_fingerprint': 'device-a',
                    'next_check_in_at': now - timedelta(minutes=1),
                    'last_valid_until': now + timedelta(minutes=10),
                    'lease_expires_at': now + timedelta(minutes=5),
                    'lease_token': self._lease_token(
                        activation_id='act-3',
                        device_fingerprint='device-a',
                        now=now - timedelta(minutes=1),
                        expires_at=now + timedelta(minutes=5),
                        grace_until=now + timedelta(minutes=10),
                    ),
                }
            )

            with patch(
                'core.client.license_auth._post_hosted_client_auth',
                side_effect=HttpError(403, '令牌无效'),
            ):
                status = refresh_remote_license(force=True)

        self.assertEqual(status['state'], STATE_INVALID)
        self.assertFalse(status['can_use_business'])

    def test_public_status_marks_expired_after_local_valid_window_passes(self):
        now = timezone.now()
        with self._state_patch(), patch('core.client.license_auth._now', return_value=now):
            save_license_state(
                {
                    'local_state': STATE_ACTIVE,
                    'activation_id': 'act-4',
                    'device_fingerprint': 'device-a',
                    'last_valid_until': now - timedelta(minutes=1),
                    'lease_expires_at': now - timedelta(minutes=2),
                    'lease_token': self._lease_token(
                        activation_id='act-4',
                        device_fingerprint='device-a',
                        now=now - timedelta(minutes=20),
                        expires_at=now - timedelta(minutes=2),
                        grace_until=now - timedelta(minutes=1),
                    ),
                }
            )
            status = get_public_license_status()

        self.assertEqual(status['state'], STATE_EXPIRED)
        self.assertFalse(status['can_use_business'])

    def test_require_business_access_uses_local_lease_without_sync_check_in(self):
        now = timezone.now()
        with self._state_patch(), patch('core.client.license_auth._now', return_value=now):
            save_license_state(
                {
                    'local_state': STATE_ACTIVE,
                    'activation_id': 'act-5',
                    'device_fingerprint': 'device-a',
                    'lease_expires_at': now + timedelta(minutes=20),
                    'last_valid_until': now + timedelta(hours=2),
                    'lease_token': self._lease_token(
                        activation_id='act-5',
                        device_fingerprint='device-a',
                        now=now - timedelta(minutes=1),
                        expires_at=now + timedelta(minutes=20),
                        grace_until=now + timedelta(hours=2),
                    ),
                }
            )
            with patch('core.client.license_auth.refresh_remote_license') as refresh_mock, patch('env.ENV', 'client'):
                status = require_client_business_access('测试操作')

        self.assertEqual(status['state'], STATE_ACTIVE)
        refresh_mock.assert_not_called()

    def test_public_status_stays_active_without_lease_token(self):
        now = timezone.now()
        with self._state_patch(), patch('core.client.license_auth._now', return_value=now):
            save_license_state(
                {
                    'local_state': STATE_ACTIVE,
                    'activation_id': 'act-6',
                    'activation_status': 'active',
                    'device_fingerprint': 'device-a',
                    'lease_token': '',
                    'lease_expires_at': now + timedelta(minutes=20),
                    'last_valid_until': now + timedelta(hours=2),
                    'heartbeat_interval_minutes': 30,
                    'grace_period_minutes': 120,
                }
            )

            status = get_public_license_status()

        self.assertEqual(status['state'], STATE_ACTIVE)
        self.assertTrue(status['can_use_business'])
        self.assertEqual(status['last_error'], '')

    def test_public_status_recovers_from_stale_invalid_state_and_clears_error(self):
        now = timezone.now()
        with self._state_patch(), patch('core.client.license_auth._now', return_value=now):
            save_license_state(
                {
                    'local_state': STATE_INVALID,
                    'activation_id': 'act-7',
                    'activation_status': 'active',
                    'device_fingerprint': 'device-a',
                    'lease_token': '',
                    'lease_expires_at': now + timedelta(minutes=20),
                    'last_valid_until': now + timedelta(hours=2),
                    'heartbeat_interval_minutes': 30,
                    'grace_period_minutes': 120,
                    'last_error': 'missing_lease',
                }
            )

            status = get_public_license_status()
            persisted = load_license_state()

        self.assertEqual(status['state'], STATE_ACTIVE)
        self.assertEqual(status['last_error'], '')
        self.assertEqual(persisted['local_state'], STATE_ACTIVE)
        self.assertEqual(persisted['last_error'], '')
