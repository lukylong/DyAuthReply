from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from ninja.errors import HttpError

from core.douyin.douyin_account_api import delete_douyin_account, batch_delete_douyin_account
from core.douyin.douyin_account_model import DouyinAccount
from core.douyin.douyin_account_schema import DouyinAccountBatchDeleteIn
from core.douyin.douyin_rule_model import DouyinRule
from core.user.user_model import User


class AccountDeleteRuleGuardTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(username='delete-guard-owner', password='not-a-real-password')
        self.account = DouyinAccount.objects.create(owner=self.owner, nickname='待删除测试号', auto_reply_enabled=False)
        self.request = SimpleNamespace()

    def test_disabled_explicit_rule_blocks_before_any_side_effect(self):
        rule = DouyinRule.objects.create(name='已停用但仍绑定', status=False, account_ids=[str(self.account.id)])
        with patch('core.douyin.douyin_account_api.command_publisher.send_session_control') as stop, patch('core.douyin.runtime.storage.delete_account_runtime_state') as cleanup:
            with self.assertRaises(HttpError) as raised:
                delete_douyin_account(self.request, str(self.account.id))
            self.assertIn(rule.name, str(raised.exception))
            stop.assert_not_called()
            cleanup.assert_not_called()
        self.assertTrue(DouyinAccount.objects.filter(id=self.account.id).exists())
        rule.refresh_from_db()
        self.assertEqual(rule.account_ids, [str(self.account.id)])

    def test_legacy_binding_also_blocks(self):
        DouyinRule.objects.create(name='旧单账号规则', account=self.account, status=False)
        with self.assertRaises(HttpError):
            delete_douyin_account(self.request, str(self.account.id))

    def test_unbound_and_global_rules_do_not_block(self):
        rule = DouyinRule.objects.create(name='已经解绑', account_ids=[], status=False)
        global_rule = DouyinRule.objects.create(name='全局规则', account_ids=[], status=True)
        aid = str(self.account.id)
        with patch('core.douyin.douyin_account_api.command_publisher.send_session_control'), patch('core.douyin.runtime.storage.delete_account_runtime_state') as cleanup:
            delete_douyin_account(self.request, aid)
            cleanup.assert_called_once_with(aid)
        self.assertFalse(DouyinAccount.objects.filter(id=aid).exists())
        self.assertTrue(DouyinRule.objects.filter(id__in=[rule.id, global_rule.id]).count() == 2)

    def test_batch_reports_bound_account_and_deletes_only_unbound_one(self):
        other = DouyinAccount.objects.create(owner=self.owner, nickname='无绑定', auto_reply_enabled=False)
        bound_id, free_id = str(self.account.id), str(other.id)
        DouyinRule.objects.create(name='绑定规则', account_ids=[bound_id])
        with patch('core.douyin.douyin_account_api.command_publisher.send_session_control') as stop, patch('core.douyin.runtime.storage.delete_account_runtime_state') as cleanup:
            result = batch_delete_douyin_account(self.request, DouyinAccountBatchDeleteIn(ids=[bound_id, free_id]))
            self.assertEqual(result.count, 1)
            self.assertEqual(result.failed_ids, [bound_id])
            stop.assert_called_once_with(free_id, 'stop')
            cleanup.assert_called_once_with(free_id)
        self.assertTrue(DouyinAccount.objects.filter(id=bound_id).exists())

    def test_binding_validation_rejects_a_deleted_account(self):
        from core.douyin.douyin_rule_api import _validate_accounts_exist
        aid = str(self.account.id)
        self.account.delete()
        with self.assertRaises(HttpError):
            _validate_accounts_exist([aid])
