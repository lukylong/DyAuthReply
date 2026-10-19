"""Native device labels upgrade without rebinding or losing renewal idempotency."""
import uuid
from django.test import TestCase
from ninja.errors import HttpError
from core.license.license_model import ClientDevice, LicenseActivation, LicenseKey, LicensePlan
from core.license.license_service import activate_license, generate_license_code, hash_license_code
from core.license.renewal_receipt import renew_once
from core.license.diagnostic_model import ClientDiagnosticState
from core.license.diagnostic_service import list_devices


class NativeDeviceNameTests(TestCase):
    def setUp(self):
        plan = LicensePlan.objects.create(code='device-names', name='Fixture', max_devices=1, valid_days=30)
        code = generate_license_code()
        LicenseKey.objects.create(plan=plan, code_hash=hash_license_code(code), masked_code='FIXTURE')
        self.credentials = activate_license(license_code=code, device_fingerprint='unchanged-device', device_name='本地设备')
        self.activation = LicenseActivation.objects.select_related('client_device').get(id=self.credentials['activation_id'])
        self.device = self.activation.client_device

    def request(self, meta):
        return dict(request_id=uuid.uuid4(), activation_id=self.credentials['activation_id'],
                    refresh_token=self.credentials['refresh_token'], app_version='next', machine_meta=meta)

    def renew(self, meta):
        self.credentials = renew_once(**self.request(meta))
        self.device.refresh_from_db()

    def test_upgrade_updates_placeholder_and_diagnostic_name_without_rebinding(self):
        before = self.device.id
        ClientDiagnosticState.objects.create(device=self.device, activation=self.activation)
        self.renew({'runtime': 'rust', 'device_name': 'DESKTOP-DEMO', 'device_name_source': 'system'})
        self.assertEqual(self.device.device_name, 'DESKTOP-DEMO')
        self.assertEqual(self.device.id, before)
        self.assertEqual(self.device.device_fingerprint, 'unchanged-device')
        self.assertEqual(ClientDevice.objects.count(), 1)
        self.assertEqual(self.credentials['activation_id'], str(self.activation.id))
        self.assertEqual(list_devices()[0]['device_name'], 'DESKTOP-DEMO')

    def test_custom_admin_name_is_preserved(self):
        self.device.device_name = '客服电脑A'
        self.device.save(update_fields=['device_name'])
        for name in ['DESKTOP-A', 'DESKTOP-B']:
            self.renew({'runtime': 'rust', 'device_name': name})
            self.assertEqual(self.device.device_name, '客服电脑A')

    def test_auto_name_tracks_computer_rename_and_old_clients_do_not_blank_it(self):
        for name in ['DESKTOP-A', 'DESKTOP-B']:
            self.renew({'runtime': 'rust', 'device_name': name})
            self.assertEqual(self.device.device_name, name)
        self.renew({'runtime': 'rust'})
        self.assertEqual(self.device.device_name, 'DESKTOP-B')

    def test_invalid_names_do_not_replace_existing_value_and_names_are_bounded(self):
        for name in [None, {}, [], 123, '', '  ', '本地设备', '\n\x00']:
            self.renew({'runtime': 'rust', 'device_name': name})
            self.assertEqual(self.device.device_name, '本地设备')
        self.renew({'runtime': 'rust', 'device_name': '  客服\n' + '机' * 200})
        self.assertEqual(len(self.device.device_name), 128)
        self.assertNotIn('\n', self.device.device_name)

    def test_metadata_retry_is_idempotent_and_changed_payload_is_rejected(self):
        request = self.request({'runtime': 'rust', 'device_name': 'DESKTOP-A'})
        first = renew_once(**request)
        second = renew_once(**request)
        self.assertEqual(first['lease_sequence'], second['lease_sequence'])
        self.assertEqual(first['activation_token'], second['activation_token'])
        with self.assertRaises(HttpError) as error:
            renew_once(**{**request, 'machine_meta': {'device_name': 'DESKTOP-B'}})
        self.assertEqual(error.exception.status_code, 409)
        self.device.refresh_from_db()
        self.assertEqual(self.device.device_name, 'DESKTOP-A')
