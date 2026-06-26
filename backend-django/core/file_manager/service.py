#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""文件管理服务层：供其它模块复用的存储落库逻辑（不依赖 ninja request/auth）。"""
import mimetypes
import os
from typing import BinaryIO

from core.file_manager.file_manager_model import FileManager
from core.file_manager.storage_backends import get_storage_backend


def save_uploaded_public_file(file: BinaryIO, filename: str) -> FileManager:
    """保存一个公开可访问的上传文件并落库，返回 FileManager 行。

    与 file_manager_api.upload_file 等价，但不要求登录 User（sys_creator 留空），
    用于客户端经 license 通道转发上传的封面图等场景。文件标记 is_public=True，
    可通过 file_manager 的 auth=None 公开路由（/proxy、/url）访问，供抖音爬虫抓取。
    """
    storage = get_storage_backend()
    file_ext = os.path.splitext(filename)[1].lower()
    mime_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'

    storage_path, url = storage.save(file, filename, '')
    md5 = storage.calculate_md5(file)

    return FileManager.objects.create(
        name=filename,
        type='file',
        parent=None,
        path=filename,
        size=getattr(file, 'size', 0) or 0,
        file_ext=file_ext,
        mime_type=mime_type,
        storage_type=storage.__class__.__name__.replace('StorageBackend', '').lower(),
        storage_path=storage_path,
        url=url,
        md5=md5,
        is_public=True,
        sys_creator_id=None,
    )
