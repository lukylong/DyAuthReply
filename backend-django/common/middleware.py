#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: middleware.py
@Desc: 日志 django中间件
"""
"""
日志 django中间件
"""
import json
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.utils.deprecation import MiddlewareMixin
from ninja.errors import HttpError

from common.utils.request_util import (
    get_browser,
    get_os,
    get_request_data,
    get_request_ip,
    get_request_path,
    get_request_user,
    get_verbose_name,
)
from core.models import User


class SecurityHeadersMiddleware(MiddlewareMixin):
    """
    添加安全相关的 HTTP 头
    """

    def process_response(self, request, response):
        # 添加安全头
        response['X-Content-Type-Options'] = 'nosniff'
        response['X-Frame-Options'] = 'DENY'
        response['X-XSS-Protection'] = '1; mode=block'
        response['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'

        # Content Security Policy - 可根据实际需求调整
        response[
            'Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';"

        return response


class CorsMiddleware(MiddlewareMixin):
    """
    极简本地跨域处理中间件，供本地前端和 Tauri 壳直连使用
    """

    def _is_client_mode(self) -> bool:
        return getattr(settings, 'ROOT_URLCONF', '') == 'application.client_urls'

    def _is_allowed_client_origin(self, origin: str) -> bool:
        parsed = urlparse(origin)
        scheme = parsed.scheme.lower()
        hostname = (parsed.hostname or '').lower()
        if scheme in ('tauri', 'file'):
            return True
        if scheme not in ('http', 'https'):
            return False
        return hostname in ('127.0.0.1', 'localhost', '::1', 'tauri.localhost') or hostname.endswith('.localhost')

    def _allow_origin(self, request) -> str:
        origin = request.headers.get('Origin', '')
        if not origin:
            return ''
        if self._is_client_mode():
            return origin if self._is_allowed_client_origin(origin) else ''
        return origin

    def _with_cors_headers(self, response, origin: str):
        response['Access-Control-Allow-Origin'] = origin
        response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
        response['Access-Control-Allow-Headers'] = (
            'Content-Type, Authorization, X-Requested-With, X-CSRFToken, X-Admin-Token'
        )
        response['Access-Control-Allow-Credentials'] = 'true'
        return response

    def process_request(self, request):
        if request.method == 'OPTIONS':
            origin = self._allow_origin(request)
            if self._is_client_mode() and request.headers.get('Origin') and not origin:
                return HttpResponse(status=403)
            response = HttpResponse()
            return self._with_cors_headers(response, origin or '*')

    def process_response(self, request, response):
        origin = self._allow_origin(request)
        if origin:
            self._with_cors_headers(response, origin)
        return response


class ClientLicenseGateMiddleware(MiddlewareMixin):
    """
    客户端本地服务端统一授权闸门。

    仅在 client URLConf 下生效：
    - 放行健康检查、启动信息、授权状态/激活/心跳/解绑、隐藏诊断接口、只读查询
    - 拦截商业写接口
    """

    CLIENT_DOUYIN_PREFIX = '/api/client/v1/douyin/'

    SAFE_PREFIXES = (
        '/api/client/v1/health',
        '/api/client/v1/bootstrap',
        '/api/client/v1/license/status',
        '/api/client/v1/license/activate',
        '/api/client/v1/license/check-in',
        '/api/client/v1/license/deactivate',
        '/api/client/v1/admin/',
        '/api/client/v1/runtime-logs/',
        '/api/client/v1/lifecycle/',
    )

    SAFE_DOUYIN_WRITE_PREFIXES = (
        '/api/client/v1/douyin/session/heartbeat',
        '/api/client/v1/douyin/event/report',
        '/api/client/v1/douyin/event/batch/read',
        '/api/client/v1/douyin/event/read-all',
    )

    def _is_client_mode(self) -> bool:
        return getattr(settings, 'ROOT_URLCONF', '') == 'application.client_urls'

    def _is_protected_path(self, path: str) -> bool:
        if any(path.startswith(prefix) for prefix in self.SAFE_PREFIXES):
            return False
        if not path.startswith(self.CLIENT_DOUYIN_PREFIX):
            return False
        if any(path.startswith(prefix) for prefix in self.SAFE_DOUYIN_WRITE_PREFIXES):
            return False
        return True

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not self._is_client_mode():
            return None

        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return None

        path = request.path or ''
        if not self._is_protected_path(path):
            return None

        from core.client.license_auth import require_client_business_access

        try:
            require_client_business_access('当前业务操作')
        except HttpError as exc:
            return HttpResponse(str(exc.message), status=exc.status_code, content_type='text/plain; charset=utf-8')
        return None



# ApiLoggingMiddleware removed because OperationLog model does not exist in this project
