#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DyAuthReply Standalone Bundle Launcher.
Starts both Django API (Uvicorn) and Douyin Worker in separate threads.
Suitable for PyInstaller bundling and Tauri sidecar packaging.
"""
from __future__ import annotations

import argparse
import atexit
import json
import multiprocessing
import os
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# Windows: set UTF-8 before any Chinese log output (cp936/cp1252 crash otherwise)
if sys.platform == 'win32':
    os.environ.setdefault('PYTHONUTF8', '1')
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

multiprocessing.freeze_support()

# Add backend-django to PYTHONPATH programmatically
if getattr(sys, 'frozen', False):
    ROOT = Path(sys._MEIPASS)
    REPO_ROOT = Path(sys._MEIPASS)
else:
    REPO_ROOT = Path(__file__).resolve().parents[2]
    ROOT = REPO_ROOT

BACKEND = ROOT / 'backend-django'
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(REPO_ROOT / 'dyauthreply-client' / 'launcher'))

# Set default settings
os.environ.setdefault('ZQ_ENV', 'client')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'application.settings')

# Parse arguments
parser = argparse.ArgumentParser(description='DyAuthReply client launcher')
parser.add_argument('--host', default='127.0.0.1')
parser.add_argument('--port', type=int, default=8765)
parser.add_argument('--data-dir', default='')
parser.add_argument('--no-worker', action='store_true')
args = parser.parse_args()

# Setup client data dir
if not args.data_dir:
    if sys.platform == 'darwin':
        data_dir = Path.home() / 'Library' / 'Application Support' / 'DyAuthReply'
    elif sys.platform == 'win32':
        appdata = os.environ.get('APPDATA', str(Path.home()))
        data_dir = Path(appdata) / 'DyAuthReply'
    else:
        data_dir = ROOT / 'data' / 'client'
else:
    data_dir = Path(args.data_dir).resolve()

data_dir.mkdir(parents=True, exist_ok=True)
(data_dir / 'douyin').mkdir(parents=True, exist_ok=True)
(data_dir / 'logs').mkdir(parents=True, exist_ok=True)

from launcher_logging import configure_windows_stdio, setup_launcher_logging
from defaults import load_launcher_defaults

configure_windows_stdio()
launcher_log = setup_launcher_logging(data_dir)
print(f'[launcher_bundled] log file: {launcher_log}', flush=True)
bundled_defaults = load_launcher_defaults(ROOT)

# Export environment variables so uvicorn and worker read them
os.environ['ZQ_ENV'] = 'client'
os.environ['DOUYIN_COMMAND_BACKEND'] = 'db'
os.environ['CLIENT_DATA_DIR'] = str(data_dir)
os.environ['CLIENT_HTTP_PORT'] = str(args.port)
os.environ['CLIENT_HTTP_HOST'] = args.host
if getattr(sys, 'frozen', False):
    os.environ.pop('PYTHONPATH', None)
else:
    os.environ['PYTHONPATH'] = str(BACKEND)

from node_runtime import configure_node_env
from instance_lock import (
    _clear_stale_lock,
    _pid_alive,
    _read_lock_pid,
    acquire_instance_lock,
)
from core.client.lifecycle import _terminate_pid


def _port_listening(host: str, port: int) -> bool:
    """端口是否已有进程在监听。"""
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def _is_our_client_api(host: str, port: int) -> bool:
    """占用端口的是否为本客户端 API（用于区分旧实例残留与其它程序占用）。"""
    url = f'http://{host}:{port}/api/client/v1/health'
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            if resp.status != 200:
                return False
            data = json.loads(resp.read().decode('utf-8'))
            return bool(data.get('ok')) and data.get('service') == 'dyauthreply-client'
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        return False


def _preempt_stale_instance(data_dir: Path, host: str, port: int) -> None:
    """启动 API 前抢占端口/锁。

    - 端口空闲：直接返回。
    - 端口被本客户端旧实例占用（更新覆盖安装等场景的残留）：读 launcher.lock 持锁 PID
      终止旧实例，轮询等待端口释放并清理 stale 锁后返回。
    - 端口被其它程序占用：打印明确错误并退出（不静默失败）。
    """
    if not _port_listening(host, port):
        return

    if not _is_our_client_api(host, port):
        print(
            f'[launcher_bundled] 错误: {host}:{port} 已被其它程序占用，无法启动后端 API。'
            ' 请关闭占用该端口的程序后重试。',
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)

    print(
        f'[launcher_bundled] 检测到旧实例仍在 {host}:{port} 运行，开始抢占...',
        flush=True,
    )

    lock_path = data_dir / 'launcher.lock'
    holder_pid = _read_lock_pid(lock_path)
    if holder_pid and holder_pid != os.getpid() and _pid_alive(holder_pid):
        print(f'[launcher_bundled] 终止旧实例进程树 PID={holder_pid}', flush=True)
        _terminate_pid(holder_pid)
    else:
        print(
            '[launcher_bundled] 警告: 未能从 launcher.lock 读取到有效的旧实例 PID，'
            '仅等待端口自行释放。',
            file=sys.stderr,
            flush=True,
        )

    deadline = time.time() + 15.0
    while time.time() < deadline:
        if not _port_listening(host, port):
            print('[launcher_bundled] 旧实例已退出，端口已释放。', flush=True)
            _clear_stale_lock(lock_path)
            return
        time.sleep(0.5)

    print(
        f'[launcher_bundled] 错误: 等待旧实例释放 {host}:{port} 超时（15s）。'
        f' 请手动结束旧进程或删除 {lock_path} 后重试。',
        file=sys.stderr,
        flush=True,
    )
    sys.exit(1)


_preempt_stale_instance(data_dir, args.host, args.port)

_instance_lock = acquire_instance_lock(data_dir)
if _instance_lock is None:
    print(
        '[launcher_bundled] 已有 DyAuthReply 实例在运行，请勿重复启动。',
        file=sys.stderr,
        flush=True,
    )
    sys.exit(1)

_node_bin = configure_node_env(app_root=REPO_ROOT if not getattr(sys, 'frozen', False) else None)
if _node_bin:
    print(f'[launcher_bundled] Node.js -> {_node_bin}', flush=True)
else:
    print(
        '[launcher_bundled] 警告: 未找到 Node.js，抖音签名/收消息将不可用。'
        '请重新打包 launcher（需本机构建机已安装 Node.js 18+）。',
        file=sys.stderr,
        flush=True,
    )

# Set up cryptography key
env_file = data_dir / '.env'
env_lines = env_file.read_text(encoding='utf-8').splitlines() if env_file.is_file() else []


def _read_env_file_value(key: str) -> str:
    for line in env_lines:
        if line.startswith(f'{key}='):
            return line.split('=', 1)[1].strip().strip("'\"")
    return ''


if not os.environ.get('DOUYIN_STORAGE_ENCRYPTION_KEY'):
    key = None
    key = _read_env_file_value('DOUYIN_STORAGE_ENCRYPTION_KEY') or None
    if not key:
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()
        env_file.write_text(f'DOUYIN_STORAGE_ENCRYPTION_KEY={key}\n', encoding='utf-8')
    os.environ['DOUYIN_STORAGE_ENCRYPTION_KEY'] = key

if not os.environ.get('LICENSE_LEASE_PUBLIC_KEY_B64'):
    lease_public_key = _read_env_file_value('LICENSE_LEASE_PUBLIC_KEY_B64')
    if not lease_public_key:
        lease_public_key = str(bundled_defaults.get('license_lease_public_key_b64') or '').strip()
    if lease_public_key:
        os.environ['LICENSE_LEASE_PUBLIC_KEY_B64'] = lease_public_key

if not os.environ.get('LICENSE_LEASE_PUBLIC_KEY'):
    lease_public_key_pem = _read_env_file_value('LICENSE_LEASE_PUBLIC_KEY')
    if not lease_public_key_pem:
        lease_public_key_pem = str(bundled_defaults.get('license_lease_public_key') or '').strip()
    if lease_public_key_pem:
        os.environ['LICENSE_LEASE_PUBLIC_KEY'] = lease_public_key_pem

if not os.environ.get('CLIENT_LICENSE_SERVER_URL'):
    default_license_server = _read_env_file_value('CLIENT_LICENSE_SERVER_URL')
    if not default_license_server:
        default_license_server = str(bundled_defaults.get('client_license_server_url') or '').strip()
    if default_license_server:
        os.environ['CLIENT_LICENSE_SERVER_URL'] = default_license_server

if not os.environ.get('CLIENT_APP_VERSION'):
    app_version = _read_env_file_value('CLIENT_APP_VERSION')
    if not app_version:
        app_version = str(bundled_defaults.get('client_app_version') or '').strip()
    if app_version:
        os.environ['CLIENT_APP_VERSION'] = app_version


def _shutdown_bundled(*_args: object) -> None:
    print('[launcher_bundled] 正在退出...', flush=True)
    os._exit(0)


atexit.register(_shutdown_bundled)
for sig in (signal.SIGINT, signal.SIGTERM):
    try:
        signal.signal(sig, _shutdown_bundled)
    except (ValueError, OSError):
        pass

# Run migrations and setup Django (imports must happen AFTER env is set)
import django

django.setup()

# Fix database migration issues before running migrations
from django.db import connection


def fix_worker_command_migration():
    """修复 DouyinWorkerCommand 迁移冲突"""
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='core_douyin_worker_command'
            """)
            if not cursor.fetchone():
                return

            cursor.execute("PRAGMA table_info(core_douyin_worker_command)")
            columns = {row[1] for row in cursor.fetchall()}
            has_root_fields = 'sys_creator_id' in columns

            cursor.execute("""
                SELECT id FROM django_migrations
                WHERE app='core' AND name='0022_douyin_worker_command_root_fields'
            """)
            migration_applied = cursor.fetchone() is not None

            if has_root_fields and not migration_applied:
                print("[launcher_bundled] 修复迁移记录: 0022", flush=True)
                cursor.execute("""
                    INSERT INTO django_migrations (app, name, applied)
                    VALUES ('core', '0022_douyin_worker_command_root_fields', datetime('now'))
                """)
            elif not has_root_fields and migration_applied:
                print("[launcher_bundled] 删除错误的迁移记录: 0022", flush=True)
                cursor.execute("""
                    DELETE FROM django_migrations
                    WHERE app='core' AND name='0022_douyin_worker_command_root_fields'
                """)
    except Exception as e:
        print(f"[launcher_bundled] 迁移修复失败: {e}", file=sys.stderr, flush=True)


fix_worker_command_migration()

from core.client.runtime_logs import ensure_runtime_log_files

ensure_runtime_log_files()

import start_client

print("[launcher_bundled] Preparing database on main thread...", flush=True)
start_client.prepare_database()
print("[launcher_bundled] Database ready.", flush=True)

from core.client.sign_probe import probe_sign_engine

sign_status = probe_sign_engine(force=True)
if sign_status.get('sign_js_ready'):
    print(f"[launcher_bundled] JS sign engine ready (node={sign_status.get('node_bin')})", flush=True)
else:
    print(
        "[launcher_bundled] 警告: JS 签名引擎不可用，扫描收件箱/自动回复/手动发送将失败。"
        f" 原因: {sign_status.get('sign_js_detail')}",
        file=sys.stderr,
        flush=True,
    )


def run_api():
    print("[launcher_bundled] Starting Django API server...", flush=True)
    try:
        start_client.serve()
    except Exception as e:
        print(f"[launcher_bundled] Error in API Server: {e}", file=sys.stderr, flush=True)
        import traceback
        traceback.print_exc()


def run_worker():
    print("[launcher_bundled] Starting Douyin Worker loop...", flush=True)
    try:
        import start_douyin_worker
        start_douyin_worker.main()
    except Exception as e:
        print(f"[launcher_bundled] Error in Douyin Worker: {e}", file=sys.stderr, flush=True)


if __name__ == '__main__':
    api_thread = threading.Thread(target=run_api, daemon=True, name='dyauthreply-api')
    api_thread.start()

    health_url = f'http://{args.host}:{args.port}/api/client/v1/health'
    print(f"[launcher_bundled] Waiting for API to be ready at {health_url}...", flush=True)

    api_ready = False
    for _ in range(180):
        try:
            import urllib.request
            with urllib.request.urlopen(health_url, timeout=1) as resp:
                if resp.status == 200:
                    api_ready = True
                    break
        except Exception:
            pass
        time.sleep(0.5)

    if not api_ready:
        print(
            "[launcher_bundled] Error: API server failed to start or respond. "
            f"See log: {launcher_log}",
            flush=True,
        )
        sys.exit(1)

    print("[launcher_bundled] API Server is ready!", flush=True)

    if not args.no_worker:
        worker_thread = threading.Thread(target=run_worker, daemon=True, name='dyauthreply-worker')
        worker_thread.start()

    print("[launcher_bundled] All services started. Running...", flush=True)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[launcher_bundled] Exiting...", flush=True)
        sys.exit(0)
