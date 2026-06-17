#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""桌面客户端首次启动：确保存在本地超级用户。"""
from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)

LOCAL_USERNAME = 'local'
LOCAL_DISPLAY_NAME = '本地用户'


def get_or_create_local_user():
    from core.user.user_model import User

    user = User.objects.filter(username=LOCAL_USERNAME).first()
    if user:
        return user

    user = User.objects.create(
        username=LOCAL_USERNAME,
        name=LOCAL_DISPLAY_NAME,
        is_active=True,
        is_superuser=True,
        user_status=1,
    )
    user.set_password(str(uuid.uuid4()))
    user.save(update_fields=['password'])
    logger.info('[client.bootstrap] 已创建本地用户 username=%s id=%s', LOCAL_USERNAME, user.id)
    return user
