# ************** 默认数据库 配置  ************** #
# ================================================= #
# 数据库类型 MYSQL/SQLSERVER/SQLITE3/POSTGRESQL
#
# 所有字段都支持从环境变量读取，便于 docker-compose / Kubernetes 等场景覆盖；
# 未设置环境变量时回退到本地开发默认值。
import os


def _env(name: str, default: str = '') -> str:
    return os.environ.get(name, default)


DATABASE_TYPE = _env('DATABASE_TYPE', 'SQLITE3')
DATABASE_HOST = _env('DATABASE_HOST', 'django-ninja.zq-platform.cn')
DATABASE_PORT = int(_env('DATABASE_PORT', '5323'))
DATABASE_USER = _env('DATABASE_USER', _env('DEV_DB_USER', ''))
DATABASE_PASSWORD = _env('DATABASE_PASSWORD', _env('DEV_DB_PASSWORD', ''))
DATABASE_NAME = _env('DATABASE_NAME', 'zq-platform')

# ================================================= #
# ******** redis配置  *********** #
# ================================================= #
REDIS_PASSWORD = _env('REDIS_PASSWORD', '')
REDIS_HOST = _env('REDIS_HOST', '127.0.0.1')
REDIS_PORT = int(_env('REDIS_PORT', '6379'))
REDIS_DB = _env('REDIS_DB', '2')
if REDIS_PASSWORD:
    _default_redis_url = f'redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}'
else:
    _default_redis_url = f'redis://{REDIS_HOST}:{REDIS_PORT}'
REDIS_URL = _env('REDIS_URL', _default_redis_url)

# # ================================================= #
# # ******************** JWT 配置 ***************** #
# # ================================================= #

# JWT 密钥从环境变量读取
JWT_ACCESS_SECRET_KEY = os.environ.get(
    'JWT_ACCESS_SECRET_KEY',
    'default-access-secret-key-change-in-production'
)
JWT_REFRESH_SECRET_KEY = os.environ.get(
    'JWT_REFRESH_SECRET_KEY',
    'default-refresh-secret-key-change-in-production'
)

# ================================================= #
# ******** 其他配置 *********** #
# ================================================= #
IS_DEMO = False

ENABLE_SCHEDULER = _env('ENABLE_SCHEDULER', 'True').lower() == 'true'

# ================================================= #
# ******** OAuth 配置 *********** #
# ================================================= #

# 是否给OAuth登陆用户授予管理员权限（生产环境最好不要这样做）
GRANT_ADMIN_TO_OAUTH_USER = True

# Gitee OAuth
GITEE_CLIENT_ID = os.environ.get('GITEE_CLIENT_ID', 'your-gitee-client-id')
GITEE_CLIENT_SECRET = os.environ.get('GITEE_CLIENT_SECRET', 'your-gitee-client-secret')
# 注意：前端端口是 5777，回调路径是 /oauth/gitee/callback
GITEE_REDIRECT_URI = os.environ.get('GITEE_REDIRECT_URI', 'http://localhost:5777/oauth/gitee/callback')

# GitHub OAuth
GITHUB_CLIENT_ID = os.environ.get('GITHUB_CLIENT_ID', 'your-github-client-id')
GITHUB_CLIENT_SECRET = os.environ.get('GITHUB_CLIENT_SECRET', 'your-github-client-secret')
GITHUB_REDIRECT_URI = os.environ.get('GITHUB_REDIRECT_URI', 'http://localhost:5777/oauth/github/callback')

# QQ 互联 OAuth
QQ_APP_ID = os.environ.get('QQ_APP_ID', 'your-qq-app-id')
QQ_APP_KEY = os.environ.get('QQ_APP_KEY', 'your-qq-app-key')
QQ_REDIRECT_URI = os.environ.get('QQ_REDIRECT_URI', 'http://localhost:5777/oauth/qq/callback')

# Google OAuth
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', 'your-google-client-id')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', 'your-google-client-secret')
GOOGLE_REDIRECT_URI = os.environ.get('GOOGLE_REDIRECT_URI', 'http://localhost:5777/oauth/google/callback')

# 微信开放平台 OAuth
WECHAT_APP_ID = os.environ.get('WECHAT_APP_ID', 'your-wechat-app-id')
WECHAT_APP_SECRET = os.environ.get('WECHAT_APP_SECRET', 'your-wechat-app-secret')
WECHAT_REDIRECT_URI = os.environ.get('WECHAT_REDIRECT_URI', 'http://localhost:5777/oauth/wechat/callback')

# Microsoft OAuth
MICROSOFT_CLIENT_ID = os.environ.get('MICROSOFT_CLIENT_ID', 'your-microsoft-client-id')
MICROSOFT_CLIENT_SECRET = os.environ.get('MICROSOFT_CLIENT_SECRET', 'your-microsoft-client-secret')
MICROSOFT_REDIRECT_URI = os.environ.get('MICROSOFT_REDIRECT_URI', 'http://localhost:5777/oauth/microsoft/callback')

# 钉钉 OAuth
DINGTALK_APP_ID = os.environ.get('DINGTALK_APP_ID', 'your-dingtalk-app-id')
DINGTALK_APP_SECRET = os.environ.get('DINGTALK_APP_SECRET', 'your-dingtalk-app-secret')
DINGTALK_REDIRECT_URI = os.environ.get('DINGTALK_REDIRECT_URI', 'http://localhost:5777/oauth/dingtalk/callback')

# 飞书 OAuth
FEISHU_APP_ID = os.environ.get('FEISHU_APP_ID', 'your-feishu-app-id')
FEISHU_APP_SECRET = os.environ.get('FEISHU_APP_SECRET', 'your-feishu-app-secret')
FEISHU_REDIRECT_URI = os.environ.get('FEISHU_REDIRECT_URI', 'http://localhost:5777/oauth/feishu/callback')

# ================================================= #
# ******** 邮件配置 *********** #
# ================================================= #
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.qq.com')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True').lower() == 'true'
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')  # 授权码
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', EMAIL_HOST_USER)

# ================================================= #
# ******** AI 平台配置 *********** #
# ================================================= #

# OpenAI
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
OPENAI_API_BASE = os.environ.get('OPENAI_API_BASE', 'https://api.openai.com/v1')

# Anthropic Claude
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')

# 阿里通义千问
QWEN_API_KEY = os.environ.get('QWEN_API_KEY', '')

# 阿里云百炼 DashScope（语音识别/合成）
# 获取 API Key: https://help.aliyun.com/zh/model-studio/get-api-key
DASHSCOPE_API_KEY = os.environ.get('DASHSCOPE_API_KEY', 'sk-1c3799cb5acc455a9532e3800ddd0b41')

# Ollama (本地)
OLLAMA_HOST = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')

# ================================================= #
# ******** 抖音私信自动回复（core.douyin / M2+） *********** #
# ================================================= #
DOUYIN_DATA_DIR = _env('DOUYIN_DATA_DIR', '/var/lib/zq-platform/douyin')
DOUYIN_STORAGE_ENCRYPTION_KEY = _env('DOUYIN_STORAGE_ENCRYPTION_KEY', '')
DOUYIN_REDIS_CHANNEL_PREFIX = _env('DOUYIN_REDIS_CHANNEL_PREFIX', 'douyin')

# ---- 抖音 Transport ----
# WS 入向消息快路径：HttpProtocolTransport 被 FrontierWsDecorator 包一层，监听 IM
# WebSocket 帧 → 解 Frontier protobuf → 命中即立即唤醒 scan_inbox（低延时信号）。
DOUYIN_TRANSPORT_WS_INBOUND = _env('DOUYIN_TRANSPORT_WS_INBOUND', 'false').lower() == 'true'

# Transport backend：纯 HTTP 协议（http_protocol），无浏览器。保留该开关仅为兼容，
# 其它取值会被忽略并退回 http_protocol。
DOUYIN_TRANSPORT_BACKEND = _env('DOUYIN_TRANSPORT_BACKEND', 'http_protocol')

# 签名后端：
#   'js'   : JsSignProvider —— PyExecJS 执行 vendored dy_ab.js，a_bogus +
#            bd-ticket-guard 齐全，私信可收可发（推荐，默认）
#   'local': LocalSignProvider —— 纯 Python 算 a_bogus + msToken，缺 bd-ticket-guard，
#            私信发送大概率签不出，仅用于扫描调试
DOUYIN_SIGN_BACKEND = _env('DOUYIN_SIGN_BACKEND', 'js')

# Verb 级开关：对应 verb 走 HTTP 协议路径。STRICT=true 时失败直接抛错，不做任何回退。
DOUYIN_HTTP_PROTOCOL_SEND_TEXT = _env('DOUYIN_HTTP_PROTOCOL_SEND_TEXT', 'true').lower() == 'true'
DOUYIN_HTTP_PROTOCOL_SEND_REPLY = _env('DOUYIN_HTTP_PROTOCOL_SEND_REPLY', 'true').lower() == 'true'
DOUYIN_HTTP_PROTOCOL_SEND_STRICT = _env('DOUYIN_HTTP_PROTOCOL_SEND_STRICT', 'true').lower() == 'true'
DOUYIN_HTTP_PROTOCOL_SCAN_INBOX = _env('DOUYIN_HTTP_PROTOCOL_SCAN_INBOX', 'true').lower() == 'true'
DOUYIN_HTTP_PROTOCOL_SCAN_STRICT = _env('DOUYIN_HTTP_PROTOCOL_SCAN_STRICT', 'true').lower() == 'true'
DOUYIN_HTTP_PROTOCOL_SCAN_INBOX_DUAL_RUN = _env(
    'DOUYIN_HTTP_PROTOCOL_SCAN_INBOX_DUAL_RUN', 'false'
).lower() == 'true'

# 影子干跑（dual-run）：启用后每次发送类调用额外编码一份 protobuf 落 [transport.dual_run]
# 日志但不真发，用于协议字段对账。零真发风险，与主路径正交，默认关闭。
DOUYIN_TRANSPORT_DUAL_RUN = _env('DOUYIN_TRANSPORT_DUAL_RUN', 'false').lower() == 'true'

# ---- 多账号托管 / Cookie 池治理（P1 健康 / P2 续期 / P3 并发） ----
# 常驻签名进程池：消除 PyExecJS「每次 call 起 Node 子进程」的子进程风暴。
# ENABLED=false 时回退每次起子进程的旧路径；SIZE 为常驻 node 进程数。
DOUYIN_SIGN_POOL_ENABLED = _env('DOUYIN_SIGN_POOL_ENABLED', 'true').lower() == 'true'
DOUYIN_SIGN_POOL_SIZE = int(_env('DOUYIN_SIGN_POOL_SIZE', '2'))
DOUYIN_SIGN_POOL_TIMEOUT = float(_env('DOUYIN_SIGN_POOL_TIMEOUT', '20'))
DOUYIN_NODE_BIN = _env('DOUYIN_NODE_BIN', 'node')

# 全局并发治理：同一时刻并发 scan/send 的账号数上限（<=0 不限制）；启动错峰最大抖动秒数。
DOUYIN_MAX_CONCURRENT_IO = int(_env('DOUYIN_MAX_CONCURRENT_IO', '16'))
DOUYIN_STARTUP_JITTER_S = float(_env('DOUYIN_STARTUP_JITTER_S', '8'))

# 统一 httpx 连接/超时（每账号独立 client）。
DOUYIN_HTTP_TIMEOUT_S = float(_env('DOUYIN_HTTP_TIMEOUT_S', '15'))
DOUYIN_HTTP_MAX_CONNECTIONS = int(_env('DOUYIN_HTTP_MAX_CONNECTIONS', '8'))
DOUYIN_HTTP_MAX_KEEPALIVE = int(_env('DOUYIN_HTTP_MAX_KEEPALIVE', '4'))
DOUYIN_HTTP_KEEPALIVE_EXPIRY_S = float(_env('DOUYIN_HTTP_KEEPALIVE_EXPIRY_S', '30'))

# 资源阈值告警：worker 进程 RSS/CPU 超阈值发 risk_alert（<=0 关闭对应维度）。
DOUYIN_MEM_ALERT_MB = float(_env('DOUYIN_MEM_ALERT_MB', '1500'))
DOUYIN_CPU_ALERT_PCT = float(_env('DOUYIN_CPU_ALERT_PCT', '85'))

# 僵尸 session 判定：心跳超过该秒数视为失联，由清理任务置 stopped。
DOUYIN_SESSION_STALE_SECONDS = int(_env('DOUYIN_SESSION_STALE_SECONDS', '120'))

# 凭证临期告警：bd-ticket 年龄超过该小时数发提前告警。
DOUYIN_TICKET_WARN_AGE_HOURS = float(_env('DOUYIN_TICKET_WARN_AGE_HOURS', '24'))

# ---- 接收侧加固：防误打回（瞬时抖动不当作真失效）+ 错峰退避 ----
# 登录失效「二次确认」阈值：连续命中失效信号多少次才真正打回账号（=1 即一次失效即打回）。
DOUYIN_LOGIN_EXPIRE_CONFIRM_TIMES = int(_env('DOUYIN_LOGIN_EXPIRE_CONFIRM_TIMES', '3'))
# 接收侧错误指数退避：base*2^(n-1) 截到 cap，再叠加 [0,base) 抖动。
DOUYIN_RECV_BACKOFF_BASE_S = float(_env('DOUYIN_RECV_BACKOFF_BASE_S', '10'))
DOUYIN_RECV_BACKOFF_CAP_S = float(_env('DOUYIN_RECV_BACKOFF_CAP_S', '120'))
# scheduler 主动探活：首探判失效后是否再复核一次，两次都失效才打回（防误判）。
DOUYIN_PROBE_RECONFIRM = _env('DOUYIN_PROBE_RECONFIRM', 'true').lower() == 'true'
DOUYIN_PROBE_RECONFIRM_DELAY_S = float(_env('DOUYIN_PROBE_RECONFIRM_DELAY_S', '3'))
# signer 半开熔断冷却秒数：失败累计达阈值后降级，冷却到期放一次试探，成功即自愈（无需重启）。
DOUYIN_SIGNER_DEGRADE_COOLDOWN_S = float(_env('DOUYIN_SIGNER_DEGRADE_COOLDOWN_S', '60'))

# 自动续期（默认关闭门控，PoC 验证通过后再开）：超过 REFRESH_AGE 小时尝试续期 bd-ticket。
DOUYIN_TICKET_AUTORENEW_ENABLED = _env('DOUYIN_TICKET_AUTORENEW_ENABLED', 'true').lower() == 'true'
DOUYIN_TICKET_REFRESH_AGE_HOURS = float(_env('DOUYIN_TICKET_REFRESH_AGE_HOURS', '18'))

# ---- 多 worker 分片 + 租约（P4 横向扩展，面向 200+ 账号） ----
# 分片：每个 worker 实例只托管 hash(account_id) % COUNT == INDEX 的账号。
# 单 worker 部署保持 COUNT=1（托管全部）。多实例部署时每个实例配不同 INDEX。
DOUYIN_WORKER_SHARD_COUNT = int(_env('DOUYIN_WORKER_SHARD_COUNT', '1'))
DOUYIN_WORKER_SHARD_INDEX = int(_env('DOUYIN_WORKER_SHARD_INDEX', '0'))

# 租约：多 worker 时用 Redis 锁保证一个账号同一时刻只被一个 worker 托管，崩溃后 TTL 到期转移。
# 单 worker 部署可保持关闭。命令定向路由在「分片>1 或 租约开启」时自动生效。
DOUYIN_WORKER_LEASE_ENABLED = _env('DOUYIN_WORKER_LEASE_ENABLED', 'false').lower() == 'true'
DOUYIN_WORKER_LEASE_TTL = int(_env('DOUYIN_WORKER_LEASE_TTL', '45'))
