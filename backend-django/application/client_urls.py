#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""桌面客户端 URL：API + client-ui SPA。"""
import os

from django.conf import settings
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.urls import path, re_path
from django.views.static import serve

from application.client_main import client_api


def serve_client_ui(request, path=''):
    if path.startswith('api/') or path.startswith('ws/'):
        raise Http404('Not Found')

    if not path and not request.path.endswith('/'):
        return redirect(request.path + '/')

    from core.client.ui_updater import get_active_ui_dist

    dist_dir = get_active_ui_dist()
    if not dist_dir:
        dist_dir = getattr(settings, 'CLIENT_UI_DIST', None) or os.environ.get('CLIENT_UI_DIST', '')
    
    if not dist_dir or not os.path.isdir(dist_dir):
        return HttpResponse(
            '<h1>DyAuthReply Client</h1>'
            '<p>客户端 UI 未构建。请在 dyauthreply-client/client-ui 执行 <code>npm install && npm run build</code></p>'
            '<p><a href="/api/docs">API 文档</a></p>',
            content_type='text/html; charset=utf-8',
            status=503,
        )

    path = path or ''
    file_path = os.path.join(dist_dir, path)
    if path and os.path.exists(file_path) and os.path.isfile(file_path):
        return serve(request, path, document_root=dist_dir)

    index_path = os.path.join(dist_dir, 'index.html')
    if os.path.exists(index_path):
        with open(index_path, 'rb') as f:
            return HttpResponse(f.read(), content_type='text/html')

    raise Http404('client-ui index.html not found')


urlpatterns = [
    path('api/', client_api.urls),
    re_path(r'^app(?:/(?P<path>.*))?$', serve_client_ui),
    path('', lambda request: redirect('/app/')),
]
