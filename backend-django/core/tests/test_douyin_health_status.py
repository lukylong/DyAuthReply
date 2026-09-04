from django.test import TestCase

from core.douyin.douyin_account_model import DouyinAccount
from core.douyin.runtime.health import (
    _update_probe_inconclusive,
    _update_probe_ok,
)
from core.user.user_model import User


class DouyinCredentialProbeStatusTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(
            username='probe_status_owner',
            password='test_password',
            email='probe-status@example.com',
        )

    def test_read_only_probe_does_not_clear_confirmed_send_restriction(self):
        account = DouyinAccount.objects.create(
            nickname='发送封控账号',
            owner=self.owner,
            status=1,
            credential_state='receive_only',
            last_probe_error='账号发送封控（business=7911 raw_check=2）',
        )

        _update_probe_ok(str(account.id), has_send=True)

        account.refresh_from_db()
        self.assertEqual(account.credential_state, 'receive_only')
        self.assertIn('7911', account.last_probe_error)
        self.assertIsNotNone(account.last_probe_at)

    def test_inconclusive_probe_does_not_replace_send_restriction_reason(self):
        account = DouyinAccount.objects.create(
            nickname='发送封控账号',
            owner=self.owner,
            status=1,
            credential_state='receive_only',
            last_probe_error='账号发送封控（business=8610 raw_check=2）',
        )

        _update_probe_inconclusive(str(account.id), '临时网络异常')

        account.refresh_from_db()
        self.assertIn('8610', account.last_probe_error)
        self.assertNotIn('网络异常', account.last_probe_error)

    def test_read_only_probe_can_promote_unknown_when_send_material_exists(self):
        account = DouyinAccount.objects.create(
            nickname='新导入账号',
            owner=self.owner,
            status=1,
            credential_state='unknown',
            last_probe_error='等待首次探活',
        )

        _update_probe_ok(str(account.id), has_send=True)

        account.refresh_from_db()
        self.assertEqual(account.credential_state, 'sendable')
        self.assertIsNone(account.last_probe_error)

    def test_read_only_probe_clears_non_risk_receive_only_error(self):
        account = DouyinAccount.objects.create(
            nickname='补全发送凭证账号',
            owner=self.owner,
            status=1,
            credential_state='receive_only',
            last_probe_error='缺少 sdk_cert',
        )

        _update_probe_ok(str(account.id), has_send=True)

        account.refresh_from_db()
        self.assertEqual(account.credential_state, 'sendable')
        self.assertIsNone(account.last_probe_error)
