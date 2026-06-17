#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""桌面客户端 API 路由（仅抖音私信相关）。"""
from datetime import datetime

from ninja import Router, Schema
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


class AdminLoginIn(Schema):
    password: str


class EmergencyStopIn(Schema):
    reason: str = '管理员急停'


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


@client_router.get('/runtime-logs/files', summary='运行日志文件列表（隐藏入口）')
def runtime_log_files(request):
    from core.client.admin_auth import require_admin
    from core.client.runtime_logs import list_runtime_log_files

    require_admin(request)
    return {'items': list_runtime_log_files()}


@client_router.get('/runtime-logs/tail', summary='运行日志 tail（隐藏入口）')
def runtime_log_tail(request, lines: int = 400, file: str = ''):
    from core.client.admin_auth import require_admin
    from core.client.runtime_logs import tail_runtime_logs

    require_admin(request)
    safe_lines = max(50, min(int(lines or 400), 2000))
    return tail_runtime_logs(max_lines=safe_lines, file_name=file or None)


@client_router.post('/admin/login', auth=None, summary='管理员登录（隐藏入口）')
def admin_login(request, payload: AdminLoginIn):
    from common.local_desktop_auth import _is_loopback
    from core.client.admin_auth import issue_admin_token, verify_admin_password

    if not _is_loopback(request):
        return client_api.create_response(request, {'detail': 'forbidden'}, status=403)

    password = (payload.password or '').strip()
    if not verify_admin_password(password):
        return client_api.create_response(request, {'detail': '密码错误'}, status=401)
    return issue_admin_token()


@client_router.post('/admin/logout', summary='管理员退出')
def admin_logout(request):
    from core.client.admin_auth import require_admin, revoke_admin_token

    require_admin(request)
    token = request.headers.get('X-Admin-Token') or request.META.get('HTTP_X_ADMIN_TOKEN')
    revoke_admin_token(token)
    return {'ok': True}


@client_router.get('/admin/dashboard', summary='管理员控制台概览')
def admin_dashboard(request):
    from core.client.admin_auth import require_admin
    from core.client.admin_console import get_admin_dashboard

    require_admin(request)
    return get_admin_dashboard()


@client_router.post('/admin/emergency-stop', summary='急停：关闭自动回复并清空待发命令')
def admin_emergency_stop(request, payload: EmergencyStopIn):
    from core.client.admin_auth import require_admin
    from core.client.admin_console import emergency_stop

    require_admin(request)
    return emergency_stop(reason=(payload.reason or '管理员急停').strip())


@client_router.post('/lifecycle/shutdown', auth=None, summary='退出客户端（仅本机）')
def lifecycle_shutdown(request):
    from common.local_desktop_auth import _is_loopback
    from core.client.lifecycle import schedule_shutdown

    if not _is_loopback(request):
        return client_api.create_response(request, {'detail': 'forbidden'}, status=403)
    return schedule_shutdown(reason='api')


client_api.add_router('/client/v1', client_router)
client_api.add_router('/client/v1/douyin', douyin_account_router, tags=['Client-Douyin-Account'])
client_api.add_router('/client/v1/douyin', douyin_rule_router, tags=['Client-Douyin-Rule'])
client_api.add_router('/client/v1/douyin', douyin_template_router, tags=['Client-Douyin-Template'])
client_api.add_router('/client/v1/douyin', douyin_session_router, tags=['Client-Douyin-Session'])
client_api.add_router('/client/v1/douyin', douyin_reply_log_router, tags=['Client-Douyin-ReplyLog'])
client_api.add_router('/client/v1/douyin', douyin_blacklist_router, tags=['Client-Douyin-Blacklist'])
client_api.add_router('/client/v1/douyin', douyin_quick_reply_router, tags=['Client-Douyin-QuickReply'])
client_api.add_router('/client/v1/douyin', douyin_event_router, tags=['Client-Douyin-Event'])
