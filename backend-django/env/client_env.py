# ************** DyAuthReply 桌面客户端运行配置 ************** #
# launcher / start_client.py 设置 ZQ_ENV=client 后加载。
# SQLite 单库 + SQLite 命令队列 + 单 Worker；用户无需安装 Redis / Postgres。

import os
import sys
from pathlib import Path

from env.dev_env import *  # noqa: F401,F403


def _env(name: str, default: str = '') -> str:
    return os.environ.get(name, default)


def _default_client_data_dir() -> Path:
    explicit = _env('CLIENT_DATA_DIR', '')
    if explicit:
        return Path(explicit)
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'DyAuthReply'
    if sys.platform == 'win32':
        appdata = os.environ.get('APPDATA', str(Path.home()))
        return Path(appdata) / 'DyAuthReply'
    return Path(__file__).resolve().parent.parent.parent / 'data' / 'client'


_DATA_ROOT = _default_client_data_dir()
_DATA_ROOT.mkdir(parents=True, exist_ok=True)
(_DATA_ROOT / 'douyin').mkdir(parents=True, exist_ok=True)
(_DATA_ROOT / 'media' / 'file_manager').mkdir(parents=True, exist_ok=True)
(_DATA_ROOT / 'logs').mkdir(parents=True, exist_ok=True)

DATABASE_TYPE = 'SQLITE3'
DATABASE_SQLITE_PATH = _env('DATABASE_SQLITE_PATH', str(_DATA_ROOT / 'db.sqlite3'))

# 客户端：API → Worker 走 SQLite 命令表，不依赖 Redis
DOUYIN_COMMAND_BACKEND = _env('DOUYIN_COMMAND_BACKEND', 'db')

# Django 缓存 / WebSocket 层改内存后端（客户端不跑 Celery Beat）
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'dyauthreply-client',
    }
}
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    }
}

REDIS_HOST = _env('REDIS_HOST', '127.0.0.1')
REDIS_PORT = int(_env('REDIS_PORT', '6379'))
REDIS_DB = _env('REDIS_DB', '2')
REDIS_PASSWORD = _env('REDIS_PASSWORD', '')
if REDIS_PASSWORD:
    _default_redis_url = f'redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}'
else:
    _default_redis_url = f'redis://{REDIS_HOST}:{REDIS_PORT}'
REDIS_URL = _env('REDIS_URL', _default_redis_url)

DOUYIN_DATA_DIR = _env('DOUYIN_DATA_DIR', str(_DATA_ROOT / 'douyin'))

DOUYIN_WORKER_SHARD_COUNT = int(_env('DOUYIN_WORKER_SHARD_COUNT', '1'))
DOUYIN_WORKER_SHARD_INDEX = int(_env('DOUYIN_WORKER_SHARD_INDEX', '0'))
DOUYIN_WORKER_LEASE_ENABLED = _env('DOUYIN_WORKER_LEASE_ENABLED', 'false').lower() == 'true'
DOUYIN_TRANSPORT_WS_INBOUND = _env('DOUYIN_TRANSPORT_WS_INBOUND', 'false').lower() == 'true'

# Worker 空闲轮询（WS 不可用时 HTTP 兜底；客户端默认更快）
DOUYIN_WORKER_IDLE_POLL_MIN = int(_env('DOUYIN_WORKER_IDLE_POLL_MIN', '8'))
DOUYIN_WORKER_IDLE_POLL_MAX = int(_env('DOUYIN_WORKER_IDLE_POLL_MAX', '15'))
DOUYIN_WS_HTTP_FALLBACK_INTERVAL = float(_env('DOUYIN_WS_HTTP_FALLBACK_INTERVAL', '25'))

# 客户端历史补扫限流（避免首次启动一次性落库数百条导致卡顿）
DOUYIN_CLIENT_BACKFILL_MAX_CONVERSATIONS = int(
    _env('DOUYIN_CLIENT_BACKFILL_MAX_CONVERSATIONS', '20')
)
DOUYIN_CLIENT_BACKFILL_MAX_MESSAGES = int(_env('DOUYIN_CLIENT_BACKFILL_MAX_MESSAGES', '12'))

FILE_STORAGE_TYPE = _env('FILE_STORAGE_TYPE', 'local')
FILE_STORAGE_LOCAL_PATH = str(_DATA_ROOT / 'media' / 'file_manager')

CLIENT_DATA_DIR = str(_DATA_ROOT)

# 客户端日志写入用户数据目录（打包后 _MEIPASS 不可持久化）
SERVER_LOGS_FILE = str(_DATA_ROOT / 'logs' / 'server.log')
ERROR_LOGS_FILE = str(_DATA_ROOT / 'logs' / 'error.log')

# 客户端 API 默认端口（launcher 可覆盖 CLIENT_HTTP_PORT）
CLIENT_HTTP_PORT = int(_env('CLIENT_HTTP_PORT', '8765'))

# 客户端默认授权服务地址（开发态默认本地；打包产物由 launcher defaults / 外部环境覆盖）
CLIENT_LICENSE_SERVER_URL = _env('CLIENT_LICENSE_SERVER_URL', 'http://127.0.0.1:8000')

# 仅挂载客户端 URL（API + client-ui 静态资源）
ROOT_URLCONF = 'application.client_urls'

# 演示模式关闭，允许写操作
IS_DEMO = False

# 客户端内置 UI 静态目录（开发/打包后由 launcher 设置绝对路径）
CLIENT_UI_DIST = _env(
    'CLIENT_UI_DIST',
    str(Path(__file__).resolve().parent.parent.parent / 'dyauthreply-client' / 'client-ui' / 'dist'),
)
