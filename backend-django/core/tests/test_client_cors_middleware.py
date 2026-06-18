import os
from pathlib import Path
from types import SimpleNamespace

import django
from django.conf import settings
from django.http import HttpResponse

os.environ.setdefault('CLIENT_DATA_DIR', '/private/tmp/dyauthreply-test-client')
os.environ.setdefault('ZQ_ENV', 'client')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'application.settings')
Path(os.environ['CLIENT_DATA_DIR']).mkdir(parents=True, exist_ok=True)
django.setup()

from common.middleware import CorsMiddleware


def _request(origin: str = '', method: str = 'GET'):
    return SimpleNamespace(
        method=method,
        headers={'Origin': origin} if origin else {},
    )


def test_client_mode_rejects_non_local_preflight():
    settings.ROOT_URLCONF = 'application.client_urls'
    middleware = CorsMiddleware(lambda request: HttpResponse())

    response = middleware.process_request(_request('https://example.com', 'OPTIONS'))

    assert response.status_code == 403


def test_client_mode_omits_cors_for_non_local_origin():
    settings.ROOT_URLCONF = 'application.client_urls'
    middleware = CorsMiddleware(lambda request: HttpResponse())
    response = HttpResponse()

    middleware.process_response(_request('https://example.com'), response)

    assert 'Access-Control-Allow-Origin' not in response


def test_client_mode_allows_localhost_origin():
    settings.ROOT_URLCONF = 'application.client_urls'
    middleware = CorsMiddleware(lambda request: HttpResponse())
    response = HttpResponse()

    middleware.process_response(_request('http://localhost:5173'), response)

    assert response['Access-Control-Allow-Origin'] == 'http://localhost:5173'
