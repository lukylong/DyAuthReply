# ************** Standalone 本地免 Docker 运行配置 ************** #
# 由 scripts/standalone/launcher.py 设置 ZQ_ENV=standalone 后加载。
# 目标：SQLite + 本机 Redis + 单 Worker，数据落在项目 data/standalone/ 目录。

import os
from pathlib import Path

from env.dev_env import *  # noqa: F401,F403


def _env(name: str, default: str = '') -> str:
    return os.environ.get(name, default)


# 项目根目录：backend-django/env/standalone_env.py → ../../..
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_ROOT = Path(_env('STANDALONE_DATA_DIR', str(_PROJECT_ROOT / 'data' / 'standalone')))

_DATA_ROOT.mkdir(parents=True, exist_ok=True)
(_DATA_ROOT / 'douyin').mkdir(parents=True, exist_ok=True)
(_DATA_ROOT / 'media' / 'file_manager').mkdir(parents=True, exist_ok=True)
(_DATA_ROOT / 'logs').mkdir(parents=True, exist_ok=True)

DATABASE_TYPE = 'SQLITE3'
DATABASE_SQLITE_PATH = _env('DATABASE_SQLITE_PATH', str(_DATA_ROOT / 'db.sqlite3'))

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

# 单进程部署：一个 worker 托管全部账号，无需 Redis 租约
DOUYIN_WORKER_SHARD_COUNT = int(_env('DOUYIN_WORKER_SHARD_COUNT', '1'))
DOUYIN_WORKER_SHARD_INDEX = int(_env('DOUYIN_WORKER_SHARD_INDEX', '0'))
DOUYIN_WORKER_LEASE_ENABLED = _env('DOUYIN_WORKER_LEASE_ENABLED', 'false').lower() == 'true'

# 本地模式默认开启 WS 入向（与 compose 开发一致）
DOUYIN_TRANSPORT_WS_INBOUND = _env('DOUYIN_TRANSPORT_WS_INBOUND', 'true').lower() == 'true'

# 文件存储走本地目录（data/standalone/media）
FILE_STORAGE_TYPE = _env('FILE_STORAGE_TYPE', 'local')

# 密钥由 launcher 写入 data/standalone/.env；此处仅作未设置时的兜底（首次启动前会被覆盖）
if not DOUYIN_STORAGE_ENCRYPTION_KEY:
    DOUYIN_STORAGE_ENCRYPTION_KEY = _env(
        'DOUYIN_STORAGE_ENCRYPTION_KEY',
        'CHANGE-ME-run-scripts-standalone-launcher-py',
    )
