# -*- coding: utf-8 -*-
"""客户端公告模型"""
import uuid
from django.db import models
from common.fu_model import RootModel


def generate_uuid():
    """生成 UUID 字符串"""
    return str(uuid.uuid4())


class ClientAnnouncement(RootModel):
    """客户端公告模型"""

    # 覆盖 id 字段以避免 lambda 序列化错误
    id = models.CharField(
        primary_key=True,
        max_length=36,
        default=generate_uuid,
        help_text="主键ID",
        editable=False,
    )

    title = models.CharField(max_length=200, verbose_name='公告标题')
    content = models.TextField(verbose_name='公告内容')
    level = models.CharField(
        max_length=20,
        choices=[
            ('info', '普通'),
            ('warning', '警告'),
            ('urgent', '紧急'),
        ],
        default='info',
        verbose_name='公告级别'
    )
    status = models.CharField(
        max_length=20,
        choices=[
            ('draft', '草稿'),
            ('published', '已发布'),
            ('revoked', '已撤回'),
        ],
        default='draft',
        verbose_name='状态'
    )
    publish_time = models.DateTimeField(null=True, blank=True, verbose_name='发布时间')
    expire_time = models.DateTimeField(null=True, blank=True, verbose_name='过期时间')
    target_version = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        verbose_name='目标客户端版本',
        help_text='留空表示所有版本，支持版本范围如 >=0.1.10'
    )

    class Meta:
        db_table = 'core_client_announcement'
        verbose_name = '客户端公告'
        verbose_name_plural = verbose_name
        ordering = ['-publish_time', '-sys_create_datetime']

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"
