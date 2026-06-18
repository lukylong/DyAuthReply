import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import django
from django.conf import settings
from ninja.errors import HttpError

os.environ.setdefault('CLIENT_DATA_DIR', '/private/tmp/dyauthreply-test-client')
os.environ.setdefault('ZQ_ENV', 'client')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'application.settings')
Path(os.environ['CLIENT_DATA_DIR']).mkdir(parents=True, exist_ok=True)
django.setup()

from common.middleware import ClientLicenseGateMiddleware


def _request(path: str, method: str = 'POST'):
    return SimpleNamespace(
        method=method,
        path=path,
    )


def test_client_mode_allows_read_requests_without_license_check():
    settings.ROOT_URLCONF = 'application.client_urls'
    middleware = ClientLicenseGateMiddleware(lambda request: None)

    with patch('core.client.license_auth.require_client_business_access') as guard:
        response = middleware.process_view(
            _request('/api/client/v1/douyin/quick-reply', 'GET'),
            None,
            (),
            {},
        )

    assert response is None
    guard.assert_not_called()


def test_client_mode_allows_safe_local_license_write_without_license_check():
    settings.ROOT_URLCONF = 'application.client_urls'
    middleware = ClientLicenseGateMiddleware(lambda request: None)

    with patch('core.client.license_auth.require_client_business_access') as guard:
        response = middleware.process_view(
            _request('/api/client/v1/license/check-in'),
            None,
            (),
            {},
        )

    assert response is None
    guard.assert_not_called()


def test_client_mode_allows_worker_runtime_writes_without_license_check():
    settings.ROOT_URLCONF = 'application.client_urls'
    middleware = ClientLicenseGateMiddleware(lambda request: None)

    with patch('core.client.license_auth.require_client_business_access') as guard:
        response = middleware.process_view(
            _request('/api/client/v1/douyin/session/heartbeat'),
            None,
            (),
            {},
        )

    assert response is None
    guard.assert_not_called()


def test_client_mode_blocks_protected_write_when_license_invalid():
    settings.ROOT_URLCONF = 'application.client_urls'
    middleware = ClientLicenseGateMiddleware(lambda request: None)

    with patch(
        'core.client.license_auth.require_client_business_access',
        side_effect=HttpError(403, '当前授权状态为「已撤销」，无法执行当前业务操作'),
    ) as guard:
        response = middleware.process_view(
            _request('/api/client/v1/douyin/quick-reply'),
            None,
            (),
            {},
        )

    assert response is not None
    assert response.status_code == 403
    assert '已撤销' in response.content.decode('utf-8')
    guard.assert_called_once_with('当前业务操作')


def test_client_mode_allows_protected_write_when_license_valid():
    settings.ROOT_URLCONF = 'application.client_urls'
    middleware = ClientLicenseGateMiddleware(lambda request: None)

    with patch(
        'core.client.license_auth.require_client_business_access',
        return_value={'can_use_business': True},
    ) as guard:
        response = middleware.process_view(
            _request('/api/client/v1/douyin/session/123/control'),
            None,
            (),
            {},
        )

    assert response is None
    guard.assert_called_once_with('当前业务操作')
