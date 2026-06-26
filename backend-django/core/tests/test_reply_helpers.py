from types import SimpleNamespace
from unittest import TestCase

from django.test import TestCase as DjangoTestCase

from core.douyin.runtime.reply_helpers import (
    _build_segments,
    _normalize_send_mode,
    render_template,
    resolve_card_landing_urls,
)


class ReplyHelpersTests(TestCase):
    def test_multi_message_splits_text_and_links(self):
        rule = SimpleNamespace(
            reply_text='你好 {{nickname}}',
            links=[{'title': '名片', 'url': 'https://example.com/card'}],
            send_mode='merged',
            template=None,
        )
        segments = _build_segments(rule, '小明')
        self.assertEqual(segments, ['你好 小明', 'https://example.com/card'])

    def test_merged_only_when_no_links(self):
        rule = SimpleNamespace(
            reply_text='第一行',
            links=[],
            send_mode='merged',
            template=None,
        )
        segments = _build_segments(rule, '')
        self.assertEqual(segments, ['第一行'])

    def test_normalize_send_mode_forces_multi_with_links(self):
        self.assertEqual(
            _normalize_send_mode('merged', has_links=True),
            'multi_message',
        )

    def test_link_title_not_sent_as_message_body(self):
        rule = SimpleNamespace(
            reply_text='',
            links=[{'title': '我的名片', 'url': 'https://example.com/x'}],
            send_mode='multi_message',
            template=None,
        )
        segments = _build_segments(rule, '')
        self.assertEqual(segments, ['https://example.com/x'])

    def test_template_links_used_when_rule_links_empty(self):
        template = SimpleNamespace(
            content='欢迎 {{nickname}}',
            links=[{'title': '', 'url': 'https://example.com/tpl'}],
            send_mode='merged',
        )
        rule = SimpleNamespace(
            reply_text='',
            links=[],
            send_mode='',
            template=template,
        )
        segments = _build_segments(rule, '访客')
        self.assertEqual(segments, ['欢迎 访客', 'https://example.com/tpl'])

    def test_render_template_link_title_placeholder(self):
        text = render_template(
            '点这里：{{link_1_title}}',
            links=[{'title': '名片', 'url': 'https://example.com'}],
        )
        self.assertEqual(text, '点这里：名片')


class CardSegmentTests(TestCase):
    """卡片落地页 URL 作为独立段注入（card_urls 由调用方预取传入）"""

    def _rule(self, **kw):
        base = dict(reply_text='', links=[], send_mode='multi_message', template=None)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_text_then_cards_then_links_order(self):
        rule = self._rule(
            reply_text='你好',
            links=[{'title': '', 'url': 'https://link.example/x'}],
        )
        segs = _build_segments(
            rule, '小明',
            card_urls=['https://site/c/aaa', 'https://site/c/bbb'],
        )
        self.assertEqual(segs, [
            '你好',
            'https://site/c/aaa',
            'https://site/c/bbb',
            'https://link.example/x',
        ])

    def test_cards_only_no_text(self):
        rule = self._rule(reply_text='')
        segs = _build_segments(rule, '', card_urls=['https://site/c/aaa'])
        self.assertEqual(segs, ['https://site/c/aaa'])

    def test_no_cards_behaves_as_before(self):
        rule = self._rule(reply_text='仅文本')
        self.assertEqual(_build_segments(rule, ''), ['仅文本'])
        self.assertEqual(_build_segments(rule, '', card_urls=[]), ['仅文本'])

    def test_merged_mode_includes_cards(self):
        rule = self._rule(reply_text='文案', send_mode='merged')
        segs = _build_segments(rule, '', card_urls=['https://site/c/aaa'])
        # 有卡片时强制 multi_message（has_links 语义），不再合并
        self.assertEqual(segs, ['文案', 'https://site/c/aaa'])


class ResolveCardLandingUrlsTests(DjangoTestCase):
    """resolve_card_landing_urls：按 card_ids 顺序、跳过停用/缺失（需 DB）"""

    def test_keeps_order_and_skips_disabled(self):
        from core.douyin.douyin_card_model import DouyinCard
        from core.douyin.douyin_card_schema import build_landing_url

        c1 = DouyinCard.objects.create(title='A', target_url='https://a.com', status=True)
        c2 = DouyinCard.objects.create(title='B', target_url='https://b.com', status=True)
        c3 = DouyinCard.objects.create(title='停用', target_url='https://c.com', status=False)
        rule = SimpleNamespace(card_ids=[str(c2.id), str(c3.id), str(c1.id)])
        urls = resolve_card_landing_urls(rule)
        self.assertEqual(urls, [build_landing_url(str(c2.id)), build_landing_url(str(c1.id))])

    def test_empty_card_ids(self):
        self.assertEqual(resolve_card_landing_urls(SimpleNamespace(card_ids=[])), [])
        self.assertEqual(resolve_card_landing_urls(SimpleNamespace(card_ids=None)), [])

