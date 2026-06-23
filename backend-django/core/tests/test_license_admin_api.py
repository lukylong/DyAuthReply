from types import SimpleNamespace

import django
import os
from pathlib import Path

from django.test import TestCase

os.environ.setdefault("CLIENT_DATA_DIR", "/private/tmp/dyauthreply-test-client")
os.environ.setdefault("ZQ_ENV", "server")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "application.settings")
Path(os.environ["CLIENT_DATA_DIR"]).mkdir(parents=True, exist_ok=True)
django.setup()

from core.license import license_api
from core.license.license_model import LicensePlan, LicenseKey, LicenseActivation, ClientDevice
from core.license.license_schema import (
    LicensePlanIn,
    LicensePlanOut,
    LicensePlanPatch,
    LicenseKeyGenerateIn,
    LicenseRevokeIn,
    LicenseUnbindIn,
)
from core.license.license_service import activate_license
from core.license.license_service import get_license_key_detail_queryset


class LicenseAdminApiTests(TestCase):
    def setUp(self):
        self.request = SimpleNamespace(auth=None, META={"REMOTE_ADDR": "127.0.0.1"})

    def test_plan_create_patch_and_list(self):
        created = license_api.create_license_plan(
            self.request,
            LicensePlanIn.model_validate(
                {
                    "code": "pro",
                    "name": "专业版",
                    "description": "desc",
                    "feature_flags": {"auto_reply": True},
                    "max_devices": 2,
                    "valid_days": 365,
                    "heartbeat_interval_minutes": 15,
                    "grace_period_minutes": 120,
                    "is_active": True,
                }
            ),
        )
        self.assertEqual(created.code, "pro")
        out = LicensePlanOut.model_validate(created)
        self.assertIsInstance(out.id, str)

        updated = license_api.patch_license_plan(
            self.request,
            str(created.id),
            LicensePlanPatch.model_validate(
                {
                    "name": "专业版-2",
                    "max_devices": 3,
                    "is_active": False,
                }
            ),
        )
        self.assertEqual(updated.name, "专业版-2")
        self.assertEqual(updated.max_devices, 3)
        self.assertFalse(updated.is_active)

        rows = LicensePlan.objects.filter(is_deleted=False)
        self.assertEqual(rows.count(), 1)

    def test_key_generate_list_revoke_and_unbind(self):
        plan = LicensePlan.objects.create(
            code="basic",
            name="基础版",
            max_devices=1,
            valid_days=30,
            heartbeat_interval_minutes=15,
            grace_period_minutes=60,
            feature_flags={"auto_reply": True},
        )

        generated = license_api.generate_license_key_batch(
            self.request,
            LicenseKeyGenerateIn.model_validate(
                {
                    "plan_id": str(plan.id),
                    "count": 2,
                    "issued_to": "tester",
                    "batch_no": "B001",
                }
            ),
        )
        self.assertEqual(generated.count, 2)
        self.assertEqual(len(generated.items), 2)

        rows = get_license_key_detail_queryset().filter(is_deleted=False)
        self.assertEqual(rows.count(), 2)

        raw_code = generated.items[0].code
        activation = activate_license(
            license_code=raw_code,
            device_fingerprint="device-a",
            device_name="Device A",
            os_type="macos",
        )
        self.assertTrue(activation["activation_id"])

        license_key = LicenseKey.objects.get(id=generated.items[0].id)
        device = ClientDevice.objects.get(device_fingerprint="device-a")

        unbound = license_api.unbind_license_device(
            self.request,
            str(license_key.id),
            LicenseUnbindIn.model_validate(
                {
                    "client_device_id": str(device.id),
                    "reason": "管理员解绑测试",
                }
            ),
        )
        self.assertEqual(unbound.status, LicenseActivation.STATUS_DEACTIVATED)

        revoked = license_api.revoke_license(
            self.request,
            str(license_key.id),
            LicenseRevokeIn.model_validate({"reason": "后台撤销测试"}),
        )
        self.assertEqual(revoked.status, LicenseKey.STATUS_REVOKED)

        devices = ClientDevice.objects.filter(is_deleted=False)
        self.assertEqual(devices.count(), 1)

        activations = LicenseActivation.objects.filter(is_deleted=False)
        self.assertEqual(activations.count(), 1)

        events = list(license_api.list_license_events(self.request))
        self.assertGreaterEqual(len(events), 3)

        stats = license_api.license_stats(self.request)
        self.assertEqual(stats["plans_total"], 1)
        self.assertEqual(stats["keys_total"], 2)
        self.assertEqual(stats["devices_total"], 1)
