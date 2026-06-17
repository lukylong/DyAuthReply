from django.test import SimpleTestCase

from core.douyin.douyin_rule_api import (
    _build_quick_enable_rule_payload,
    _normalize_rule_payload,
)
from core.douyin.douyin_rule_schema import DouyinRuleQuickEnableIn, DouyinRuleSchemaIn


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


class DouyinRuleNormalizePayloadTests(SimpleTestCase):
    """全局规则相关：account_id 可空 / 空串归一化为 None"""

    def test_normalize_keeps_account_id_when_provided(self):
        out = _normalize_rule_payload({'account_id': 'acc-1', 'template_id': 'tpl-1'})
        self.assertEqual(out['account_id'], 'acc-1')
        self.assertEqual(out['template_id'], 'tpl-1')

    def test_normalize_treats_empty_string_account_id_as_none(self):
        out = _normalize_rule_payload({'account_id': '', 'template_id': ''})
        self.assertIsNone(out['account_id'])
        self.assertIsNone(out['template_id'])

    def test_normalize_keeps_none_account_id_for_global_rule(self):
        out = _normalize_rule_payload({'account_id': None})
        self.assertIsNone(out['account_id'])

    def test_normalize_forces_multi_message_when_links_present(self):
        out = _normalize_rule_payload({
            'links': [{'title': '名片', 'url': 'https://example.com'}],
            'send_mode': 'merged',
        })
        self.assertEqual(out['send_mode'], 'multi_message')

    def test_schema_in_accepts_global_rule_without_account_id(self):
        """Schema 层应允许 account_id 缺省（全局规则）"""
        data = DouyinRuleSchemaIn(
            name='全局兜底',
            match_type='default',
            keywords=[],
            reply_text='你好',
            links=[],
            send_mode='merged',
            priority=0,
            status=True,
            cooldown_seconds=60,
            channel='dm',
            weekday_mask='1111111',
        )
        self.assertIsNone(data.account_id)
        self.assertIsNone(data.template_id)
