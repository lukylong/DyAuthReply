import json
import uuid
from django.test import TestCase
from ninja.errors import HttpError
from core.license.license_model import LicensePlan, LicenseKey, LicenseActivation
from core.license.license_service import activate_license, generate_license_code, hash_license_code, check_in_activation
from core.license.renewal_receipt import renew_once


class NativeRenewalReceiptTests(TestCase):
    def setUp(self):
        plan=LicensePlan.objects.create(code='native-receipt',name='Native',max_devices=1,valid_days=30,heartbeat_interval_minutes=30,grace_period_minutes=60)
        code=generate_license_code()
        key=LicenseKey.objects.create(plan=plan,code_hash=hash_license_code(code),masked_code='fixture',status=LicenseKey.STATUS_PENDING)
        self.initial=activate_license(license_code=code,device_fingerprint='native-test-device')
        self.request=dict(request_id=uuid.uuid4(),activation_id=self.initial['activation_id'],refresh_token=self.initial['refresh_token'],app_version='native-test',machine_meta={'runtime':'rust'})

    def test_lost_response_retry_returns_identical_tokens_without_rotating_again(self):
        first=renew_once(**self.request)
        second=renew_once(**self.request)
        for key in ['activation_token','refresh_token','lease_sequence','lease_token','request_id']:
            self.assertEqual(first[key],second[key])
        row=LicenseActivation.objects.get(id=first['activation_id'])
        self.assertEqual(row.lease_sequence,first['lease_sequence'])
        raw=json.dumps(row.renewal_receipt)
        self.assertNotIn(first['activation_token'],raw);self.assertNotIn(first['refresh_token'],raw)

    def test_same_nonce_changed_payload_and_different_nonce_old_token_rejected(self):
        renew_once(**self.request)
        with self.assertRaises(HttpError):renew_once(**{**self.request,'app_version':'different'})
        with self.assertRaises(HttpError):renew_once(**{**self.request,'request_id':uuid.uuid4()})

    def test_receipt_does_not_survive_new_rotation_or_revocation(self):
        first=renew_once(**self.request)
        check_in_activation(activation_id=first['activation_id'],refresh_token=first['refresh_token'])
        with self.assertRaises(HttpError):renew_once(**self.request)
        LicenseActivation.objects.filter(id=first['activation_id']).update(status=LicenseActivation.STATUS_REVOKED)
        with self.assertRaises(HttpError):renew_once(**self.request)
