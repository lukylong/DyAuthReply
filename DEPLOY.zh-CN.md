# 部署文档（DyAuthReply / zq-platform）

本项目包含三类进程：**Django Web（ASGI）**、**APScheduler（后台调度）**、**Douyin Worker（Playwright 浏览器自动化）**，前端是 **Vue 3 + Vite** 打包产物。文档覆盖：本地开发、本地打包产物、Linux 服务器部署、Nginx 反向代理、域名解析与 HTTPS 证书、抖音模块特殊注意点。

> 目录索引
>
> 1. [架构速览](#1-架构速览)
> 2. [本地开发](#2-本地开发运行源码)
> 3. [本地打包产物](#3-本地打包产物)
> 4. [服务器基础环境](#4-服务器基础环境)
> 5. [服务器部署：方式 A — Docker Compose（推荐）](#5-服务器部署方式-a--docker-compose推荐)
> 6. [服务器部署：方式 B — 裸机 / systemd](#6-服务器部署方式-b--裸机--systemd)
> 7. [前端部署与 Nginx 配置](#7-前端部署与-nginx-配置)
> 8. [域名 DNS 解析](#8-域名-dns-解析)
> 9. [HTTPS 证书（Let's Encrypt / 商业证书）](#9-https-证书lets-encrypt--商业证书)
> 10. [抖音 worker 部署要点](#10-抖音-worker-部署要点)
> 11. [升级 / 回滚 / 备份](#11-升级--回滚--备份)
> 12. [常见故障排查](#12-常见故障排查)

---

## 1. 架构速览

```
                         ┌───────────────────────────┐
                         │  DNS: zq.example.com      │
                         │  → 服务器 公网 IP         │
                         └────────────┬──────────────┘
                                      │ 443 / 80
                          ┌───────────▼────────────┐
                          │        Nginx           │
                          │  SSL 终结 / 静态托管  │
                          └───┬─────────┬──────────┘
                              │         │
                 前端静态 dist│         │反代 /api /ws
                              │         │
                     ┌────────▼──┐  ┌───▼───────────────┐
                     │ web/dist  │  │ Django ASGI :8000 │
                     └───────────┘  │  (uvicorn)        │
                                    └───┬───────────────┘
                                        │
                    ┌───────────────────┼───────────────────────┐
                    │                   │                       │
           ┌────────▼────────┐ ┌────────▼────────┐  ┌───────────▼──────────┐
           │  PostgreSQL 16  │ │     Redis 7     │  │ APScheduler (独进程) │
           └─────────────────┘ └────────┬────────┘  └──────────────────────┘
                                        │ Pub/Sub
                                ┌───────▼─────────────────┐
                                │ Douyin Worker           │
                                │ Playwright + Chromium   │
                                │ 每账号一个 Context      │
                                └─────────────────────────┘
```

端口 / 路径约定：

| 组件 | 端口（内部） | 对外 | 说明 |
| --- | --- | --- | --- |
| PostgreSQL | 5432 | 否 | Django、APScheduler 共用 |
| Redis | 6379 | 否 | Channels、APScheduler、Douyin 命令 |
| Django ASGI (uvicorn) | 8000 | 通过 Nginx 反代 | `/basic-api/*`、`/ws/*` |
| Vite dev server | 5777 | 仅开发 | `pnpm dev` |
| Nginx | 80 / 443 | 是 | 唯一暴露给公网的端口 |

---

## 2. 本地开发（运行源码）

### 2.1 最低依赖

- **操作系统**：macOS / Linux / Windows（WSL2）
- **Python** ≥ 3.11
- **Node.js** ≥ 20 + **pnpm** ≥ 9
- **PostgreSQL** ≥ 14、**Redis** ≥ 6（或直接 `docker compose up postgres redis`）
- **Chromium**（Playwright 安装）

### 2.2 一键 Docker Compose（推荐开发方式）

```bash
cp .env.example .env          # 按需修改账号密码
docker compose up -d --build  # 启动 postgres / redis / backend / scheduler / web
docker compose logs -f backend
```

访问：

- 前端：<http://localhost:5777>
- 后端 API：<http://localhost:8000/api/docs>

抖音 worker 需要显式加 profile：

```bash
# 本地首次扫码调试推荐关闭无头：改 .env 里 DOUYIN_WORKER_HEADLESS=False，
# 并在宿主机跑（容器里跑无头登录则保持 True）
docker compose --profile douyin up -d douyin-worker
```

### 2.3 裸机本地开发

```bash
# 后端
cd backend-django
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium       # Playwright 浏览器
python manage.py migrate
python manage.py runserver 0.0.0.0:8000

# 调度器（另开终端）
python start_scheduler.py

# 抖音 worker（另开终端，首次扫码需 DOUYIN_WORKER_HEADLESS=False）
python start_douyin_worker.py

# 前端
cd web
pnpm install
pnpm dev                           # 默认启动 web-ele on :5777
```

---

## 3. 本地打包产物

### 3.1 前端打包

```bash
cd web
pnpm install
pnpm build              # turbo 驱动，会构建所有 apps
#   或只打一个 app:
pnpm --filter @vben/web-ele build
```

产物目录：

```
web/apps/web-ele/dist/          ← 最终用于部署的静态文件
web/apps/web-ele/dist.zip       ← 若 .env.production 里 VITE_ARCHIVER=true 会额外生成
```

关键环境变量（`web/apps/web-ele/.env.production`）：

| 变量 | 默认 | 生产必须改 |
| --- | --- | --- |
| `VITE_BASE` | `/` | 如果部署到子路径（如 `/admin/`），改成 `/admin/` |
| `VITE_GLOB_API_URL` | `https://fastapi.zq-platform.cn/basic-api/` | **必须**改成你自己的后端 API 地址，例如 `https://zq.example.com/basic-api/`（末尾 `/` 不可省。前端代码接口名已带 `/api/...`，Nginx 必须用 `/basic-api/` 前缀匹配，否则 404） |
| `VITE_ROUTER_HISTORY` | `history` | 保持 `history`（需 Nginx 配 fallback） |
| `VITE_COMPRESS` | `none` | 想减小体积可用 `gzip` 或 `brotli` |

> **重要**：修改 `.env.production` 后必须重新 `pnpm build`，Vite 会把 API 地址 inline 进 JS，运行时无法再改。

### 3.2 后端打包

后端不需要"打包"，部署时把整个 `backend-django/` 目录拷贝到服务器即可；生产环境的差异靠 **环境变量** 区分：

- `ZQ_ENV=prod`（对应 `backend-django/env/prod_env.py`）
- `DEBUG=false`（生产固定）
- 数据库 / Redis / JWT 密钥 / 抖音 Fernet 密钥全部走环境变量

也可以构建 Docker 镜像：

```bash
# 在项目根执行
docker build -t registry.example.com/zq-platform/backend:v1.0.0 ./backend-django
docker push  registry.example.com/zq-platform/backend:v1.0.0
```

---

## 4. 服务器基础环境

**最低建议配置**：

| 角色 | CPU | 内存 | 磁盘 | 说明 |
| --- | --- | --- | --- | --- |
| 全部混部（小规模） | 2 核 | 4 GB | 40 GB | ≤ 5 个抖音账号 |
| 生产推荐 | 4 核 | 8 GB | 80 GB + SSD | ≤ 20 个抖音账号 |
| 每多 10 个抖音账号 | +1 核 | +1 GB | — | Chromium 常驻进程耗资源 |

**操作系统**：Ubuntu 22.04 LTS / Debian 12（其他发行版 Playwright 容器已内置依赖，推荐 Docker 方案）。

**开放端口**（云厂商安全组）：**仅 22 / 80 / 443**，其他所有端口不要对公网开放。

---

## 5. 服务器部署：方式 A — Docker Compose（推荐）

### 5.1 服务器初始化

```bash
# 以 Ubuntu 22.04 为例
sudo apt update && sudo apt install -y git curl ca-certificates

# 安装 Docker + Compose 插件
curl -fsSL https://get.docker.com | sudo bash
sudo usermod -aG docker $USER   # 重新登录生效

# 安装 Nginx（用于 443 入口 + 静态前端）
sudo apt install -y nginx
```

### 5.2 拉取项目 & 写 .env

```bash
sudo mkdir -p /opt && cd /opt
sudo git clone <your-repo-url> zq-platform
sudo chown -R $USER:$USER /opt/zq-platform
cd /opt/zq-platform

cp .env.example .env
vim .env    # 下面的变量必改
```

生产 `.env` 必改项：

```dotenv
POSTGRES_PASSWORD=<强随机>
REDIS_PASSWORD=<强随机>
DJANGO_SECRET_KEY=<至少 50 字节随机>
JWT_ACCESS_SECRET_KEY=<随机>
JWT_REFRESH_SECRET_KEY=<随机>

UVICORN_RELOAD=                 # 生产置空，关掉热重载
AUTO_LOADDATA=false             # 首次以外都设 false

# 抖音 Fernet 登录态加密密钥（生成命令见下）
DOUYIN_STORAGE_ENCRYPTION_KEY=<Fernet.generate_key()>
DOUYIN_WORKER_HEADLESS=True     # 生产必须 True；扫码通过 WS 推前端完成
```

生成 Fernet 密钥：

```bash
docker run --rm python:3.11 python -c \
  "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 5.3 启动服务

```bash
# 基础四件套：postgres / redis / backend / scheduler / web
docker compose up -d --build

# 抖音 worker（需要先完成上面一条）
docker compose --profile douyin up -d douyin-worker

# 查看状态
docker compose ps
docker compose logs -f backend
```

### 5.4 首次启动：数据库迁移与初始化数据

后端容器启动时 entrypoint 会自动执行 `migrate --noinput`，但 **`scheduler` 等非 `core` app 的迁移第一次可能没生成**，导致 `db_init.json` 加载失败（报错 `relation "core_scheduler_job" does not exist`）。按下面顺序执行一次即可：

```bash
cd /opt/zq-platform

# ① 查看当前迁移状态（如果发现某个 app 标记为 [ ] 未应用，进第 ② 步）
docker compose exec backend python manage.py showmigrations

# ② 为缺失迁移的 app 补生成迁移文件（幂等，重复跑无副作用）
docker compose exec backend python manage.py makemigrations

# ③ 应用所有未执行的迁移（建表）
docker compose exec backend python manage.py migrate

# ④ 加载初始化种子数据（管理员、部门、菜单、角色、字典等共 130+ 条）
docker compose exec backend python manage.py loaddata db_init.json
```

加载完成后即可用内置账号登录：

| 账号 | 密码 | 说明 |
| --- | --- | --- |
| `superadmin` | `123456` | 系统超级管理员，**登录后立即改密** |

#### 5.4.1 验证

```bash
# 用户是否导入成功
docker compose exec backend python manage.py shell -c "
from django.contrib.auth import get_user_model
U = get_user_model()
print('用户总数:', U.objects.count())
for u in U.objects.filter(is_superuser=True):
    print('  超管:', u.username, 'active=', u.is_active)
"

# 直接打后端接口验证 JWT 能拿到（绕过前端，避免锁定干扰）
curl -s -X POST http://localhost:8000/api/core/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"superadmin","password":"123456"}'
```

看到返回体里有 `accessToken` 就表示全链路通了。

#### 5.4.2 不想用内置数据、自建超管

如果不想导入 `db_init.json`（只要一个干净超管），用 Django 内置命令即可：

```bash
docker compose exec backend python manage.py createsuperuser
```

#### 5.4.3 让以后每次重建容器自动加载

改 `.env`：

```dotenv
AUTO_LOADDATA=true
```

下次 `docker compose up -d` 时 entrypoint 会自动跑 `loaddata db_init.json`。**建议只在第一次或重置环境时开启**，生产日常保持 `false` 以免误覆盖已修改的用户数据。

#### 5.4.4 常见报错

| 报错 | 原因 | 解决 |
| --- | --- | --- |
| `relation "core_xxx" does not exist` | 对应 app 的迁移未应用 | 回到 ②③ 步重新 `makemigrations` + `migrate` |
| `IntegrityError: UNIQUE constraint failed` | 表里已有部分冲突数据 | `docker compose exec backend python manage.py flush --no-input` 清数据后重载；或手动删对应行 |
| `Problem installing fixture ... Could not find natural key` | 外键引用的记录未先插入 | `db_init.json` 已按依赖顺序排好，如需自定义 fixture 请保证先插父表 |
| 登录始终 401 但数据库里用户存在 | 连续登录失败被锁定 | 等几分钟；或参考 [第 12 节 故障排查](#12-常见故障排查) 清理登录失败表 |

### 5.5 前端的两种部署形态

Compose 默认的 `web` 服务跑的是 **Vite 开发服务器**，只适合开发。**生产部署前端应该用下面任一方式**：

**方案 1（推荐）：前端打包成静态文件，Nginx 直接托管**

```bash
# 在开发机或 CI 打包
cd web
pnpm install && pnpm --filter @vben/web-ele build

# 上传产物到服务器
scp -r apps/web-ele/dist/* user@server:/var/www/zq-platform/
```

**方案 2：直接在服务器上打包**

```bash
# 服务器安装 Node 20 + pnpm
curl -fsSL https://get.pnpm.io/install.sh | sh -
cd /opt/zq-platform/web
pnpm install
pnpm --filter @vben/web-ele build

# 同步到 Nginx 目录
sudo mkdir -p /var/www/zq-platform
sudo cp -r apps/web-ele/dist/* /var/www/zq-platform/
sudo chown -R www-data:www-data /var/www/zq-platform
```

> 生产环境可以在 `docker-compose.yml` 里把 `web` 服务删掉或改 profile，以节省服务器资源。

---

## 6. 服务器部署：方式 B — 裸机 / systemd

适合不愿用 Docker 的场景。核心是把三类进程各自注册成 systemd 服务。

### 6.1 准备

```bash
sudo apt install -y python3.11 python3.11-venv postgresql-16 redis-server nginx \
                    libnss3 libxkbcommon0 libgbm1 libasound2 fonts-noto-cjk
sudo -u postgres createuser zq --pwprompt
sudo -u postgres createdb zq_platform -O zq

sudo useradd -r -m -d /opt/zq-platform -s /bin/bash zq
sudo -u zq git clone <your-repo-url> /opt/zq-platform
cd /opt/zq-platform/backend-django
sudo -u zq python3.11 -m venv .venv
sudo -u zq .venv/bin/pip install -r requirements.txt
sudo -u zq .venv/bin/playwright install --with-deps chromium
```

把环境变量写到 `/etc/zq-platform.env`（systemd 会读取）：

```dotenv
ZQ_ENV=prod
DATABASE_TYPE=POSTGRESQL
DATABASE_HOST=127.0.0.1
DATABASE_PORT=5432
DATABASE_NAME=zq_platform
DATABASE_USER=zq
DATABASE_PASSWORD=<...>
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_PASSWORD=<...>
DJANGO_SECRET_KEY=<...>
JWT_ACCESS_SECRET_KEY=<...>
JWT_REFRESH_SECRET_KEY=<...>
DOUYIN_DATA_DIR=/var/lib/zq-platform/douyin
DOUYIN_STORAGE_ENCRYPTION_KEY=<...>
DOUYIN_WORKER_HEADLESS=True
```

### 6.2 三个 systemd 单元

`/etc/systemd/system/zq-backend.service`

```ini
[Unit]
Description=zq-platform Django ASGI
After=network.target postgresql.service redis-server.service

[Service]
Type=simple
User=zq
WorkingDirectory=/opt/zq-platform/backend-django
EnvironmentFile=/etc/zq-platform.env
ExecStartPre=/opt/zq-platform/backend-django/.venv/bin/python manage.py migrate --noinput
ExecStart=/opt/zq-platform/backend-django/.venv/bin/python -m uvicorn application.asgi:application --host 127.0.0.1 --port 8000 --workers 2
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/zq-scheduler.service`

```ini
[Unit]
Description=zq-platform APScheduler
After=zq-backend.service

[Service]
Type=simple
User=zq
WorkingDirectory=/opt/zq-platform/backend-django
EnvironmentFile=/etc/zq-platform.env
ExecStart=/opt/zq-platform/backend-django/.venv/bin/python start_scheduler.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/zq-douyin-worker.service`

```ini
[Unit]
Description=zq-platform Douyin Worker
After=zq-backend.service

[Service]
Type=simple
User=zq
WorkingDirectory=/opt/zq-platform/backend-django
EnvironmentFile=/etc/zq-platform.env
ExecStart=/opt/zq-platform/backend-django/.venv/bin/python start_douyin_worker.py
Restart=on-failure
RestartSec=10
# Chromium 崩溃不要无限重启
StartLimitIntervalSec=60
StartLimitBurst=5

[Install]
WantedBy=multi-user.target
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now zq-backend zq-scheduler zq-douyin-worker
sudo systemctl status zq-backend
```

---

## 7. 前端部署与 Nginx 配置

### 7.1 前端静态文件存放位置

| 路径 | 用途 |
| --- | --- |
| `/var/www/zq-platform/` | **推荐**，Nginx `root` 指向这里 |
| `/opt/zq-platform/web/apps/web-ele/dist/` | 若直接在服务器打包，可以让 Nginx 直接指向它，省一次拷贝 |

目录结构：

```
/var/www/zq-platform/
├── index.html
├── favicon.ico
├── assets/                  # JS / CSS / 图片等（Vite 带 hash）
├── resource/                # 静态资源
└── ...
```

### 7.2 Nginx 站点配置

`/etc/nginx/sites-available/zq-platform.conf`

```nginx
# 1) HTTP → HTTPS 跳转
server {
    listen 80;
    listen [::]:80;
    server_name zq.example.com;

    # Let's Encrypt http-01 校验需要的路径（详见第 9 节）
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

# 2) HTTPS 主站
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name zq.example.com;

    # --- 证书（第 9 节获取） ---
    ssl_certificate     /etc/letsencrypt/live/zq.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/zq.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;

    # --- 安全响应头 ---
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header Referer-Policy "strict-origin-when-cross-origin" always;

    client_max_body_size 50m;     # 文件上传上限，按需调整

    # --- 前端静态资源 ---
    root /var/www/zq-platform;
    index index.html;

    # 带 hash 的静态资源走强缓存
    location ~* \.(?:js|css|png|jpe?g|gif|svg|woff2?|ttf|ico)$ {
        expires 30d;
        add_header Cache-Control "public, immutable";
        try_files $uri =404;
    }

    # --- 后端 API 反代 ---
    # 前端 axios baseURL = VITE_GLOB_API_URL = https://zq.example.com/basic-api/
    # 接口相对路径自身已带 /api/... 前缀，最终浏览器请求形如
    #     https://zq.example.com/basic-api/api/core/douyin/rule
    # 下面的 proxy_pass 末尾带 "/"，会自动去掉 /basic-api 前缀，
    # 转发给 Django 的就是 /api/core/douyin/rule（匹配后端路由）。
    location /basic-api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
        # 若接口会返回 >10MB 的大 JSON（如导出），适当提高下面两项
        proxy_buffers          8 32k;
        proxy_busy_buffers_size   64k;
    }

    # Django admin / swagger docs 如果有对外需求再开（不推荐公网暴露 admin）
    location ~ ^/(admin|static)/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # --- WebSocket（抖音二维码推送 + 实时通知 + 心跳） ---
    # 前端直接连 wss://{host}/ws/*（见 web/apps/web-ele/src/api/core/websocket.ts）
    # 通道：
    #   /ws/douyin/         抖音账号事件（扫码二维码、登录成功/失败、回复日志）
    #   /ws/notifications/  通用系统通知
    #   /ws/test/           连通性探针（部署完成后可用来验证 ws 是否通）
    # 关键点：必须开启 HTTP/1.1 + Upgrade 头，且 proxy_read_timeout 拉长，
    # 否则 60s 无消息就会被 Nginx 默默踢断，二维码刷新期间容易掉线。
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade           $http_upgrade;
        proxy_set_header Connection        "upgrade";
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout  3600s;   # 心跳 30s 一次，给足 1 小时 idle 不断开
        proxy_send_timeout  3600s;
        proxy_buffering     off;      # ASGI 推送需要立即下发，关闭缓冲
    }

    # --- Vue history 路由 fallback（必须放最后） ---
    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

> **路径对齐小抄**（改配置前先对一眼）：
>
> | 浏览器请求 | Nginx 匹配 | 转发给 Django 的实际路径 |
> | --- | --- | --- |
> | `GET /basic-api/api/core/douyin/rule`          | `/basic-api/` | `/api/core/douyin/rule` |
> | `POST /basic-api/api/core/login`               | `/basic-api/` | `/api/core/login` |
> | `WSS /ws/douyin/?token=xxx`                    | `/ws/`        | `/ws/douyin/?token=xxx` |
> | `GET /assets/index-xxx.js`                     | 静态文件     | —（Nginx 直接返回 dist 文件） |
> | `GET /douyin/account`（刷新页面）              | `/`          | history fallback → `/index.html` |

启用：

```bash
sudo ln -s /etc/nginx/sites-available/zq-platform.conf /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

> **前端 `VITE_GLOB_API_URL` 必须设为 `https://zq.example.com/basic-api/`**（末尾 `/` 不可省），与上面 Nginx `location /basic-api/` 严格对齐，否则所有接口 404。
>
> 修改后需要重新 `pnpm --filter @vben/web-ele build` 并重新同步 `dist/` 到 `/var/www/zq-platform/`，Vite 会把 API 地址 inline 进 JS，运行时不可变更。

---

## 8. 域名 DNS 解析

以常见云厂商（阿里云 / 腾讯云 / Cloudflare）为例：

1. 进入 DNS 控制台，定位到你的域名 `example.com`
2. 新增 **A 记录**：

   | 主机记录 | 记录类型 | 记录值 | TTL |
   | --- | --- | --- | --- |
   | `zq` | A | `<服务器公网 IPv4>` | 600 |
   | `zq`（可选） | AAAA | `<IPv6>` | 600 |

3. 如果启用 CDN 或 Cloudflare Proxy，请临时关闭（申请 SSL 证书时用 DNS-01 除外），证书签发成功后再开
4. 验证：

   ```bash
   dig +short zq.example.com
   # 应返回你的服务器 IP
   ```

5. 域名备案（中国大陆服务器）：腾讯云/阿里云的 **ICP 备案** 必须在打开 80/443 之前完成，否则会被运营商拦截。香港/海外服务器无此要求。

---

## 9. HTTPS 证书（Let's Encrypt / 商业证书）

### 9.1 方案 A：Let's Encrypt + Certbot（免费，推荐）

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo mkdir -p /var/www/certbot

# 首次签发（Nginx 插件模式，会自动改写配置文件）
sudo certbot --nginx -d zq.example.com -m you@example.com --agree-tos --no-eff-email

# 或仅签发，自己管理 Nginx（webroot 模式，对应第 7 节里的 /.well-known 位置）
sudo certbot certonly --webroot -w /var/www/certbot -d zq.example.com -m you@example.com --agree-tos --no-eff-email
```

证书文件位置：

```
/etc/letsencrypt/live/zq.example.com/
├── fullchain.pem    ← ssl_certificate
├── privkey.pem      ← ssl_certificate_key
├── cert.pem
└── chain.pem
```

自动续期（apt 安装的 certbot 已自带 `systemd timer`）：

```bash
sudo systemctl list-timers | grep certbot
sudo certbot renew --dry-run   # 演练一次
```

### 9.2 方案 B：商业证书（阿里云、腾讯云、DigiCert 等）

1. 在云厂商控制台购买 / 免费申请 DV 证书，选 Nginx 格式
2. 下载 zip，包含 `xxx.pem`（含证书链）和 `xxx.key`
3. 上传到服务器：

   ```bash
   sudo mkdir -p /etc/nginx/ssl/zq.example.com
   sudo cp zq.example.com.pem /etc/nginx/ssl/zq.example.com/fullchain.pem
   sudo cp zq.example.com.key /etc/nginx/ssl/zq.example.com/privkey.pem
   sudo chmod 600 /etc/nginx/ssl/zq.example.com/*
   ```

4. 修改第 7 节 Nginx 配置里的证书路径指向上述位置
5. `sudo nginx -t && sudo systemctl reload nginx`
6. 记录证书到期日期，提前 15 天续签

### 9.3 证书校验

```bash
curl -I https://zq.example.com
# 浏览器：证书链完整、无 mixed content 警告

# 评分测试
# https://www.ssllabs.com/ssltest/analyze.html?d=zq.example.com
```

---

## 10. 抖音 worker 部署要点

1. **资源隔离**：Chromium 单实例 ≈ 150–300 MB 内存，生产环境建议 worker 独立一台 / 独立容器，避免和 web 抢 CPU
2. **登录态目录**：`DOUYIN_DATA_DIR=/var/lib/zq-platform/douyin/` **必须持久化**（Docker 已用命名卷 `douyin-data`；裸机部署自己做定期备份）
3. **Fernet 密钥**：`DOUYIN_STORAGE_ENCRYPTION_KEY` 一旦丢失，所有已保存的登录态作废，所有账号需要重新扫码。**强烈建议把这个 key 离线备份**
4. **首次扫码**：生产环境保持 `DOUYIN_WORKER_HEADLESS=True`，二维码会通过 WebSocket 推到前端 `/douyin/account` 页面，用抖音 APP 扫。**扫码前务必先验证 `/ws/douyin/` 通畅**：打开前端页面，浏览器 DevTools → Network → WS 过滤器，应该看到一条 `wss://<your-domain>/ws/douyin/?token=***` 的连接并处于 **101 Switching Protocols** 状态，否则二维码不会显示（对应排错：第 12 节"WebSocket 连不上"那行）
5. **代理 / UA**：在 `/douyin/account` 为每个账号单独配置 `proxy_url` 与 `user_agent`，worker 会自动应用
6. **调度任务**：登录到 `/scheduler` 页面添加两个内置任务：

   - `scheduler.tasks.douyin_reset_daily_quota` — `cron: 0 0 * * *`
   - `scheduler.tasks.douyin_aggregate_daily_stats` — `cron: 5 * * * *`

7. **DOM 失效**：抖音创作者中心页面会不定期改版，若扫描失败，优先更新 `backend-django/core/douyin/runtime/selectors.py`，不需要重新部署其他组件

---

## 11. 升级 / 回滚 / 备份

### 11.1 滚动升级（Docker）

```bash
cd /opt/zq-platform
git fetch && git checkout <new-tag>
docker compose build backend
docker compose up -d backend scheduler
# 观察日志；验证通过后：
docker compose --profile douyin up -d douyin-worker
```

### 11.2 回滚

```bash
git checkout <previous-tag>
docker compose up -d --build
# 数据库回滚：参考 backend-django/core/migrations/ 下的 migration 文件，
# 严重时用 11.3 的备份 restore
```

### 11.3 备份（每日定时）

```bash
# PostgreSQL
docker compose exec -T postgres pg_dump -U zq zq_platform | \
  gzip > /backup/pg_$(date +%F).sql.gz

# 抖音登录态（非常重要）
tar czf /backup/douyin_$(date +%F).tgz \
  /var/lib/docker/volumes/zq-platform_douyin-data/_data

# Fernet 密钥（建议放密码管理器，不要留在服务器）
cat .env | grep DOUYIN_STORAGE_ENCRYPTION_KEY
```

推荐用 `cron` 每日 03:00 执行，保留 14 天。

---

## 12. 常见故障排查

| 现象 | 检查点 |
| --- | --- |
| 前端打开白屏 | 1) 浏览器 Network 看静态资源是否 200；2) `VITE_BASE` 与部署子路径一致；3) Nginx `root` 路径正确 |
| 所有接口 404 / Network 里 URL 形如 `/api/api/...` | `VITE_GLOB_API_URL` 设成了 `/api/` 导致前缀重复。必须用 `/basic-api/`（末尾带 `/`），与 Nginx `location /basic-api/` 对齐 |
| 登录接口 CORS / 跨域 | `VITE_GLOB_API_URL` 必须 **同源 + /basic-api/**，或后端 `CORS_ALLOWED_ORIGINS` 显式允许当前域名 |
| WebSocket 连不上（二维码不显示 / 回复日志不实时） | 1) Nginx `/ws/` 缺 `Upgrade` / `Connection: upgrade` 头；2) `proxy_read_timeout` 短于心跳间隔（需 ≥60s，建议 3600s）；3) 前端浏览器控制台报 `WebSocket is closed before the connection is established`；4) 云厂商安全组未放行 443；5) 自建证书是自签名，浏览器默认拒绝 wss |
| 开发环境 vite 报 `ws proxy error: This socket has been ended...` | 已知无害噪声，vite.config 已吞掉；若仍刷屏，重启 `pnpm --filter @vben/web-ele dev` |
| 抖音扫码后立刻掉线 | 服务器时间漂移（`timedatectl`）；IP 段被风控，配 `proxy_url` |
| `migrate` 卡住 | PostgreSQL 连接超限；上一次进程锁未释放：`docker compose restart backend` |
| worker 频繁 OOM | 内存不足；减少同时托管账号数；或升配至 8 GB |
| Let's Encrypt 续签失败 | `/var/www/certbot` 路径被 Nginx 其他 location 拦截；`/.well-known` 必须可直接访问 |

---

> 附：`.env` 生产模板在仓库根目录 `.env.example`，Docker Compose 编排文件为 `docker-compose.yml`，Nginx 示例可以直接从第 7 节复制。
