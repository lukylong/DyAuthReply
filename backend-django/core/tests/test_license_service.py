import base64
import os
from pathlib import Path

import django
from django.test import TestCase
from django.utils import timezone
from ninja.errors import HttpError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

os.environ.setdefault("CLIENT_DATA_DIR", "/private/tmp/dyauthreply-test-client")
os.environ.setdefault("ZQ_ENV", "server")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "application.settings")
Path(os.environ["CLIENT_DATA_DIR"]).mkdir(parents=True, exist_ok=True)
django.setup()

from core.license.license_model import LicensePlan, LicenseKey, LicenseActivation
from core.license.license_service import activate_license, check_in_activation, unbind_device, generate_license_code, hash_license_code


class LicenseServiceTests(TestCase):
    def setUp(self):
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
        self.plan = LicensePlan.objects.create(
            code="basic",
            name="基础版",
            max_devices=1,
            valid_days=30,
            heartbeat_interval_minutes=15,
            grace_period_minutes=60,
            feature_flags={"auto_reply": True},
        )
        self.code = generate_license_code()
        self.license_key = LicenseKey.objects.create(
            plan=self.plan,
            code_hash=hash_license_code(self.code),
            masked_code="TEST****CODE",
        )

    def tearDown(self):
        os.environ.pop("LICENSE_LEASE_PRIVATE_KEY_B64", None)
        os.environ.pop("LICENSE_LEASE_PUBLIC_KEY_B64", None)

    def test_activate_license_creates_activation_and_sets_expiry(self):
        result = activate_license(
            license_code=self.code,
            device_fingerprint="device-a",
            device_name="MacBook",
            os_type="macos",
            app_version="0.1.0",
        )

        self.license_key.refresh_from_db()
        activation = LicenseActivation.objects.get(id=result["activation_id"])

        self.assertEqual(result["status"], LicenseActivation.STATUS_ACTIVE)
        self.assertEqual(self.license_key.status, LicenseKey.STATUS_ACTIVE)
        self.assertIsNotNone(self.license_key.activated_at)
        self.assertIsNotNone(activation.last_valid_until)
        self.assertEqual(result["plan"]["max_devices"], 1)
        self.assertTrue(result["refresh_token"])
        self.assertTrue(result["lease_token"])
        self.assertEqual(result["lease_sequence"], 1)

    def test_activate_license_enforces_device_limit_until_unbound(self):
        activate_license(
            license_code=self.code,
            device_fingerprint="device-a",
            device_name="Machine A",
        )

        with self.assertRaises(HttpError) as exc:
            activate_license(
                license_code=self.code,
                device_fingerprint="device-b",
                device_name="Machine B",
            )
        self.assertEqual(exc.exception.status_code, 409)

        unbind_device(license_key=self.license_key, client_device_id=LicenseActivation.objects.get().client_device_id)

        result = activate_license(
            license_code=self.code,
            device_fingerprint="device-b",
            device_name="Machine B",
        )
        self.assertEqual(result["status"], LicenseActivation.STATUS_ACTIVE)

    def test_check_in_extends_last_valid_until(self):
        result = activate_license(
            license_code=self.code,
            device_fingerprint="device-a",
            device_name="Machine A",
        )
        activation = LicenseActivation.objects.get(id=result["activation_id"])
        previous_last_valid_until = activation.last_valid_until

        check_in_result = check_in_activation(
            activation_id=result["activation_id"],
            refresh_token=result["refresh_token"],
            app_version="0.1.1",
        )

        activation.refresh_from_db()
        self.assertGreaterEqual(activation.last_valid_until, previous_last_valid_until)
        self.assertEqual(check_in_result["status"], LicenseActivation.STATUS_ACTIVE)
        self.assertNotEqual(check_in_result["refresh_token"], result["refresh_token"])
        self.assertNotEqual(check_in_result["activation_token"], result["activation_token"])
        self.assertEqual(check_in_result["lease_sequence"], 2)

    def test_activate_license_rejects_expired_key(self):
        self.license_key.expires_at = timezone.now() - timezone.timedelta(minutes=1)
        self.license_key.save(update_fields=["expires_at"])

        with self.assertRaises(HttpError) as exc:
            activate_license(
                license_code=self.code,
                device_fingerprint="device-a",
            )
        self.assertEqual(exc.exception.status_code, 403)
