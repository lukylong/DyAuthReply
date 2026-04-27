from django.test import SimpleTestCase

from core.douyin.douyin_account_model import DouyinAccount


class DouyinAccountModelTests(SimpleTestCase):
    def test_can_reply_requires_online_and_auto_reply_enabled(self):
        self.assertTrue(DouyinAccount(status=1, auto_reply_enabled=True).can_reply())
        self.assertFalse(DouyinAccount(status=0, auto_reply_enabled=True).can_reply())
        self.assertFalse(DouyinAccount(status=1, auto_reply_enabled=False).can_reply())
