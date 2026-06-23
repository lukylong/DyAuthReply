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
import re
from django.conf import settings
from django.http import HttpResponse, Http404
from django.shortcuts import render

# 下载文件名安全白名单：仅允许扁平的安全文件名，杜绝路径穿越与响应头注入
_SAFE_DOWNLOAD_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')

from application.main import api


def download_landing(request):
    """根路径 / 的公开下载落地页：安装包/插件下载 + 使用文档。无需登录。"""
    return render(request, 'landing.html', {
        'version': settings.DOWNLOAD_LATEST_VERSION,
        'macos_url': settings.DOWNLOAD_MACOS_URL,
        'windows_url': settings.DOWNLOAD_WINDOWS_URL,
        'extension_url': settings.DOWNLOAD_EXTENSION_URL,
        'release_page': settings.DOWNLOAD_RELEASE_PAGE,
        'console_url': settings.DOWNLOAD_CONSOLE_URL,
    })


def _versioned_download_name(name):
    """客户端安装包按当前版本号生成下载文件名，方便用户辨认版本。

    物理文件名保持稳定（维持滚动 latest 链接不变），仅在响应头里改下载名。
    插件 zip 版本独立于客户端，不加版本号。
    """
    version = (getattr(settings, 'DOWNLOAD_LATEST_VERSION', '') or '').strip()
    if not version:
        return None
    client_files = {settings.DOWNLOAD_MACOS_FILE, settings.DOWNLOAD_WINDOWS_FILE}
    base = os.path.basename(name)
    if base not in client_files:
        return None
    stem, ext = os.path.splitext(base)
    return f"{stem}-v{version}{ext}"


def serve_download(request, name):
    """自托管安装包/插件下载：从 DOWNLOAD_LOCAL_DIR 提供文件（国内直连，规避 GitHub 慢）。

    三种部署形态：
    - 默认：Django 直接读盘返回（可移植兜底，无需改 nginx）。
    - 设置 DOWNLOAD_XACCEL_LOCATION（如 /_dl_internal）：返回 X-Accel-Redirect，
      由 nginx 的 internal location 直出（sendfile/断点续传），同时仍由 Django
      控制带版本号的下载文件名——既快又能显示版本，且发版不用改 nginx。
    - 也可在 nginx 用 `location /downloads/ { alias <目录>/; }` 完全绕过 Django
      静态直出（最快，但下载文件名不含版本号）。
    """
    # 仅允许扁平安全文件名，杜绝 ../ 路径穿越与 CRLF 响应头注入
    base = os.path.basename(name)
    if base != name or not _SAFE_DOWNLOAD_NAME.match(base):
        raise Http404("Not Found")

    versioned = _versioned_download_name(base)
    xaccel = (getattr(settings, 'DOWNLOAD_XACCEL_LOCATION', '') or '').strip()

    if xaccel:
        file_path = os.path.join(settings.DOWNLOAD_LOCAL_DIR, base)
        if not os.path.isfile(file_path):
            raise Http404("Not Found")
        response = HttpResponse()
        # 交给 nginx：Content-Type/Length 由 nginx 直出阶段补齐
        del response['Content-Type']
        response['X-Accel-Redirect'] = xaccel.rstrip('/') + '/' + base
    else:
        response = serve(request, base, document_root=settings.DOWNLOAD_LOCAL_DIR)

    if versioned:
        response['Content-Disposition'] = f'attachment; filename="{versioned}"'
    return response


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
