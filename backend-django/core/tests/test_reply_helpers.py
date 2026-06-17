from types import SimpleNamespace
from unittest import TestCase

from core.douyin.runtime.reply_helpers import (
    _build_segments,
    _normalize_send_mode,
    render_template,
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
