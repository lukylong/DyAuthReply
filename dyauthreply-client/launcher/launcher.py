#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DyAuthReply 桌面客户端启动器（Mac / Windows / Linux 开发态）。

启动顺序：migrate → API → Douyin Worker
（客户端模式使用 SQLite 命令队列，无需 Redis / Postgres / Docker）

用法（项目根目录）：
  python3 dyauthreply-client/launcher/launcher.py
  python3 dyauthreply-client/launcher/launcher.py --no-worker   # 仅 API
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'backend-django'
CLIENT_UI = ROOT / 'dyauthreply-client' / 'client-ui'
DEFAULT_DATA = ROOT / 'data' / 'client'

PROCS: list[subprocess.Popen] = []
DOCKER_REDIS_NAME = 'dyauthreply-redis'
_docker_redis_owned = False


def _log(msg: str) -> None:
    print(f'[launcher] {msg}', flush=True)


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def _wait_http(url: str, timeout_s: float = 90.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.5)
    return False


def _wait_port(host: str, port: int, timeout_s: float = 30.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _port_open(host, port):
            return True
        time.sleep(0.3)
    return False


def _find_redis_server() -> str | None:
    bundled = ROOT / 'dyauthreply-client' / 'launcher' / 'runtime' / 'redis'
    if sys.platform == 'win32':
        candidates = [
            bundled / 'redis-server.exe',
            Path(r'C:\Program Files\Redis\redis-server.exe'),
        ]
    else:
        candidates = [
            bundled / 'redis-server',
            Path('/opt/homebrew/bin/redis-server'),
            Path('/opt/homebrew/opt/redis/bin/redis-server'),
            Path('/usr/local/bin/redis-server'),
            Path('/usr/local/opt/redis/bin/redis-server'),
        ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    from shutil import which

    for name in ('redis-server', 'redis-server.exe'):
        found = which(name)
        if found:
            return found
    return None


def _docker_available() -> bool:
    try:
        proc = subprocess.run(
            ['docker', 'info'],
            capture_output=True,
            timeout=8,
            check=False,
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _start_redis_docker(redis_port: int) -> bool:
    global _docker_redis_owned
    if not _docker_available():
        return False

    for name in (DOCKER_REDIS_NAME, 'zq-redis'):
        proc = subprocess.run(
            ['docker', 'start', name],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode == 0:
            _log(f'已启动已有 Docker Redis 容器: {name}')
            if _wait_port('127.0.0.1', redis_port):
                return True
            _log(f'容器 {name} 已启动，但 {redis_port} 端口仍不可用')

    _log(f'通过 Docker 启动 Redis → 127.0.0.1:{redis_port}')
    proc = subprocess.run(
        [
            'docker',
            'run',
            '-d',
            '--name',
            DOCKER_REDIS_NAME,
            '-p',
            f'{redis_port}:6379',
            'redis:7-alpine',
            'redis-server',
            '--save',
            '',
            '--appendonly',
            'no',
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or '').strip()
        if 'already in use' in err or 'Conflict' in err:
            subprocess.run(['docker', 'start', DOCKER_REDIS_NAME], check=False, timeout=30)
        else:
            _log(f'Docker Redis 启动失败: {err or "unknown error"}')
            return False
    _docker_redis_owned = True
    if _wait_port('127.0.0.1', redis_port):
        _log(f'Redis 就绪 (Docker) → 127.0.0.1:{redis_port}')
        return True
    _log(f'Docker Redis 已启动，但端口 {redis_port} 未就绪')
    return False


def _stop_owned_docker_redis() -> None:
    global _docker_redis_owned
    if not _docker_redis_owned:
        return
    _log(f'停止 Docker Redis 容器: {DOCKER_REDIS_NAME}')
    subprocess.run(['docker', 'stop', DOCKER_REDIS_NAME], capture_output=True, timeout=20, check=False)
    subprocess.run(['docker', 'rm', DOCKER_REDIS_NAME], capture_output=True, timeout=20, check=False)
    _docker_redis_owned = False


def _ensure_env(args: argparse.Namespace) -> dict[str, str]:
    data_dir = Path(args.data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / 'douyin').mkdir(parents=True, exist_ok=True)
    (data_dir / 'logs').mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env['ZQ_ENV'] = 'client'
    env['DOUYIN_COMMAND_BACKEND'] = 'db'
    env['CLIENT_DATA_DIR'] = str(data_dir)
    env['CLIENT_HTTP_PORT'] = str(args.port)
    env['CLIENT_HTTP_HOST'] = args.host
    env['CLIENT_UI_DIST'] = str((CLIENT_UI / 'dist').resolve())
    env['REDIS_HOST'] = args.redis_host
    env['REDIS_PORT'] = str(args.redis_port)
    env['PYTHONPATH'] = str(BACKEND)

    launcher_dir = ROOT / 'dyauthreply-client' / 'launcher'
    if str(launcher_dir) not in sys.path:
        sys.path.insert(0, str(launcher_dir))
    from node_runtime import configure_node_env

    node = configure_node_env(app_root=ROOT)
    if node:
        env['DOUYIN_NODE_BIN'] = node
        env.setdefault('EXECJS_RUNTIME', 'Node')
        _log(f'Node.js → {node}')
    else:
        _log('警告: 未找到 Node.js，抖音签名/收消息将不可用')

    env_file = data_dir / '.env'
    if not env.get('DOUYIN_STORAGE_ENCRYPTION_KEY'):
        if env_file.is_file():
            for line in env_file.read_text(encoding='utf-8').splitlines():
                if line.startswith('DOUYIN_STORAGE_ENCRYPTION_KEY='):
                    env['DOUYIN_STORAGE_ENCRYPTION_KEY'] = line.split('=', 1)[1].strip().strip("'\"")
        else:
            from cryptography.fernet import Fernet

            key = Fernet.generate_key().decode()
            env_file.write_text(f'DOUYIN_STORAGE_ENCRYPTION_KEY={key}\n', encoding='utf-8')
            env['DOUYIN_STORAGE_ENCRYPTION_KEY'] = key
            _log(f'已生成 DOUYIN_STORAGE_ENCRYPTION_KEY → {env_file}')

    return env


def _spawn(cmd: list[str], env: dict[str, str], cwd: Path, name: str) -> subprocess.Popen:
    _log(f'启动 {name}: {" ".join(cmd)}')
    popen_kwargs: dict = {
        'cwd': str(cwd),
        'env': env,
        'stdout': sys.stdout,
        'stderr': sys.stderr,
    }
    if sys.platform == 'win32':
        popen_kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        popen_kwargs['start_new_session'] = True
    proc = subprocess.Popen(cmd, **popen_kwargs)
    PROCS.append(proc)
    return proc


def _shutdown() -> None:
    for proc in reversed(PROCS):
        if proc.poll() is None:
            if sys.platform == 'win32':
                subprocess.run(
                    ['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
            else:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
    PROCS.clear()
    _stop_owned_docker_redis()


def _start_redis(env: dict[str, str], redis_port: int) -> None:
    if env.get('DOUYIN_COMMAND_BACKEND', 'db') == 'db':
        _log('客户端模式：使用 SQLite 命令队列，无需 Redis')
        return
    if _port_open('127.0.0.1', redis_port):
        _log(f'Redis 已在 127.0.0.1:{redis_port} 运行，跳过启动')
        return
    redis_bin = _find_redis_server()
    if redis_bin:
        data_dir = Path(env['CLIENT_DATA_DIR'])
        conf = data_dir / 'redis.conf'
        conf.write_text(
            f'port {redis_port}\n'
            'bind 127.0.0.1\n'
            'daemonize no\n'
            'save ""\n'
            'appendonly no\n',
            encoding='utf-8',
        )
        _spawn([redis_bin, str(conf)], env, BACKEND, 'redis')
        if _wait_port('127.0.0.1', redis_port):
            return
        _log('本地 redis-server 已启动，但端口未就绪')
    if _start_redis_docker(redis_port):
        return
    _log('未找到 redis-server，且 Docker Redis 启动失败。')
    _log('任选其一：')
    _log('  macOS: brew install redis && brew services start redis')
    _log('  Docker: docker start zq-redis  或  docker run -p 6379:6379 redis:7-alpine')
    _log('  内置: 将 redis-server 放入 launcher/runtime/redis/')
    sys.exit(1)


def _probe_client_api(host: str, port: int) -> bool:
    url = f'http://{host}:{port}/api/client/v1/health'
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            if resp.status != 200:
                return False
            data = json.loads(resp.read().decode('utf-8'))
            return bool(data.get('ok')) and data.get('service') == 'dyauthreply-client'
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        return False


def _start_api(env: dict[str, str], args: argparse.Namespace) -> subprocess.Popen | None:
    host, port = args.host, args.port
    if _port_open(host, port):
        if _probe_client_api(host, port):
            _log(f'端口 {port} 已有 DyAuthReply API 在运行，跳过重复启动')
            return None
        _log(f'错误：{host}:{port} 已被其他程序占用，无法启动 API')
        _log(f'  释放端口: lsof -ti :{port} | xargs kill')
        sys.exit(1)

    api_cmd = [args.python, str(BACKEND / 'start_client.py')]
    return _spawn(api_cmd, env, BACKEND, 'api')


def main() -> int:
    parser = argparse.ArgumentParser(description='DyAuthReply client launcher')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--redis-host', default='127.0.0.1')
    parser.add_argument('--redis-port', type=int, default=6379)
    parser.add_argument('--data-dir', default=str(DEFAULT_DATA))
    parser.add_argument('--no-worker', action='store_true')
    parser.add_argument('--python', default=sys.executable)
    args = parser.parse_args()

    if not BACKEND.is_dir():
        _log(f'找不到 backend: {BACKEND}')
        return 1

    env = _ensure_env(args)

    data_dir = Path(args.data_dir).resolve()
    from instance_lock import acquire_instance_lock

    _instance_lock = acquire_instance_lock(data_dir)
    if _instance_lock is None:
        _log('已有 DyAuthReply 实例在运行，请勿重复启动')
        return 1

    atexit.register(_shutdown)
    signal.signal(signal.SIGINT, lambda *_: (_shutdown(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda *_: (_shutdown(), sys.exit(0)))

    _start_redis(env, args.redis_port)

    api_proc = _start_api(env, args)

    health_url = f'http://{args.host}:{args.port}/api/client/v1/health'
    if api_proc is not None:
        if not _wait_http(health_url):
            _log('API 健康检查超时')
            _shutdown()
            return 1
    elif not _probe_client_api(args.host, args.port):
        _log('API 未就绪')
        _shutdown()
        return 1
    _log(f'API 就绪 → {health_url}')
    _log(f'管理界面 → http://{args.host}:{args.port}/app/')

    if not args.no_worker:
        worker_cmd = [args.python, str(BACKEND / 'start_douyin_worker.py')]
        _spawn(worker_cmd, env, BACKEND, 'douyin-worker')

    _log('全部服务已启动。Ctrl+C 退出。')
    try:
        while True:
            if api_proc is not None and api_proc.poll() is not None:
                _log(f'API 进程已退出 code={api_proc.returncode}')
                break
            time.sleep(1)
    finally:
        _shutdown()
    return (api_proc.returncode if api_proc is not None else 0) or 0


if __name__ == '__main__':
    raise SystemExit(main())
