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
DOUYIN_WORKER_HEADLESS = _env('DOUYIN_WORKER_HEADLESS', 'False').lower() == 'true'
DOUYIN_REDIS_CHANNEL_PREFIX = _env('DOUYIN_REDIS_CHANNEL_PREFIX', 'douyin')
DOUYIN_ENABLE_VIRTUAL_DISPLAY = _env('DOUYIN_ENABLE_VIRTUAL_DISPLAY', 'true').lower() == 'true'
DOUYIN_WORKER_BROWSER_CHANNEL = _env('DOUYIN_WORKER_BROWSER_CHANNEL', '')
DOUYIN_WORKER_VIEWPORT_WIDTH = int(_env('DOUYIN_WORKER_VIEWPORT_WIDTH', '1440'))
DOUYIN_WORKER_VIEWPORT_HEIGHT = int(_env('DOUYIN_WORKER_VIEWPORT_HEIGHT', '900'))
DOUYIN_WORKER_DISABLE_GPU = _env('DOUYIN_WORKER_DISABLE_GPU', 'true').lower() == 'true'
DOUYIN_WORKER_RENDERER_PROCESS_LIMIT = int(_env('DOUYIN_WORKER_RENDERER_PROCESS_LIMIT', '2'))
DOUYIN_WORKER_BLOCK_HEAVY_RESOURCES = _env('DOUYIN_WORKER_BLOCK_HEAVY_RESOURCES', 'true').lower() == 'true'
DOUYIN_WORKER_LOCALE = _env('DOUYIN_WORKER_LOCALE', 'zh-CN')
DOUYIN_WORKER_TIMEZONE = _env('DOUYIN_WORKER_TIMEZONE', 'Asia/Shanghai')

# ---- 抖音 IM Sniffer（协议化改造前的旁路抓包） ----
# 启用后会把 IM 相关 WS 帧 + HTTP 请求/响应落到 {DOUYIN_DATA_DIR}/sniff/
# 默认关闭；分析完关掉即可，对生产无侵入。
DOUYIN_SNIFFER_ENABLED = _env('DOUYIN_SNIFFER_ENABLED', 'false').lower() == 'true'
DOUYIN_SNIFFER_DIR = _env('DOUYIN_SNIFFER_DIR', '')
# 单条 body 截断字节数（避免大响应把磁盘塞爆）
DOUYIN_SNIFFER_MAX_BODY = int(_env('DOUYIN_SNIFFER_MAX_BODY', '32768'))
# HTTP URL 关键词白名单，逗号分隔；为空则使用内置默认列表
DOUYIN_SNIFFER_URL_KEYWORDS = _env('DOUYIN_SNIFFER_URL_KEYWORDS', '')

# ---- 抖音 Transport (Phase 2) ----
# 启用 WS 入向消息快路径：worker 内的 BrowserTransport 会被 WsInboundDecorator 包一层，
# 监听 BrowserContext 上的 IM WebSocket 帧 → 解 Frontier protobuf → 命中即立即唤醒 scan_inbox。
# fallback 策略：WS 仅作为"立即扫一次"的低延时信号，真实落库内容仍由浏览器 DOM 扫描产出，
# 这避免了 protobuf 没 schema 时解错入错数据的风险。
# 默认关闭；线上稳定后再打开。
DOUYIN_TRANSPORT_WS_INBOUND = _env('DOUYIN_TRANSPORT_WS_INBOUND', 'false').lower() == 'true'

# ---- 抖音 Transport (Phase 3) ----
# Transport backend 选择：
#   'browser'        : 默认；BrowserTransport（Phase 1/2 行为，DOM 扫描 + 文本框输入）
#   'http_protocol'  : Phase 3 hybrid 协议化（浏览器仅做签名，业务流量走 httpx）
#                       未实现的 verb 自动 fallback 到 BrowserTransport，灰度安全
# 切到 'http_protocol' 之前请确认：
#   1) 已经跑过 sniffer 抓到完整协议地图（manage.py douyin_sniff_analyze）
#   2) 至少打开下面一个 DOUYIN_HTTP_PROTOCOL_* 开关
DOUYIN_TRANSPORT_BACKEND = _env('DOUYIN_TRANSPORT_BACKEND', 'http_protocol')

# 签名后端（仅当 DOUYIN_TRANSPORT_BACKEND=http_protocol 时生效）：
#   'browser'（默认）: SignProvider —— 每账号一个浏览器 context 做签名（重）
#   'local'          : LocalSignProvider —— 纯 Python 算 a_bogus + msToken，无浏览器；
#                      但缺 bd-ticket-guard，私信发送大概率签不出
#   'js'             : JsSignProvider —— PyExecJS 执行 vendored dy_ab.js，a_bogus +
#                      bd-ticket-guard 齐全，私信可收可发，无浏览器（推荐的脱浏览器后端）
# local/js 为灰度路径：imapi 是否接受需用真实 cookie/抓包验证；未就绪/失败时
# HttpProtocolTransport 仍会 fallback 到 BrowserTransport，零回归。
DOUYIN_SIGN_BACKEND = _env('DOUYIN_SIGN_BACKEND', 'js')

# Verb 级灰度开关（仅当 DOUYIN_TRANSPORT_BACKEND=http_protocol 时生效）
# 任一开关打开后，对应的 verb 走 HTTP 协议路径；失败自动 fallback 到 BrowserTransport。
# 推荐切量顺序：send_text → send_reply → scan_inbox（出向比入向幂等性高）
DOUYIN_HTTP_PROTOCOL_SEND_TEXT = _env('DOUYIN_HTTP_PROTOCOL_SEND_TEXT', 'true').lower() == 'true'
DOUYIN_HTTP_PROTOCOL_SEND_REPLY = _env('DOUYIN_HTTP_PROTOCOL_SEND_REPLY', 'true').lower() == 'true'
DOUYIN_HTTP_PROTOCOL_SEND_STRICT = _env('DOUYIN_HTTP_PROTOCOL_SEND_STRICT', 'true').lower() == 'true'
DOUYIN_HTTP_PROTOCOL_SCAN_INBOX = _env('DOUYIN_HTTP_PROTOCOL_SCAN_INBOX', 'true').lower() == 'true'
DOUYIN_HTTP_PROTOCOL_SCAN_STRICT = _env('DOUYIN_HTTP_PROTOCOL_SCAN_STRICT', 'true').lower() == 'true'
# scan_inbox 双跑对账：HTTP 解析 + DOM 扫描同时跑，对账后再翻 SCAN_INBOX=true
# 让 HTTP 主路径接管。开启时 worker 日志会出现 [transport.scan_dual_run] ... 对账输出
DOUYIN_HTTP_PROTOCOL_SCAN_INBOX_DUAL_RUN = _env(
    'DOUYIN_HTTP_PROTOCOL_SCAN_INBOX_DUAL_RUN', 'false'
).lower() == 'true'

# Phase 3 影子干跑（dual-run）：启用后**每次**发送类调用都额外编码一份 protobuf
# 落到 [transport.dual_run] 日志，但**不真发**。配合 sniffer 抓真实出站 IM 流量，
# 做事后字段映射对账。
# 零真发风险：影子路径只编码 + dump，不会调 signed_fetch / 不会发 HTTP。
# 与 DOUYIN_TRANSPORT_BACKEND 正交：browser + dual_run 是最保守的"用 DOM 真发，
# 同时验证协议格式"组合，推荐 Phase 3.3 灰度前 24-48h 用这个组合预热观察。
DOUYIN_TRANSPORT_DUAL_RUN = _env('DOUYIN_TRANSPORT_DUAL_RUN', 'false').lower() == 'true'
