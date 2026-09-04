from django.test import TestCase

from core.douyin.douyin_account_model import DouyinAccount
from core.websocket.consumers import _account_state_revision
from core.user.user_model import User


class DouyinClientRealtimeRevisionTests(TestCase):
    def test_account_revision_changes_only_for_visible_account_updates(self):
        owner = User.objects.create(
            username='realtime_owner',
            password='test_password',
            email='realtime@example.com',
        )
        account = DouyinAccount.objects.create(
            nickname='账号A',
            owner=owner,
            status=1,
            credential_state='sendable',
        )
        initial = _account_state_revision()

        account.auto_reply_enabled = False
        account.save(update_fields=['auto_reply_enabled', 'sys_update_datetime'])
        changed = _account_state_revision()

        self.assertNotEqual(initial, changed)

        account.status = 3
        account.save(update_fields=['status', 'sys_update_datetime'])
        hidden = _account_state_revision()
        self.assertTrue(hidden.startswith('0:'))
