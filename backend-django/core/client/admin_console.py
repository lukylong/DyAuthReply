#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""桌面客户端管理员控制台：监控、急停。"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

import psutil


def _data_root() -> Path:
    from env import CLIENT_DATA_DIR

    return Path(CLIENT_DATA_DIR)


def _db_path() -> Path:
    from django.conf import settings

    return Path(settings.DATABASES['default']['NAME'])


def _format_bytes(num: int) -> str:
    if num < 1024:
        return f'{num} B'
    if num < 1024 * 1024:
        return f'{num / 1024:.1f} KB'
    if num < 1024 * 1024 * 1024:
        return f'{num / (1024 * 1024):.1f} MB'
    return f'{num / (1024 * 1024 * 1024):.2f} GB'


def get_process_overview() -> dict:
    current = psutil.Process(os.getpid())
    with current.oneshot():
        api_info = {
            'pid': current.pid,
            'name': current.name(),
            'status': current.status(),
            'cpu_percent': round(current.cpu_percent(interval=0.0) or 0.0, 1),
            'memory_mb': round((current.memory_info().rss or 0) / (1024 * 1024), 1),
            'threads': current.num_threads(),
            'create_time': datetime.fromtimestamp(current.create_time()).strftime('%Y-%m-%d %H:%M:%S'),
        }

    related: list[dict] = []
    keywords = ('launcher', 'dyauthreply', 'douyin', 'uvicorn', 'python')
    seen_pids = {current.pid}
    for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'status', 'memory_info', 'create_time']):
        try:
            info = proc.info
            pid = info.get('pid')
            if not pid or pid in seen_pids:
                continue
            name = (info.get('name') or '').lower()
            cmdline = ' '.join(info.get('cmdline') or []).lower()
            if not any(k in name or k in cmdline for k in keywords):
                continue
            mem = info.get('memory_info')
            rss = getattr(mem, 'rss', 0) if mem else 0
            related.append(
                {
                    'pid': pid,
                    'name': info.get('name') or '',
                    'status': info.get('status') or '',
                    'memory_mb': round(rss / (1024 * 1024), 1),
                    'cmdline': ' '.join(info.get('cmdline') or [])[:240],
                    'create_time': datetime.fromtimestamp(info.get('create_time') or 0).strftime(
                        '%Y-%m-%d %H:%M:%S'
                    ),
                }
            )
            seen_pids.add(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return {
        'api': api_info,
        'related_processes': sorted(related, key=lambda x: x['pid']),
        'system': {
            'cpu_percent': round(psutil.cpu_percent(interval=0.1) or 0.0, 1),
            'memory_percent': round(psutil.virtual_memory().percent, 1),
            'memory_available_mb': round(psutil.virtual_memory().available / (1024 * 1024), 1),
        },
    }


def get_database_overview() -> dict:
    db_path = _db_path()
    data_root = _data_root()
    overview = {
        'path': str(db_path),
        'exists': db_path.is_file(),
        'size': db_path.stat().st_size if db_path.is_file() else 0,
        'size_human': _format_bytes(db_path.stat().st_size if db_path.is_file() else 0),
        'backup_exists': (data_root / 'db.sqlite3.bak').is_file(),
        'tables': {},
        'pending_worker_commands': 0,
    }

    if not db_path.is_file():
        return overview

    from django.db import connection

    with connection.cursor() as cursor:
        for table in (
            'core_douyin_account',
            'core_douyin_conversation',
            'core_douyin_message',
            'core_douyin_reply_log',
            'core_douyin_worker_command',
            'core_douyin_session',
            'core_douyin_event',
        ):
            try:
                cursor.execute(f'SELECT COUNT(*) FROM {table}')
                overview['tables'][table] = cursor.fetchone()[0]
            except Exception:  # noqa: BLE001
                overview['tables'][table] = None

        try:
            cursor.execute(
                "SELECT COUNT(*) FROM core_douyin_worker_command WHERE consumed_at IS NULL"
            )
            overview['pending_worker_commands'] = cursor.fetchone()[0]
        except Exception:  # noqa: BLE001
            pass

        try:
            cursor.execute(
                "SELECT COUNT(*) FROM core_douyin_message WHERE direction='in' AND processed=0"
            )
            overview['unprocessed_inbound_messages'] = cursor.fetchone()[0]
        except Exception:  # noqa: BLE001
            overview['unprocessed_inbound_messages'] = None

    try:
        with sqlite3.connect(str(db_path), timeout=2) as conn:
            page_count = conn.execute('PRAGMA page_count').fetchone()[0]
            page_size = conn.execute('PRAGMA page_size').fetchone()[0]
            overview['page_count'] = page_count
            overview['page_size'] = page_size
            overview['freelist_count'] = conn.execute('PRAGMA freelist_count').fetchone()[0]
    except sqlite3.Error:
        pass

    return overview


def get_service_overview() -> dict:
    from core.douyin.douyin_account_model import DouyinAccount
    from core.douyin.douyin_session_model import DouyinSession
    from env import CLIENT_DATA_DIR, CLIENT_HTTP_PORT, ENV

    accounts = list(
        DouyinAccount.objects.filter(is_deleted=False).values(
            'id', 'nickname', 'status', 'auto_reply_enabled', 'credential_state', 'reply_today'
        )
    )
    sessions = list(
        DouyinSession.objects.select_related('account').values(
            'account_id',
            'status',
            'worker_id',
            'last_heartbeat',
            'error_message',
            'messages_today',
            'replies_today',
            'errors_today',
        )[:20]
    )

    return {
        'env': ENV,
        'data_dir': CLIENT_DATA_DIR,
        'http_port': CLIENT_HTTP_PORT,
        'accounts_total': len(accounts),
        'accounts_auto_reply_on': sum(1 for a in accounts if a.get('auto_reply_enabled')),
        'accounts_online': sum(1 for a in accounts if a.get('status') == 1),
        'accounts': accounts,
        'sessions': sessions,
        'checked_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def get_admin_dashboard() -> dict:
    return {
        'service': get_service_overview(),
        'processes': get_process_overview(),
        'database': get_database_overview(),
    }


def emergency_stop(*, reason: str = '管理员急停') -> dict:
    from django.db import transaction
    from django.utils import timezone

    from core.douyin.douyin_account_model import DouyinAccount
    from core.douyin.douyin_message_model import DouyinMessage
    from core.douyin.douyin_worker_command_model import DouyinWorkerCommand
    from core.douyin.runtime import command_publisher

    stopped_accounts: list[str] = []
    cleared_commands = 0
    marked_messages = 0

    with transaction.atomic():
        accounts = list(DouyinAccount.objects.filter(is_deleted=False))
        for acc in accounts:
            acc.auto_reply_enabled = False
            acc.save(update_fields=['auto_reply_enabled', 'sys_update_datetime'])
            stopped_accounts.append(str(acc.id))
            command_publisher.send_session_control(str(acc.id), 'pause')

        pending_cmds = DouyinWorkerCommand.objects.filter(consumed_at__isnull=True)
        cleared_commands = pending_cmds.count()
        now = timezone.now()
        for cmd in pending_cmds:
            payload = dict(cmd.payload or {})
            payload['_result'] = {
                'status': 'failed',
                'error': reason,
                'emergency_stop': True,
            }
            cmd.payload = payload
            cmd.consumed_at = now
            cmd.save(update_fields=['payload', 'consumed_at', 'sys_update_datetime'])
            cleared_commands += 1

        marked_messages = DouyinMessage.objects.filter(
            direction='in',
            processed=False,
        ).update(processed=True)

    return {
        'ok': True,
        'message': reason,
        'accounts_stopped': len(stopped_accounts),
        'commands_cleared': cleared_commands,
        'messages_marked_processed': marked_messages,
        'stopped_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }
