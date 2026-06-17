#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DyAuthReply 桌面客户端 — API 服务入口。"""
from __future__ import annotations

import os
import sys


def main() -> None:
    os.environ.setdefault('ZQ_ENV', 'client')
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'application.settings')

    import django

    django.setup()

    from django.core.management import call_command

    call_command('migrate', '--noinput')

    from core.client.bootstrap import get_or_create_local_user

    get_or_create_local_user()

    port = int(os.environ.get('CLIENT_HTTP_PORT', '8765'))
    host = os.environ.get('CLIENT_HTTP_HOST', '127.0.0.1')

    import uvicorn

    uvicorn.run(
        'application.asgi:application',
        host=host,
        port=port,
        log_level=os.environ.get('UVICORN_LOG_LEVEL', 'info'),
        reload=False,
    )


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
