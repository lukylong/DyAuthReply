import importlib
import os
from pathlib import Path
from unittest import mock

import django
from django.conf import settings

os.environ.setdefault('CLIENT_DATA_DIR', '/private/tmp/dyauthreply-test-client')
os.environ.setdefault('ZQ_ENV', 'client')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'application.settings')
Path(os.environ['CLIENT_DATA_DIR']).mkdir(parents=True, exist_ok=True)
django.setup()

from core.douyin.runtime.transport.sign import js_signer


def test_client_mode_never_falls_back_to_execjs_context():
    settings.DOUYIN_SIGN_POOL_ENABLED = True

    with (
        mock.patch.object(js_signer, '_pool', None),
        mock.patch.object(js_signer, '_pool_failed', True),
        mock.patch.object(js_signer, '_pool_error', 'pool boot failed'),
        mock.patch.object(js_signer, '_get_ctx', side_effect=AssertionError('_get_ctx should not be called')),
    ):
        try:
            js_signer._pool_or_ctx_call('get_ab', 'foo=bar', '')
        except js_signer.JsSignerUnavailable as exc:
            assert 'Node 签名进程不可用' in str(exc)
            assert 'pool boot failed' in str(exc)
        else:
            raise AssertionError('expected JsSignerUnavailable when pool is unavailable in client mode')


def test_client_probe_reports_sign_not_ready_without_execjs_fallback():
    settings.DOUYIN_SIGN_POOL_ENABLED = True
    sign_probe = importlib.import_module('core.client.sign_probe')
    sign_probe._cached = None

    with (
        mock.patch.object(js_signer, '_pool', None),
        mock.patch.object(js_signer, '_pool_failed', True),
        mock.patch.object(js_signer, '_pool_error', 'node missing'),
        mock.patch.object(js_signer, '_node_bin', return_value='C:/missing/node.exe'),
        mock.patch.object(js_signer, '_resolve_js_dir', return_value=Path('/tmp/fake-sign-js')),
        mock.patch.object(js_signer, '_get_ctx', side_effect=AssertionError('_get_ctx should not be called')),
    ):
        status = sign_probe.probe_sign_engine(force=True)

    assert status['sign_js_ready'] is False
    assert status['node_bin'] == 'C:/missing/node.exe'
    assert status['sign_js_detail']
