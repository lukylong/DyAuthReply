from django.test import SimpleTestCase

from core.douyin.douyin_rule_api import _build_quick_enable_rule_payload
from core.douyin.douyin_rule_schema import DouyinRuleQuickEnableIn


class DouyinRuleApiTests(SimpleTestCase):
    def test_quick_enable_payload_uses_contains_when_keywords_present(self):
        data = DouyinRuleQuickEnableIn(
            account_id='acc-1',
            reply_text='你好',
            keywords=['  发货  ', '', '物流'],
            cooldown_seconds=300,
            send_mode='merged',
        )

        payload = _build_quick_enable_rule_payload(data)

        self.assertEqual(payload['match_type'], 'contains')
        self.assertEqual(payload['keywords'], ['发货', '物流'])
        self.assertEqual(payload['name'], '关键词自动回复（快捷）')

    def test_quick_enable_payload_defaults_to_fallback_without_keywords(self):
        data = DouyinRuleQuickEnableIn(
            account_id='acc-1',
            reply_text='你好',
            cooldown_seconds=300,
            send_mode='merged',
        )

        payload = _build_quick_enable_rule_payload(data)

        self.assertEqual(payload['match_type'], 'default')
        self.assertEqual(payload['keywords'], [])
        self.assertEqual(payload['name'], '陌生人自动回复（快捷）')
