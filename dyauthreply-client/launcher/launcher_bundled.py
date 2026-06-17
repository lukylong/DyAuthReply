#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DyAuthReply Standalone Bundle Launcher.
Starts both Django API (Uvicorn) and Douyin Worker in separate threads.
Suitable for PyInstaller bundling and Tauri sidecar packaging.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

# Add backend-django to PYTHONPATH programmatically
if getattr(sys, 'frozen', False):
    ROOT = Path(sys._MEIPASS)
    REPO_ROOT = Path(__file__).resolve().parents[2]
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

# Export environment variables so uvicorn and worker read them
os.environ['ZQ_ENV'] = 'client'
os.environ['DOUYIN_COMMAND_BACKEND'] = 'db'
os.environ['CLIENT_DATA_DIR'] = str(data_dir)
os.environ['CLIENT_HTTP_PORT'] = str(args.port)
os.environ['CLIENT_HTTP_HOST'] = args.host
os.environ['PYTHONPATH'] = str(BACKEND)

from node_runtime import configure_node_env
from instance_lock import acquire_instance_lock

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
    print(f'[launcher_bundled] Node.js → {_node_bin}', flush=True)
else:
    print(
        '[launcher_bundled] 警告: 未找到 Node.js，抖音签名/收消息将不可用。'
        '请重新打包 launcher（需本机构建机已安装 Node.js 18+）。',
        file=sys.stderr,
        flush=True,
    )

# Set up cryptography key
env_file = data_dir / '.env'
if not os.environ.get('DOUYIN_STORAGE_ENCRYPTION_KEY'):
    key = None
    if env_file.is_file():
        for line in env_file.read_text(encoding='utf-8').splitlines():
            if line.startswith('DOUYIN_STORAGE_ENCRYPTION_KEY='):
                key = line.split('=', 1)[1].strip().strip("'\"")
    if not key:
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()
        env_file.write_text(f'DOUYIN_STORAGE_ENCRYPTION_KEY={key}\n', encoding='utf-8')
    os.environ['DOUYIN_STORAGE_ENCRYPTION_KEY'] = key

# Run migrations and setup Django (imports must happen AFTER env is set)
import django
django.setup()

# Fix database migration issues before running migrations
from django.db import connection

def fix_worker_command_migration():
    """修复 DouyinWorkerCommand 迁移冲突"""
    try:
        with connection.cursor() as cursor:
            # 检查表是否存在
            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='core_douyin_worker_command'
            """)
            if not cursor.fetchone():
                return  # 表不存在，首次运行，无需修复

            # 检查字段
            cursor.execute("PRAGMA table_info(core_douyin_worker_command)")
            columns = {row[1] for row in cursor.fetchall()}
            has_root_fields = 'sys_creator_id' in columns

            # 检查迁移记录
            cursor.execute("""
                SELECT id FROM django_migrations
                WHERE app='core' AND name='0022_douyin_worker_command_root_fields'
            """)
            migration_applied = cursor.fetchone() is not None

            if has_root_fields and not migration_applied:
                # 字段已存在（0021 创建时就带了），但迁移未记录
                print("[launcher_bundled] 修复迁移记录: 0022", flush=True)
                cursor.execute("""
                    INSERT INTO django_migrations (app, name, applied)
                    VALUES ('core', '0022_douyin_worker_command_root_fields', datetime('now'))
                """)
            elif not has_root_fields and migration_applied:
                # 迁移已记录，但字段不存在
                print("[launcher_bundled] 删除错误的迁移记录: 0022", flush=True)
                cursor.execute("""
                    DELETE FROM django_migrations
                    WHERE app='core' AND name='0022_douyin_worker_command_root_fields'
                """)
    except Exception as e:
        print(f"[launcher_bundled] 迁移修复失败: {e}", file=sys.stderr, flush=True)

fix_worker_command_migration()

# Import start_client migration logic and uvicorn runner
import start_client

# Define thread functions
def run_api():
    print("[launcher_bundled] Starting Django API server...", flush=True)
    try:
        start_client.main()
    except Exception as e:
        print(f"[launcher_bundled] Error in API Server: {e}", file=sys.stderr, flush=True)

def run_worker():
    print("[launcher_bundled] Starting Douyin Worker loop...", flush=True)
    try:
        import start_douyin_worker
        start_douyin_worker.main()
    except Exception as e:
        print(f"[launcher_bundled] Error in Douyin Worker: {e}", file=sys.stderr, flush=True)

if __name__ == '__main__':
    # Start API in a background thread
    api_thread = threading.Thread(target=run_api, daemon=True)
    api_thread.start()

    # Wait for API to be ready (health check)
    health_url = f'http://{args.host}:{args.port}/api/client/v1/health'
    print(f"[launcher_bundled] Waiting for API to be ready at {health_url}...", flush=True)
    
    # Simple wait http loop
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
        print("[launcher_bundled] Error: API server failed to start or respond.", flush=True)
        sys.exit(1)

    print("[launcher_bundled] API Server is ready!", flush=True)

    # Start Worker in another thread if not disabled
    if not args.no_worker:
        worker_thread = threading.Thread(target=run_worker, daemon=True)
        worker_thread.start()

    print("[launcher_bundled] All services started. Running...", flush=True)
    
    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[launcher_bundled] Exiting...", flush=True)
        sys.exit(0)
