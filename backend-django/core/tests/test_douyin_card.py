#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音伪装卡片：模型 / Schema / 落地页 / 校验 测试"""
import json
import uuid

from django.test import Client, TestCase

from core.douyin.douyin_card_api import _validate_target_url
from core.douyin.douyin_card_model import DouyinCard
from core.douyin.douyin_card_schema import (
    DouyinCardSchemaOut,
    build_cover_url,
    build_landing_url,
)
from core.douyin.douyin_rule_api import _normalize_card_ids, _validate_cards_exist
from ninja.errors import HttpError


class DouyinCardModelTests(TestCase):
    def test_create_and_soft_delete(self):
        c = DouyinCard.objects.create(
            title='点击咨询', description='点这里找我',
            target_url='https://work.weixin.qq.com/ca/x', status=True,
        )
        self.assertTrue(DouyinCard.objects.filter(id=c.id, is_deleted=False).exists())
        c.delete()  # RootModel 软删除
        self.assertFalse(DouyinCard.objects.filter(id=c.id, is_deleted=False).exists())

    def test_schema_out_resolves_cover_and_landing_url(self):
        c = DouyinCard.objects.create(
            title='卡', target_url='https://a.com/x', cover_file_id='file-123', status=True,
        )
        out = DouyinCardSchemaOut.from_orm(c).dict()
        self.assertEqual(out['landing_url'], build_landing_url(str(c.id)))
        self.assertIn('/api/core/file_manager/proxy/file-123', out['cover_url'])

    def test_cover_url_none_when_no_file(self):
        self.assertIsNone(build_cover_url(None))
        self.assertIsNone(build_cover_url(''))


class TargetUrlValidationTests(TestCase):
    def test_accepts_http_https(self):
        self.assertEqual(_validate_target_url('https://a.com/x'), 'https://a.com/x')
        self.assertEqual(_validate_target_url(' http://b.com '), 'http://b.com')

    def test_rejects_non_http_scheme(self):
        for bad in ('javascript:alert(1)', 'ftp://a.com', 'file:///etc/passwd', 'data:text/html,x'):
            with self.assertRaises(HttpError):
                _validate_target_url(bad)

    def test_rejects_empty(self):
        with self.assertRaises(HttpError):
            _validate_target_url('')


class CardIdsValidationTests(TestCase):
    def test_normalize_dedupes_and_keeps_order(self):
        self.assertEqual(
            _normalize_card_ids(['b', 'a', 'b', '', '  c  ', 'a']),
            ['b', 'a', 'c'],
        )
        self.assertEqual(_normalize_card_ids(None), [])

    def test_validate_cards_exist_passes_for_existing(self):
        c = DouyinCard.objects.create(title='x', target_url='https://a.com', status=True)
        _validate_cards_exist([str(c.id)])  # 不抛异常

    def test_validate_cards_exist_rejects_missing(self):
        with self.assertRaises(HttpError):
            _validate_cards_exist([str(uuid.uuid4())])

    def test_validate_cards_exist_rejects_soft_deleted(self):
        c = DouyinCard.objects.create(title='x', target_url='https://a.com', status=True)
        c.delete()
        with self.assertRaises(HttpError):
            _validate_cards_exist([str(c.id)])


class CardLandingPageTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_enabled_card_renders_og(self):
        c = DouyinCard.objects.create(
            title='点击找到控糖佬师', description='点这里找我',
            target_url='https://work.weixin.qq.com/ca/abc', cover_file_id='cov-1', status=True,
        )
        resp = self.client.get(f'/c/{c.id}')
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('<meta property="og:title" content="点击找到控糖佬师"', html)
        self.assertIn('<meta property="og:description" content="点这里找我"', html)
        self.assertIn('/api/core/file_manager/proxy/cov-1', html)
        # JS 跳转目标为 json 编码，安全注入
        self.assertIn(json.dumps('https://work.weixin.qq.com/ca/abc'), html)

    def test_disabled_card_returns_404(self):
        c = DouyinCard.objects.create(title='停用', target_url='https://a.com', status=False)
        self.assertEqual(self.client.get(f'/c/{c.id}').status_code, 404)

    def test_nonexistent_card_returns_404(self):
        self.assertEqual(self.client.get(f'/c/{uuid.uuid4()}').status_code, 404)

    def test_card_without_cover_omits_og_image(self):
        c = DouyinCard.objects.create(title='无图', target_url='https://a.com', status=True)
        html = self.client.get(f'/c/{c.id}').content.decode()
        self.assertNotIn('og:image', html)

    def test_landing_escapes_script_close_in_target_url(self):
        """target_url 含 </script> 时不得在 HTML 解析层逃逸（存储型 XSS 回归）。"""
        evil = 'https://a.com/x</script><script>alert(1)</script>'
        c = DouyinCard.objects.create(title='x', target_url=evil, status=True)
        html = self.client.get(f'/c/{c.id}').content.decode()
        # 原样的 </script><script> 不得出现在内联脚本里（应被 < 转义）
        self.assertNotIn('</script><script>alert(1)</script>', html)
        self.assertIn('\\u003C/script\\u003E', html)

