#!/usr/bin/env python
# -*- coding: utf-8 -*-
# time: 2024/12/19
# file: file_manager_model.py
# author: 臧成龙
# QQ: 939589097

from django.db import models

from common.fu_model import RootModel


class FileManager(RootModel):
    """文件管理模型"""
    STORAGE_TYPE_CHOICES = (
        ('local', '本地存储'),
        ('oss', '阿里云OSS'),
        ('minio', 'Minio'),
        ('azure', 'Azure Blob'),
    )

    FILE_TYPE_CHOICES = (
        ('file', '文件'),
        ('folder', '文件夹'),
    )

    name = models.CharField(max_length=255, help_text="文件/文件夹名称")
    type = models.CharField(max_length=10, choices=FILE_TYPE_CHOICES, default='file', help_text="类型")
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='children',
                               help_text="父文件夹")
    path = models.TextField(help_text="文件路径")
    size = models.BigIntegerField(default=0, help_text="文件大小(字节)")
    file_ext = models.CharField(max_length=50, null=True, blank=True, help_text="文件扩展名")
    mime_type = models.CharField(max_length=200, null=True, blank=True, help_text="MIME类型")
    storage_type = models.CharField(max_length=20, choices=STORAGE_TYPE_CHOICES, default='local', help_text="存储类型")
    storage_path = models.TextField(help_text="存储路径")
    url = models.TextField(null=True, blank=True, help_text="访问URL")
    thumbnail_url = models.TextField(null=True, blank=True, help_text="缩略图URL")
    md5 = models.CharField(max_length=32, null=True, blank=True, help_text="文件MD5")
    is_public = models.BooleanField(default=False, help_text="是否公开")
    download_count = models.IntegerField(default=0, help_text="下载次数")

    class Meta:
        db_table = "core_file_manager"
        ordering = ("type", "-sys_create_datetime")
        indexes = [
            models.Index(fields=['parent', 'type']),
            models.Index(fields=['storage_type']),
        ]

    def __str__(self):
        return self.name
