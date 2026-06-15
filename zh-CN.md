# Linux + Docker 迁移上线文档

本文档面向这套项目从本地/测试环境迁移到 Linux 服务器正式上线的场景，部署方式默认使用 Docker Compose。

适用范围：

- Django Web
- APScheduler
- Douyin Worker
- PostgreSQL
- Redis
- Vue 前端

---

## 1. 目标架构

建议的生产结构：

```text
公网流量
  -> Nginx :80/:443
  -> 前端静态文件
  -> 反向代理 /basic-api 和 /ws

Docker Compose 内部：
  - backend        Django ASGI
  - scheduler      APScheduler
  - douyin-worker  抖音托管 worker
  - postgres       PostgreSQL
  - redis          Redis
```

说明：

- `douyin-worker` 当前仍带 `profiles: ["douyin"]`，生产可继续保留，按需单独启动。
- 日志文件统一写到容器内 `/app/logs/`，对应宿主机挂载目录是项目下的 `backend-logs` volume。

---

## 2. 上线前确认

上线前先确认这些点：

1. 域名、备案、DNS、HTTPS 证书方案已确定。
2. 服务器开放端口仅保留 `22`、`80`、`443`。
3. 生产 `.env` 已准备，不能直接拿开发 `.env` 原样上线。
4. 数据库迁移脚本已能在目标版本上执行。
5. 如果要迁移抖音托管账号，必须同时迁移：
   - PostgreSQL 业务数据
   - `douyin-data` 目录/volume
   - `DOUYIN_STORAGE_ENCRYPTION_KEY`
6. 前端生产包的 API 地址已确认。

---

## 3. 需要迁移的内容

正式迁移时，至少需要迁移这些内容：

### 3.1 代码和配置

- 仓库代码
- 生产 `.env`
- Nginx 配置

### 3.2 数据

- PostgreSQL 数据库
- Redis 数据
- Douyin 持久化数据

Douyin 持久化数据非常关键，包含：

- 加密后的账号登录态凭证（Cookie + web_protect/keys）

默认在容器中挂载到：

```text
/var/lib/zq-platform/douyin
```

对应 compose 里的 volume 是：

```text
douyin-data
```

### 3.3 不需要迁移的内容

这些通常不需要迁移：

- `__pycache__`
- `.pyc`
- 本地 SQLite
- 本地日志
- 前端 `node_modules`

---

## 4. Linux 服务器准备

以 Ubuntu 22.04/24.04 为例：

```bash
sudo apt update
sudo apt install -y git curl ca-certificates nginx
curl -fsSL https://get.docker.com | sudo bash
sudo usermod -aG docker $USER
```

重新登录后检查：

```bash
docker version
docker compose version
nginx -v
```

建议目录：

```bash
sudo mkdir -p /opt
sudo chown -R $USER:$USER /opt
cd /opt
git clone <your-repo-url> DyAuthReply
cd DyAuthReply
```

---

## 5. 生产环境变量

在服务器生成生产 `.env`：

```bash
cp .env.example .env
vim .env
```

至少要改这些：

```dotenv
POSTGRES_PASSWORD=<强密码>
REDIS_PASSWORD=<强密码>
DJANGO_SECRET_KEY=<长随机串>
JWT_ACCESS_SECRET_KEY=<长随机串>
JWT_REFRESH_SECRET_KEY=<长随机串>
AUTO_LOADDATA=false
UVICORN_RELOAD=

DOUYIN_STORAGE_ENCRYPTION_KEY=<必须保留原值，若是迁移老环境>
DOUYIN_TRANSPORT_BACKEND=http_protocol
DOUYIN_SIGN_BACKEND=js
```

注意：

- 如果你要恢复老环境的抖音登录态，`DOUYIN_STORAGE_ENCRYPTION_KEY` 必须和老环境完全一致。
- 改了这个 key 之后，老的加密凭证会全部失效，需要重新导入登录态。

---

## 6. 数据备份与迁移

### 6.1 PostgreSQL

源环境导出：

```bash
docker compose exec postgres pg_dump -U zq -d zq_platform > zq_platform.sql
```

目标环境导入：

```bash
cat zq_platform.sql | docker compose exec -T postgres psql -U zq -d zq_platform
```

如果目标环境是全新机器，建议先启动基础服务再导入。

### 6.2 Redis

如果 Redis 只做缓存，可不迁移。

如果 Redis 中承载了你要保留的运行态数据，再迁移 `dump.rdb` 或 AOF。

通常这个项目上线迁移时，Redis 可以按“可重建”处理。

### 6.3 Douyin 托管数据

如果要保留抖音账号托管状态，迁移源环境的 `douyin-data`。

查看 volume：

```bash
docker volume ls
docker volume inspect zq-platform_douyin-data
```

常见做法是把 volume 挂载目录打包：

```bash
docker run --rm \
  -v zq-platform_douyin-data:/from \
  -v "$PWD":/to \
  alpine sh -c "cd /from && tar czf /to/douyin-data.tar.gz ."
```

目标环境恢复：

```bash
docker run --rm \
  -v zq-platform_douyin-data:/to \
  -v "$PWD":/from \
  alpine sh -c "cd /to && tar xzf /from/douyin-data.tar.gz"
```

恢复后再启动 `douyin-worker`。

---

## 7. 首次上线顺序

推荐顺序：

### 第一步：启动基础服务

```bash
docker compose up -d postgres redis
```

确认健康：

```bash
docker compose ps
```

### 第二步：启动后端和调度器

```bash
docker compose up -d backend scheduler
```

查看日志：

```bash
docker compose logs -f backend
docker compose logs -f scheduler
```

### 第三步：执行迁移和导入数据

如果是空环境：

```bash
docker compose exec backend python manage.py migrate --noinput
```

如果是迁移老库：

1. 先导入 PostgreSQL
2. 再执行 `migrate --noinput`
3. 再验证关键表是否正常

### 第四步：启动前端

如果前端也走 compose：

```bash
docker compose up -d web
```

如果前端是提前本地打包后交给 Nginx，则将 `web/apps/web-ele/dist/` 上传到 Nginx 静态目录。

### 第五步：启动 Douyin Worker

因为它还挂在 profile 下，启动命令是：

```bash
docker compose --profile douyin up -d douyin-worker
```

查看 worker 日志：

```bash
docker compose logs -f douyin-worker
```

---

## 8. 域名、DNS、Nginx、证书之间的关系

上线时这 4 件事的关系可以简单理解成：

```text
域名 DNS
  -> 服务器公网 IP
  -> Nginx :80/:443
  -> 证书挂在 Nginx 上
  -> Nginx 再把 / 和 /basic-api/ /ws/ 分发到前端或 Django
```

### 8.1 推荐域名方案

最简单的是单域名：

```text
your-domain.com
```

用途：

- `/` 前端
- `/basic-api/` Django API
- `/ws/` Django WebSocket

也可以双域名：

```text
admin.your-domain.com   -> 前端 + API + WS
api.your-domain.com     -> 只给 API
```

但对当前项目，单域名更简单，前端环境变量也更容易统一。

### 8.2 DNS 应该指向哪里

域名记录应该指向：

- 运行 Nginx 的那台 Linux 服务器公网 IP

不是指向：

- Docker 容器 IP
- PostgreSQL / Redis
- `backend` 容器名
- `douyin-worker` 容器名

典型记录：

```text
类型: A
主机记录: @
值: <你的服务器公网IP>

类型: A
主机记录: www
值: <你的服务器公网IP>
```

如果你用子域名：

```text
类型: A
主机记录: admin
值: <你的服务器公网IP>
```

### 8.3 证书应该绑在哪里

证书应该绑定在 Nginx 的 `443 ssl` server 上。

不是绑在：

- Django
- Uvicorn
- Docker Compose
- PostgreSQL
- Redis

也就是说：

- 外网浏览器访问的是 `https://your-domain.com`
- TLS 握手发生在 Nginx
- Nginx 解密后，再把普通 HTTP 代理给 `127.0.0.1:8000`

### 8.4 证书文件应该放哪里

常见两种方式：

#### 方式 A：Let's Encrypt

Certbot 通常会把证书放在：

```text
/etc/letsencrypt/live/your-domain.com/fullchain.pem
/etc/letsencrypt/live/your-domain.com/privkey.pem
```

Nginx 配置里就引用这两个文件。

#### 方式 B：商业证书

你手里通常会拿到：

- 证书公钥文件 `.crt` 或 `.pem`
- 私钥文件 `.key`
- 有时还有中间证书链

建议统一放：

```text
/etc/nginx/ssl/your-domain.com/
```

例如：

```text
/etc/nginx/ssl/your-domain.com/fullchain.pem
/etc/nginx/ssl/your-domain.com/privkey.key
```

### 8.5 前端和域名的关系

前端如果和 API 同域名部署：

- 前端访问 `https://your-domain.com/`
- API 走 `https://your-domain.com/basic-api/`
- WebSocket 走 `wss://your-domain.com/ws/`

这种情况下最省事。

如果前端生产包里 API 地址写死了，就要确保：

- `VITE_GLOB_API_URL` 和最终域名一致
- 改完后重新打包前端

### 8.6 Douyin Worker 和域名的关系

`douyin-worker` 是纯协议进程，不对外暴露任何端口，也不需要独立公网域名。

默认情况下：

- 外部域名只服务前端、API、WebSocket
- `douyin-worker` 留在 Docker 内部运行，仅通过 Redis 与后端通信

---

## 9. Nginx 配置

建议：

- `/` 指向前端静态目录
- `/basic-api/` 反代到 Django
- `/ws/` 反代到 Django WebSocket

示例：

```nginx
server {
    listen 80;
    server_name your-domain.com www.your-domain.com;

    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com www.your-domain.com;

    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    root /var/www/dyauthreply/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /basic-api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

如果你用商业证书，只需要把这两行改成你的真实文件路径：

```nginx
ssl_certificate /etc/nginx/ssl/your-domain.com/fullchain.pem;
ssl_certificate_key /etc/nginx/ssl/your-domain.com/privkey.key;
```

### 9.1 反代目标到底指向哪里

Nginx 的 `proxy_pass` 应该指向：

- 宿主机可访问的 Django 服务地址

当前项目默认就是：

```text
http://127.0.0.1:8000
```

前提是：

- Django 容器把 `8000` 映射到了宿主机
- Nginx 跑在宿主机上

不是指向：

- `https://your-domain.com/basic-api`
- PostgreSQL
- Redis
- `douyin-worker`

### 9.2 一张对照表

| 项目 | 应该指向 |
| --- | --- |
| 域名 A 记录 | Linux 服务器公网 IP |
| 浏览器 HTTPS | Nginx `:443` |
| SSL 证书绑定位置 | Nginx `server` |
| `ssl_certificate` | 证书链文件 |
| `ssl_certificate_key` | 私钥文件 |
| `/basic-api/` | `127.0.0.1:8000` |
| `/ws/` | `127.0.0.1:8000` |
| Douyin worker | 不需要独立公网域名，内部跑 |

---

## 10. 切流上线步骤

如果是从旧服务迁移到新服务，建议按这个顺序做：

1. 旧环境冻结写入
2. 导出 PostgreSQL
3. 打包 `douyin-data`
4. 在新服务器恢复数据
5. 启动新服务
6. 检查 API、前端、WebSocket、scheduler、worker
7. 修改 DNS 或 Nginx 上游，切流到新机器
8. 观察 30-60 分钟
9. 确认无误后再下线旧环境

如果你不能接受短暂停写，后面就要做双写或增量同步，但这套项目目前更适合“短时间停机迁移”。

---

## 11. 上线后验证清单

至少验证这些：

### Web / API

- 前端首页能打开
- 登录成功
- `/api/docs` 可访问
- `/ws/` 正常建立连接

### 数据

- 用户、菜单、角色数据正常
- PostgreSQL 表数量和关键数据量符合预期

### 调度器

- `scheduler` 进程在运行
- 定时任务列表存在
- 能看到调度日志

### Douyin Worker

- `douyin-worker` 容器正常
- 能看到心跳
- 历史托管账号显示正常
- 如迁移了托管数据，抽检 1 个账号确认登录态是否还有效

### 日志

日志统一查看：

```bash
docker compose logs -f backend
docker compose logs -f scheduler
docker compose logs -f douyin-worker
```

容器内文件日志位置：

```text
/app/logs/server.log
/app/logs/error.log
/app/logs/scheduler.log
/app/logs/douyin_worker.log
```

---

## 12. 日常发布流程

以后版本更新建议这样做：

```bash
cd /opt/DyAuthReply
git pull
docker compose build backend scheduler web
docker compose up -d backend scheduler web
docker compose exec backend python manage.py migrate --noinput
```

如果 worker 有改动：

```bash
docker compose --profile douyin build douyin-worker
docker compose --profile douyin up -d douyin-worker
```

说明：

- 由于 `douyin-worker` 还在 profile 下，它不会跟普通 `docker compose up -d` 一起重启。
- 这正适合你当前“主服务频繁重启，但不想带着 worker 一起动”的需求。

---

## 13. 回滚方案

建议每次上线前保留两样：

1. 上一个稳定版本的代码或镜像 tag
2. 上线前数据库备份

回滚顺序：

1. 停止新版本容器
2. 切回旧代码或旧镜像
3. 如涉及破坏性迁移，恢复数据库备份
4. 重新启动旧版本
5. 验证 API / 前端 / worker

如果本次上线只有代码变更、没有数据库结构破坏，通常只需要回滚镜像或 git 版本即可。

---

## 14. 常用运维命令

查看状态：

```bash
docker compose ps
docker compose --profile douyin ps
```

查看日志：

```bash
docker compose logs -f backend
docker compose logs -f scheduler
docker compose logs -f web
docker compose logs -f douyin-worker
```

重启主服务：

```bash
docker compose up -d backend scheduler
```

单独启动 worker：

```bash
docker compose --profile douyin up -d douyin-worker
```

单独重启 worker：

```bash
docker compose restart douyin-worker
```

进入 Django 容器：

```bash
docker compose exec backend bash
```

执行迁移：

```bash
docker compose exec backend python manage.py migrate --noinput
```

---

## 15. 推荐上线策略

对这个项目，当前最实用的是：

- 第一次正式上线：短暂停机迁移
- 后续小版本发布：滚动式重启 `backend/scheduler/web`
- `douyin-worker` 独立重启，避免影响托管会话

原因：

- `douyin-worker` 有浏览器上下文和托管状态，不适合跟普通 Web 发布完全绑定。
- 数据库和 Douyin 持久化状态一起迁移时，短暂停机最简单、最稳。

---

## 16. Django 一体化托管（最简单容器部署）部署与初始化流程

如果您采用我们将 Vue 前端打包同步进 `backend-django/dist`、并直接由 Django 服务端托管 SPA 路由的单域名部署方案，可以按照以下步骤在生产服务器上进行首次部署与初始化：

### 16.1 本地镜像打包与推送
1. 在本地运行前端编译并拷贝至 Django 目录的脚本：
   ```bash
   ./build_and_copy_frontend.sh
   ```
2. 构建本地后端镜像（已固化 dist 产物）：
   ```bash
   docker compose build backend
   ```
3. 登录私有镜像仓库并推送（以阿里云杭州节点为例）：
   ```bash
   docker login registry.cn-hangzhou.aliyuncs.com
   docker tag zq-platform/backend:dev registry.cn-hangzhou.aliyuncs.com/<namespace>/zq-backend:latest
   docker push registry.cn-hangzhou.aliyuncs.com/<namespace>/zq-backend:latest
   ```

### 16.2 服务器环境配置
1. 在宿主机安装 Docker 与 Docker Compose。
2. 建议将项目置于 `/opt/DyAuthReply`。
3. 创建 `.env` 环境变量配置文件并修改敏感项：
   ```dotenv
   # 数据库及 Redis 强密码
   POSTGRES_DB=zq_platform
   POSTGRES_USER=zq
   POSTGRES_PASSWORD=你的复杂强密码
   REDIS_PASSWORD=你的复杂强密码

   # Django 及 JWT 安全密钥
   DJANGO_SECRET_KEY=你的复杂随机串
   JWT_ACCESS_SECRET_KEY=你的复杂随机串
   JWT_REFRESH_SECRET_KEY=你的复杂随机串

   # 首次启动自动初始化开关
   AUTO_LOADDATA=true
   ```
4. 确认生产环境的 `docker-compose.yml` 中删除了 `build:` 编译节点，并将 `image` 指向您刚刚推送的私有镜像仓库。由于采用 Django 一体化托管，您可以**直接从 compose 配置文件中删除 web (Nginx) 服务节点**，只保留 `postgres`, `redis`, `backend`, `scheduler` 以及 `douyin-worker`。

### 16.3 首次一键初始化与服务启动
1. 启动基础服务与后端：
   ```bash
   docker compose up -d postgres redis backend scheduler
   ```
   *说明：镜像内置的 entrypoint 启动脚本会自动阻塞等待 PostgreSQL 和 Redis 启动，然后自动运行 `python manage.py migrate --noinput`，并由于设置了 `AUTO_LOADDATA=true`，会自动加载默认数据卷 `db_init.json`（菜单、角色与初始化权限）。*

2. 观察日志确认初始化完毕：
   ```bash
   docker compose logs -f backend
   ```

3. 在服务器上立即为自己创建一个超级管理员账号：
   ```bash
   docker compose exec backend python manage.py createsuperuser
   ```
   按照交互提示输入用户名、密码及邮箱即可。

4. 启动完成后，为了安全以及防止容器重启覆盖数据，建议立即将 `.env` 中的初始化开关关闭：
   ```dotenv
   AUTO_LOADDATA=false
   ```

5. 启动抖音消息扫描及自动回复 Worker 节点：
   ```bash
   docker compose --profile douyin up -d douyin-worker
   ```

此时，即可通过 `https://您的域名/manage/` 直接进行安全的超级管理员后台访问，无需部署独立的前端服务器！
