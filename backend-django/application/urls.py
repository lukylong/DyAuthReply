#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: urls.py
@Desc: URL configuration for application project. - 
"""
"""
URL configuration for application project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.urls import path, re_path
from django.views.static import serve
import os
from django.conf import settings
from django.http import HttpResponse, Http404
from django.shortcuts import render

from application.main import api


def download_landing(request):
    """根路径 / 的公开下载落地页：安装包/插件下载 + 使用文档。无需登录。"""
    return render(request, 'landing.html', {
        'macos_url': settings.DOWNLOAD_MACOS_URL,
        'windows_url': settings.DOWNLOAD_WINDOWS_URL,
        'extension_url': settings.DOWNLOAD_EXTENSION_URL,
        'release_page': settings.DOWNLOAD_RELEASE_PAGE,
        'console_url': settings.DOWNLOAD_CONSOLE_URL,
    })


def serve_download(request, name):
    """自托管安装包/插件下载：从 DOWNLOAD_LOCAL_DIR 提供文件（国内直连，规避 GitHub 慢）。

    生产建议在 nginx 用 `location /downloads/ { alias <目录>/; }` 直出以支持断点续传；
    此 Django 路由作为可移植兜底（无需改 nginx 即可工作）。
    """
    return serve(request, name, document_root=settings.DOWNLOAD_LOCAL_DIR)


def serve_spa(request, path=''):
    # 忽略 API 和 WS（WebSocket）请求，由 Django 各自专门路由处理
    if path.startswith('api/') or path.startswith('ws/'):
        raise Http404("Not Found")

    # 如果访问的是 /manage 并且没有以 / 结尾，则重定向到 /manage/ 保证浏览器相对资源路径解析正确
    if not path and not request.path.endswith('/'):
        from django.shortcuts import redirect
        return redirect(request.path + '/')

    path = path or ''
    dist_dir = os.path.join(settings.BASE_DIR, 'dist')

    # 尝试读取静态资产物理文件（如 assets/ 目录下的 js/css 或 favicon.ico 等）
    file_path = os.path.join(dist_dir, path)
    if path and os.path.exists(file_path) and os.path.isfile(file_path):
        return serve(request, path, document_root=dist_dir)

    # 其他情况一律返回 index.html（供前端 Vue Router 接管 SPA 路由进行客户端渲染）
    index_path = os.path.join(dist_dir, 'index.html')
    if os.path.exists(index_path):
        with open(index_path, 'rb') as f:
            return HttpResponse(f.read(), content_type='text/html')

    raise Http404("前端构建产物未找到，请构建前端并将 dist 目录复制到 backend-django 目录下。")

urlpatterns = [
    path('', download_landing),
    re_path(r'^downloads/(?P<name>.+)$', serve_download),
    path('api/', api.urls),
    re_path(r'^manage(?:/(?P<path>.*))?$', serve_spa),
]
