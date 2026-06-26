from django.test import SimpleTestCase, TestCase

from core.douyin.douyin_rule_api import (
    _build_quick_enable_rule_payload,
    _normalize_card_ids,
    _normalize_rule_payload,
    _validate_cards_exist,
)
from core.douyin.douyin_rule_schema import DouyinRuleQuickEnableIn, DouyinRuleSchemaIn
from ninja.errors import HttpError


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


class DouyinRuleCardIdsTests(TestCase):
    """规则关联卡片 card_ids 的归一化与存在性校验"""

    def test_schema_in_accepts_card_ids(self):
        data = DouyinRuleSchemaIn(
            name='带卡片', match_type='default', keywords=[], reply_text='你好',
            links=[], send_mode='multi_message', priority=0, status=True,
            cooldown_seconds=60, channel='dm', weekday_mask='1111111',
            card_ids=['id-1', 'id-2'],
        )
        self.assertEqual(data.card_ids, ['id-1', 'id-2'])

    def test_normalize_card_ids_dedupe_and_order(self):
        self.assertEqual(_normalize_card_ids(['b', 'a', 'b', '  ', 'c']), ['b', 'a', 'c'])

    def test_validate_cards_exist_rejects_missing(self):
        import uuid
        with self.assertRaises(HttpError):
            _validate_cards_exist([str(uuid.uuid4())])

    def test_create_rule_persists_card_ids(self):
        from core.douyin.douyin_card_model import DouyinCard
        from core.douyin.douyin_rule_model import DouyinRule

        c = DouyinCard.objects.create(title='卡', target_url='https://a.com', status=True)
        rule = DouyinRule.objects.create(
            name='r', match_type='default', card_ids=[str(c.id)],
        )
        rule.refresh_from_db()
        self.assertEqual(rule.card_ids, [str(c.id)])
