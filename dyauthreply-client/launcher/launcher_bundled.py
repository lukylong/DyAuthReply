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
else:
    ROOT = Path(__file__).resolve().parents[2]

BACKEND = ROOT / 'backend-django'
sys.path.insert(0, str(BACKEND))

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
