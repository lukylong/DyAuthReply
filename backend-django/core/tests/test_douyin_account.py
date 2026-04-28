from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from core.douyin.douyin_account_model import DouyinAccount


class DouyinAccountModelTests(SimpleTestCase):
    def test_can_reply_requires_online_and_auto_reply_enabled(self):
        self.assertTrue(DouyinAccount(status=1, auto_reply_enabled=True).can_reply())
        self.assertFalse(DouyinAccount(status=0, auto_reply_enabled=True).can_reply())
        self.assertFalse(DouyinAccount(status=1, auto_reply_enabled=False).can_reply())


class DouyinAccountLogoutApiTests(SimpleTestCase):
    """trigger_logout 必须连同 sec_uid 一起清掉，否则 inbox 的身份核对会因
    DB 里残留的旧 sec_uid 把"用户重新扫码登录的新账号"误判为"身份漂移"，
    把账号又立刻强制下线（下线-登录死循环）。
    """

    def test_logout_clears_sec_uid_to_avoid_identity_check_loop(self):
        from core.douyin import douyin_account_api

        account = MagicMock()
        account.id = "acc-1"
        account.nickname = "测试号"
        account.status = 1
        account.storage_state_path = "douyin/storage/acc-1.bin"
        account.sec_uid = "MS4wLjABAAAA_old_sec_uid_xxxxxxxx"

        captured: dict = {}

        def _save(update_fields=None):
            captured["update_fields"] = list(update_fields or [])

        account.save = _save

        with patch.object(douyin_account_api, "get_object_or_404", return_value=account), \
             patch.object(douyin_account_api.command_publisher, "send_logout") as send_logout:
            request = MagicMock()
            douyin_account_api.trigger_logout(request, "acc-1")

        self.assertIn("sec_uid", captured["update_fields"])
        self.assertEqual(account.sec_uid, "")
        self.assertEqual(account.status, 0)
        self.assertEqual(account.storage_state_path, "")
        send_logout.assert_called_once_with("acc-1")
