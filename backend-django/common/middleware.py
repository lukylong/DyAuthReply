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

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.utils.deprecation import MiddlewareMixin

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

    def process_request(self, request):
        if request.method == 'OPTIONS':
            from django.http import HttpResponse
            response = HttpResponse()
            response['Access-Control-Allow-Origin'] = request.headers.get('Origin', '*')
            response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
            response['Access-Control-Allow-Headers'] = (
                'Content-Type, Authorization, X-Requested-With, X-CSRFToken, X-Admin-Token'
            )
            response['Access-Control-Allow-Credentials'] = 'true'
            return response

    def process_response(self, request, response):
        origin = request.headers.get('Origin')
        if origin:
            response['Access-Control-Allow-Origin'] = origin
            response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
            response['Access-Control-Allow-Headers'] = (
                'Content-Type, Authorization, X-Requested-With, X-CSRFToken, X-Admin-Token'
            )
            response['Access-Control-Allow-Credentials'] = 'true'
        return response



# ApiLoggingMiddleware removed because OperationLog model does not exist in this project
