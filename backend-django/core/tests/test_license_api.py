from types import SimpleNamespace

import django
import os
from pathlib import Path

from django.test import TestCase
from ninja.errors import HttpError

os.environ.setdefault("CLIENT_DATA_DIR", "/private/tmp/dyauthreply-test-client")
os.environ.setdefault("ZQ_ENV", "server")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "application.settings")
Path(os.environ["CLIENT_DATA_DIR"]).mkdir(parents=True, exist_ok=True)
django.setup()

from core.license.license_api import create_license_plan
from core.license.license_model import LicensePlan
from core.license.license_schema import LicensePlanIn


class LicenseApiTests(TestCase):
    def test_create_license_plan_returns_conflict_for_duplicate_code(self):
        LicensePlan.objects.create(
            code="00001",
            name="已有套餐",
            max_devices=1,
            valid_days=30,
            heartbeat_interval_minutes=15,
            grace_period_minutes=60,
        )

        request = SimpleNamespace(auth=None)
        payload = LicensePlanIn.model_validate(
            {
                "code": "00001",
                "name": "重复套餐",
                "max_devices": 1,
                "valid_days": 30,
                "heartbeat_interval_minutes": 15,
                "grace_period_minutes": 60,
                "feature_flags": {},
                "is_active": True,
            }
        )

        with self.assertRaises(HttpError) as exc:
            create_license_plan(request, payload)

        self.assertEqual(exc.exception.status_code, 409)
        self.assertEqual(str(exc.exception.message), "套餐编码已存在")
