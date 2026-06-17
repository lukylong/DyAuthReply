#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""桌面客户端 API 路由（仅抖音私信相关）。"""
from datetime import datetime

from ninja import Router
from ninja.renderers import JSONRenderer
from ninja.responses import NinjaJSONEncoder

from common.local_desktop_auth import LocalDesktopAuth
from core.douyin.douyin_account_api import router as douyin_account_router
from core.douyin.douyin_blacklist_api import router as douyin_blacklist_router
from core.douyin.douyin_event_api import router as douyin_event_router
from core.douyin.douyin_quick_reply_api import router as douyin_quick_reply_router
from core.douyin.douyin_reply_log_api import router as douyin_reply_log_router
from core.douyin.douyin_rule_api import router as douyin_rule_router
from core.douyin.douyin_session_api import router as douyin_session_router
from core.douyin.douyin_template_api import router as douyin_template_router
from ninja.main import NinjaAPI

client_router = Router()


class _ClientJsonEncoder(NinjaJSONEncoder):
    def default(self, o):
        if isinstance(o, datetime):
            return o.strftime('%Y-%m-%d %H:%M:%S')
        return super().default(o)


class _ClientJsonRenderer(JSONRenderer):
    encoder_class = _ClientJsonEncoder


client_api = NinjaAPI(
    title='DyAuthReply Client API',
    version='0.1.0',
    auth=LocalDesktopAuth(),
    renderer=_ClientJsonRenderer(),
)


@client_router.get('/health', auth=None, summary='健康检查')
def health(request):
    from env import ENV

    return {
        'ok': True,
        'env': ENV,
        'service': 'dyauthreply-client',
    }


@client_router.get('/bootstrap', auth=None, summary='客户端启动信息（仅本机）')
def bootstrap_info(request):
    from common.local_desktop_auth import _is_loopback
    from core.client.bootstrap import get_or_create_local_user
    from env import CLIENT_DATA_DIR, CLIENT_HTTP_PORT

    if not _is_loopback(request):
        return client_api.create_response(request, {'detail': 'forbidden'}, status=403)

    user = get_or_create_local_user()
    return {
        'user_id': str(user.id),
        'username': user.username,
        'data_dir': CLIENT_DATA_DIR,
        'http_port': CLIENT_HTTP_PORT,
        'api_prefix': '/api/client/v1',
    }


client_api.add_router('/client/v1', client_router)
client_api.add_router('/client/v1/douyin', douyin_account_router, tags=['Client-Douyin-Account'])
client_api.add_router('/client/v1/douyin', douyin_rule_router, tags=['Client-Douyin-Rule'])
client_api.add_router('/client/v1/douyin', douyin_template_router, tags=['Client-Douyin-Template'])
client_api.add_router('/client/v1/douyin', douyin_session_router, tags=['Client-Douyin-Session'])
client_api.add_router('/client/v1/douyin', douyin_reply_log_router, tags=['Client-Douyin-ReplyLog'])
client_api.add_router('/client/v1/douyin', douyin_blacklist_router, tags=['Client-Douyin-Blacklist'])
client_api.add_router('/client/v1/douyin', douyin_quick_reply_router, tags=['Client-Douyin-QuickReply'])
client_api.add_router('/client/v1/douyin', douyin_event_router, tags=['Client-Douyin-Event'])
