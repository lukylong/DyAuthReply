#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""客户端卡片同步接口（公网侧）测试：复用 license activation 鉴权 + upsert/delete/cover。"""
import json

from django.test import Client, TestCase
from django.utils import timezone

from core.douyin.douyin_card_model import DouyinCard
from core.license.license_model import (
    ClientDevice,
    LicenseActivation,
    LicenseKey,
    LicensePlan,
)
from core.license.license_service import hash_activation_token


def _make_activation(fingerprint='device-abc', token='tok-123'):
    plan = LicensePlan.objects.create(code='p1', name='套餐', feature_flags={})
    key = LicenseKey.objects.create(
        code_hash='x' * 64, masked_code='AAA***', plan=plan, status='active',
    )
    device = ClientDevice.objects.create(device_fingerprint=fingerprint, status='active')
    act = LicenseActivation.objects.create(
        license_key=key, client_device=device, status='active',
        activation_token_hash=hash_activation_token(token),
        activated_at=timezone.now(),
    )
    return act, device, token


class ClientCardSyncTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.act, self.device, self.token = _make_activation()

    def _post(self, path, payload):
        return self.client.post(
            f'/api/client-auth/{path}', data=json.dumps(payload),
            content_type='application/json',
        )

    def test_upsert_creates_public_card(self):
        cid = '11111111-1111-1111-1111-111111111111'
        resp = self._post('cards/upsert', {
            'activation_id': str(self.act.id),
            'activation_token': self.token,
            'id': cid,
            'title': '点击咨询',
            'description': '点这里找我',
            'target_url': 'https://work.weixin.qq.com/ca/x',
            'status': True,
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body['ok'])
        self.assertIn(f'/c/{cid}', body['landing_url'])
        card = DouyinCard.objects.get(id=cid)
        self.assertEqual(card.title, '点击咨询')
        self.assertEqual(card.source_device_id, self.device.device_fingerprint)
        self.assertEqual(card.sync_state, 'synced')

    def test_upsert_updates_existing(self):
        cid = '22222222-2222-2222-2222-222222222222'
        base = {
            'activation_id': str(self.act.id), 'activation_token': self.token,
            'id': cid, 'target_url': 'https://a.com/x',
        }
        self._post('cards/upsert', {**base, 'title': '旧'})
        self._post('cards/upsert', {**base, 'title': '新'})
        self.assertEqual(DouyinCard.objects.filter(id=cid).count(), 1)
        self.assertEqual(DouyinCard.objects.get(id=cid).title, '新')

    def test_upsert_rejects_bad_token(self):
        resp = self._post('cards/upsert', {
            'activation_id': str(self.act.id), 'activation_token': 'wrong',
            'id': '33333333-3333-3333-3333-333333333333',
            'title': 'x', 'target_url': 'https://a.com',
        })
        self.assertEqual(resp.status_code, 401)

    def test_upsert_rejects_non_http_url(self):
        resp = self._post('cards/upsert', {
            'activation_id': str(self.act.id), 'activation_token': self.token,
            'id': '44444444-4444-4444-4444-444444444444',
            'title': 'x', 'target_url': 'javascript:alert(1)',
        })
        self.assertEqual(resp.status_code, 400)

    def test_upsert_rejects_overwriting_other_device_card(self):
        """IDOR 防护：合法激活客户端不能用已属于别设备的 card id 覆盖其内容。"""
        cid = '77777777-7777-7777-7777-777777777777'
        DouyinCard.objects.create(
            id=cid, title='受害卡片', target_url='https://victim.com', status=True,
            source_device_id='some-other-device',
        )
        resp = self._post('cards/upsert', {
            'activation_id': str(self.act.id), 'activation_token': self.token,
            'id': cid, 'title': '钓鱼', 'target_url': 'https://attacker.com',
        })
        self.assertEqual(resp.status_code, 403)
        card = DouyinCard.objects.get(id=cid)
        self.assertEqual(card.target_url, 'https://victim.com')
        self.assertEqual(card.title, '受害卡片')

    def test_delete_soft_deletes_own_card(self):
        cid = '55555555-5555-5555-5555-555555555555'
        DouyinCard.objects.create(
            id=cid, title='x', target_url='https://a.com', status=True,
            source_device_id=self.device.device_fingerprint,
        )
        resp = self._post('cards/delete', {
            'activation_id': str(self.act.id), 'activation_token': self.token, 'id': cid,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(DouyinCard.objects.filter(id=cid, is_deleted=False).exists())

    def test_delete_rejects_other_device_card(self):
        cid = '66666666-6666-6666-6666-666666666666'
        DouyinCard.objects.create(
            id=cid, title='x', target_url='https://a.com', status=True,
            source_device_id='some-other-device',
        )
        resp = self._post('cards/delete', {
            'activation_id': str(self.act.id), 'activation_token': self.token, 'id': cid,
        })
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(DouyinCard.objects.filter(id=cid, is_deleted=False).exists())
