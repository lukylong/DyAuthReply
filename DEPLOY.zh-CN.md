# 生产部署指南

本文档汇总 zq-platform（DyAuthReply）**生产环境**的部署方案，默认使用 Docker Compose。  
侧重：**环境同步、3 Worker 分片托管、Redis 租约防重复、后台进程监控**。

> 若需从旧环境**迁移数据库 / 抖音凭证**，请参考根目录 [zh-CN.md](./zh-CN.md) 中的数据迁移章节。

---

## 1. 目标架构

```text
公网 HTTPS
  └─ Nginx :443
       ├─ /manage/          → 前端 SPA（Django 托管 或 Nginx 静态）
       ├─ /basic-api/       → Django API（127.0.0.1:8000）
       └─ /ws/              → Django WebSocket

Docker Compose 内部：
  postgres          PostgreSQL 16
  redis             Redis 7（命令频道 + 租约锁）
  backend           Django ASGI（Web API + 可选 SPA）
  scheduler         APScheduler 定时任务
  douyin-worker-0   抖音 Worker 分片 0
  douyin-worker-1   抖音 Worker 分片 1
  douyin-worker-2   抖音 Worker 分片 2
```

### 抖音 Worker 分片模型

| 机制 | 说明 |
|------|------|
| **静态分片** | `bucket = md5(account_id)[:8] % SHARD_COUNT`，仅对应 `SHARD_INDEX` 的 worker 加载账号 |
| **Redis 租约** | `douyin:lease:<account_id>`，SET NX + TTL，防止多实例重复托管 |
| **命令路由** | 后台 pause/resume/manual_reply 经 Redis 广播，仅托管该账号的 worker 执行 |

默认配置：**3 个 Worker**（`SHARD_COUNT=3`，INDEX 分别为 0/1/2）。

---

## 2. 服务器准备

以 Ubuntu 22.04/24.04 为例：

```bash
sudo apt update
sudo apt install -y git curl ca-certificates nginx
curl -fsSL https://get.docker.com | sudo bash
sudo usermod -aG docker $USER
# 重新登录后
docker compose version
```

建议目录：

```bash
sudo mkdir -p /opt && sudo chown -R $USER:$USER /opt
cd /opt
git clone <your-repo-url> DyAuthReply
cd DyAuthReply
```

---

## 3. 环境变量（`.env`）

```bash
cp .env.example .env
vim .env
```

### 3.1 必改项（生产）

```dotenv
# 数据库
POSTGRES_PASSWORD=<强密码>

# Redis（生产强烈建议开启）
REDIS_PASSWORD=<强密码>

# Django / JWT
DJANGO_SECRET_KEY=<长随机串>
JWT_ACCESS_SECRET_KEY=<长随机串>
JWT_REFRESH_SECRET_KEY=<长随机串>

# 关闭开发特性
UVICORN_RELOAD=
AUTO_LOADDATA=false

# 抖音凭证加密（上线后不可随意更改）
DOUYIN_STORAGE_ENCRYPTION_KEY=<Fernet密钥>
```

生成 Fernet 密钥：

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 3.2 抖音 Worker 分片 / 租约

```dotenv
DOUYIN_WORKER_SHARD_COUNT=3
DOUYIN_WORKER_LEASE_ENABLED=true
DOUYIN_WORKER_LEASE_TTL=45
DOUYIN_SESSION_STALE_SECONDS=120
```

### 3.3 纯协议默认值（一般保持即可）

```dotenv
DOUYIN_TRANSPORT_BACKEND=http_protocol
DOUYIN_SIGN_BACKEND=js
DOUYIN_HTTP_PROTOCOL_SEND_STRICT=true
DOUYIN_HTTP_PROTOCOL_SCAN_STRICT=true
```

### 3.4 生产注意事项

| 项 | 说明 |
|----|------|
| `ZQ_ENV` | compose 默认 `dev`；若改 `prd`，需确认 `env/prd_env.py` 中 `IS_DEMO=false` 且 DB/Redis 从环境变量读取 |
| `DEBUG` | 生产应在代码或 env 中关闭，并启用 HTTPS 安全头 |
| 密钥 | 切勿提交 `.env` 到 Git |

---

## 4. 生产 Compose 调整

当前 `docker-compose.yml` 偏开发态，**生产建议**：

| 开发配置 | 生产应改为 |
|----------|------------|
| `./backend-django:/app` bind mount | 去掉，使用镜像内代码 |
| `UVICORN_RELOAD=1` | 留空 |
| `build:` 本地构建 | CI 构建后 push 私有镜像，`image: registry/.../zq-backend:tag` |
| Postgres/Redis 端口映射到公网 | 仅容器内访问 |
| 对外端口 | 仅 Nginx `80`/`443` |

Worker 服务（已在 compose 中配置）：

- `douyin-worker-0` → `DOUYIN_WORKER_SHARD_INDEX=0`
- `douyin-worker-1` → `DOUYIN_WORKER_SHARD_INDEX=1`
- `douyin-worker-2` → `DOUYIN_WORKER_SHARD_INDEX=2`

每个 Worker 含 **healthcheck**（检测 `start_douyin_worker.py` 进程）。

---

## 5. 前端构建与发布

### 5.1 修改生产 API 地址

编辑 `web/apps/web-ele/.env.production`：

```dotenv
VITE_BASE=/manage/

# 与 Nginx 同域名反代（推荐）
VITE_GLOB_API_URL=/basic-api/

VITE_ROUTER_HISTORY=history
```

若 API 在独立域名：

```dotenv
VITE_GLOB_API_URL=https://your-domain.com/basic-api/
```

### 5.2 构建并打入 Django（一体化托管）

```bash
./build_and_copy_frontend.sh
docker compose build backend
```

访问地址：`https://your-domain.com/manage/`

### 5.3 API 路径约定

前端请求统一带 `/api` 前缀，经 Nginx `/basic-api/` 反代到 Django：

```text
浏览器  →  /basic-api/api/core/douyin/worker-monitor/overview
Nginx   →  http://127.0.0.1:8000/api/core/douyin/worker-monitor/overview
```

---

## 6. 首次部署步骤

### 6.1 构建镜像（可选：推送到私有仓库）

```bash
./build_and_copy_frontend.sh
docker compose build backend
# docker tag / push ...
```

### 6.2 启动基础服务

```bash
docker compose up -d postgres redis
docker compose ps   # 确认 healthy
```

### 6.3 启动 Web 与调度器

```bash
docker compose up -d backend scheduler
docker compose logs -f backend   # 等待 migrate 完成
```

### 6.4 数据库初始化（空库首次）

```bash
docker compose exec backend python manage.py migrate --noinput
# 可选：docker compose exec backend python manage.py loaddata db_init.json
docker compose exec backend python manage.py createsuperuser
```

迁移 Worker 监控菜单（若尚未执行）：

```bash
docker compose exec backend python manage.py migrate core --noinput
```

### 6.5 启动 3 个抖音 Worker

**务必带 `--remove-orphans`**，清理旧版单 worker 容器，避免重复托管：

```bash
docker compose --profile douyin up -d --remove-orphans \
  douyin-worker-0 douyin-worker-1 douyin-worker-2
```

### 6.6 配置 Nginx + HTTPS

```nginx
server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate     /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    # Django 一体化托管时，/manage/ 由 backend 提供
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # 若前端静态由 Nginx 托管，改用：
    # location / { root /var/www/dist; try_files $uri $uri/ /index.html; }
    # location /basic-api/ {
    #     proxy_pass http://127.0.0.1:8000/;
    #     ...
    # }
    # location /ws/ { ... WebSocket Upgrade ... }
}
```

---

## 7. Worker 进程监控（后台）

部署完成后，在管理后台查看：

**抖音托管 → Worker 进程监控**（`/douyin/worker-monitor`）

监控项包括：

| 区块 | 内容 |
|------|------|
| 概览 | 分片数、托管账号、存活会话、Worker 数、租约数、告警数 |
| 分片状态 | 每个 shard 账号数、活跃 worker、正常/空闲/缺失 |
| Worker 进程 | worker_id、推断分片、心跳、内存、租约数 |
| Redis 租约 | 持有者 vs 会话 worker 是否一致 |
| 告警 | 分片无 worker、租约不一致、Redis 不可用、跨分片托管等 |

API（需登录）：

```http
GET /api/core/douyin/worker-monitor/overview
```

---

## 8. 上线验收清单

### 8.1 基础服务

- [ ] `docker compose ps` 中 postgres、redis、backend、scheduler 均为 Up
- [ ] `docker compose --profile douyin ps` 中 **3 个 worker 均为 Up (healthy)**
- [ ] 无遗留旧容器（`docker ps -a | grep douyin-worker` 不应出现未在 compose 中定义的名称）

### 8.2 Web / API

- [ ] `https://域名/manage/` 可打开并登录
- [ ] `https://域名/api/docs` 可访问
- [ ] Worker 监控页数据正常刷新

### 8.3 抖音 Worker

- [ ] 后台 **Worker 进程监控**：3 个分片状态符合预期，告警为 0
- [ ] 各账号落在正确分片（`md5(account_id) % 3`）
- [ ] Redis 租约数 ≈ 在线托管账号数
- [ ] 租约「一致」列均为「是」
- [ ] 日志有心跳与扫描：`docker compose logs -f douyin-worker-1`

### 8.4 命令行快速检查

```bash
# Worker 状态
docker compose --profile douyin ps

# 分片日志关键字
docker compose logs douyin-worker-0 2>&1 | rg "分片|shard"
docker compose logs douyin-worker-1 2>&1 | rg "启动账号协程"
docker compose logs douyin-worker-2 2>&1 | rg "启动账号协程"

# Redis 租约（在 backend 容器内）
docker compose exec backend python -c "
import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','application.settings'); django.setup()
from django.conf import settings; import redis
r = redis.from_url(settings.REDIS_URL, decode_responses=True)
for k in sorted(r.scan_iter('douyin:lease:*')):
    print(k, '->', r.get(k), 'ttl=', r.ttl(k))
"
```

---

## 9. 日常运维

### 9.1 发布新版本

```bash
cd /opt/DyAuthReply
git pull
./build_and_copy_frontend.sh          # 前端有变更时
docker compose build backend
docker compose up -d backend scheduler
docker compose exec backend python manage.py migrate --noinput

# Worker 有变更时单独重启（不影响 Web）
docker compose --profile douyin up -d --remove-orphans \
  douyin-worker-0 douyin-worker-1 douyin-worker-2
```

### 9.2 仅重启 Worker

```bash
docker compose restart douyin-worker-0 douyin-worker-1 douyin-worker-2
```

### 9.3 查看日志

```bash
docker compose logs -f backend
docker compose logs -f scheduler
docker compose logs -f douyin-worker-0
docker compose logs -f douyin-worker-1
docker compose logs -f douyin-worker-2
```

容器内日志文件：

```text
/app/logs/server.log
/app/logs/scheduler.log
/app/logs/douyin_worker.log
```

---

## 10. 扩容与缩容

### 10.1 何时需要更多 Worker

| 账号规模 | 建议 |
|----------|------|
| < 30 | 可改为 1 Worker（`SHARD_COUNT=1`，租约可关） |
| 30–80 | 1 Worker + 调 `DOUYIN_MAX_CONCURRENT_IO` |
| 80+ | 保持 3 Worker 或增加分片数 |

### 10.2 扩分片（例如 3 → 4）

1. **停掉全部 Worker**
2. 修改 `.env`：`DOUYIN_WORKER_SHARD_COUNT=4`
3. 在 compose 中增加 `douyin-worker-3`（`SHARD_INDEX=3`）
4. **同时启动全部 4 个 Worker**

> 改 `SHARD_COUNT` 会重新分配账号归属，不可新旧配置混跑。

### 10.3 租约与故障转移的边界

- **租约**：防止两个 Worker **同时**托管同一账号（配置错误、遗留容器）
- **分片单点**：某分片唯一的 Worker 挂了，**其他分片 Worker 不会接管**其账号；依赖 Docker `restart: unless-stopped` 自动重启该容器

---

## 11. 常见问题

### Q1：两个 Worker 同时消费同一账号

**原因**：旧版单 worker 容器未清理，或两个容器配置了相同 `SHARD_INDEX`。

**处理**：

```bash
docker ps -a | grep douyin
docker stop <遗留容器> && docker rm <遗留容器>
docker compose --profile douyin up -d --remove-orphans \
  douyin-worker-0 douyin-worker-1 douyin-worker-2
```

在 **Worker 进程监控** 中确认：无「租约与会话 worker 不一致」「Worker 跨分片托管」告警。

### Q2：部分账号无人托管

**原因**：`SHARD_COUNT=3` 但只启动了部分 Worker（例如缺 worker-2）。

**处理**：确保 3 个 Worker 全部 Up；监控页对应分片不应显示「无 Worker」。

### Q3：Worker 监控 API 404

正确路径必须带 `/api` 前缀：

```text
/api/core/douyin/worker-monitor/overview
```

错误示例：`/core/douyin/worker-monitor/overview`

### Q4：`DOUYIN_STORAGE_ENCRYPTION_KEY` 改了之后账号失效

密钥变更后已加密凭证无法解密，需在后台重新「导入登录态」。

### Q5：Redis 不可用

- 租约状态未知，多 Worker 下有重复托管风险
- 后台 pause/manual_reply 等 Redis 命令失效
- 监控页会显示「Redis 不可用」告警

---

## 12. 服务与端口对照

| 组件 | 容器名 | 对外端口（生产建议） |
|------|--------|----------------------|
| PostgreSQL | zq-postgres | 不暴露 |
| Redis | zq-redis | 不暴露 |
| Django API | zq-backend | 127.0.0.1:8000 或仅内网 |
| Scheduler | zq-scheduler | 无 |
| Worker-0 | zq-douyin-worker-0 | 无 |
| Worker-1 | zq-douyin-worker-1 | 无 |
| Worker-2 | zq-douyin-worker-2 | 无 |
| Nginx | 宿主机 | 80 / 443 |

---

## 13. 相关文件

| 文件 | 说明 |
|------|------|
| `docker-compose.yml` | 服务定义（含 3 Worker + healthcheck） |
| `.env.example` | 环境变量模板 |
| `build_and_copy_frontend.sh` | 前端构建并复制到 `backend-django/dist` |
| `backend-django/core/douyin/douyin_worker_monitor_*.py` | Worker 监控采集与 API |
| `web/apps/web-ele/src/views/douyin/worker-monitor/` | Worker 监控前端页 |
| `zh-CN.md` | 含数据迁移、回滚等补充说明 |

---

## 14. 最小启动命令速查

```bash
# 全量生产启动（首次或日常）
docker compose up -d postgres redis backend scheduler
docker compose --profile douyin up -d --remove-orphans \
  douyin-worker-0 douyin-worker-1 douyin-worker-2

# 验收
docker compose --profile douyin ps
# 浏览器：抖音托管 → Worker 进程监控
```
