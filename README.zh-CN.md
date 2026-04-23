# zq-platform(芷青开发平台)

[English](./README.md) | 简体中文

<div align="center">

一个现代化的企业级后台管理系统，提供 Django 和 FastAPI 双后端选择 + Vue3 + Element Plus 构建

[![Django](https://img.shields.io/badge/Django-5.2.7-green.svg)](https://www.djangoproject.com/)
[![Vue](https://img.shields.io/badge/Vue-3.x-brightgreen.svg)](https://vuejs.org/)
[![Element Plus](https://img.shields.io/badge/Element%20Plus-latest-blue.svg)](https://element-plus.org/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

</div>

## 官方网站
[https://zq-platform.com](https://zq-platform.com/)

## 官方文档
[https://doc.zq-platform.com](https://doc.zq-platform.com/)

## 演示链接
[https://demo.zq-platform.com](https://demo.zq-platform.com/)

## 演示账户
**账号**: yangfei

**密码**: 123456

## 📞 联系合作方式

如有问题或建议，请通过以下方式联系：

- Issue: [GitHub Issues](../../issues)
- Email: jiangzhikj@outlook.com
- 微信: dlpuzcl
- QQ Group: 1073561328

<img src="qq.png" alt="qq" width="200" />

## 📖 项目简介

zq-platform 是一个功能完善的企业级后台管理系统解决方案，采用前后端分离架构。提供两种后端选择：Django 5.2 + Django Ninja 或 FastAPI + SQLAlchemy 异步 ORM，前端基于 Vue 3 + Vben Admin + Element Plus 打造现代化的管理界面。

### ✨ 核心特性

- 🎯 **完整的 RBAC 权限系统** - 用户、角色、权限、部门、岗位多维度权限控制
- 🔐 **JWT 认证机制** - 安全的 Token 认证，支持 Access Token 和 Refresh Token
- 📊 **系统监控** - 服务器监控、Redis 监控、数据库监控，实时掌握系统状态
- 📁 **文件管理** - 完善的文件上传、下载、预览功能
- 📝 **操作日志** - 详细的登录日志和操作审计
- 🗂️ **数据字典** - 灵活的字典管理，支持多级分类
- ⏰ **任务调度** - 基于 APScheduler 的定时任务管理
- 🔌 **WebSocket 支持** - 实时通信能力
- 🌐 **多数据库支持** - MySQL、PostgreSQL、SQL Server、SQLite
- 🎨 **现代化 UI** - 响应式设计，支持暗黑模式
- 📦 **Monorepo 架构** - 基于 pnpm workspace 的前端工程化方案

## 🏗️ 技术栈

### 后端技术

**Django 后端 (backend-django)**
- **核心框架**: Django 5.2.7
- **API 框架**: Django Ninja 1.4.5 (高性能 API 框架)
- **认证**: PyJWT 2.8.0
- **任务调度**: APScheduler 3.10.4
- **缓存**: Redis + django-redis
- **WebSocket**: Django Channels 4.2
- **数据库驱动**: psycopg2-binary, pymysql, pyodbc
- **服务器**: Uvicorn 0.38.0 / Gunicorn 23.0.0
- **其他**: openpyxl, geoip2, psutil, cryptography

**FastAPI 后端 (backend-fastapi)**
- **核心框架**: FastAPI 0.115+
- **ORM**: SQLAlchemy 2.0+ (异步)
- **数据库**: PostgreSQL 16+
- **迁移**: Alembic
- **认证**: JWT
- **缓存**: Redis
- **Python**: 3.12+

### 前端技术

- **核心框架**: Vue 3.x
- **构建工具**: Vite 5.x
- **UI 组件库**: Element Plus
- **状态管理**: Pinia
- **路由**: Vue Router
- **HTTP 客户端**: Axios
- **工具库**: VueUse, dayjs, lodash-es
- **代码规范**: ESLint, Prettier, Stylelint
- **包管理**: pnpm 10.14.0
- **Monorepo**: Turbo

## 📁 项目结构

```
zq-platform/
├── backend-django/          # Django 后端
│   ├── application/         # 项目配置
│   ├── core/               # 核心业务模块
│   │   ├── auth/           # 认证授权
│   │   ├── user/           # 用户管理
│   │   ├── role/           # 角色管理
│   │   ├── permission/     # 权限管理
│   │   ├── dept/           # 部门管理
│   │   ├── post/           # 岗位管理
│   │   ├── menu/           # 菜单管理
│   │   ├── dict/           # 字典管理
│   │   ├── login_log/      # 登录日志
│   │   ├── file_manager/   # 文件管理
│   │   ├── server_monitor/ # 服务器监控
│   │   ├── redis_monitor/  # Redis 监控
│   │   ├── redis_manager/  # Redis 管理
│   │   ├── database_monitor/ # 数据库监控
│   │   └── database_manager/ # 数据库管理
│   ├── scheduler/          # 任务调度模块
│   ├── common/             # 公共模块
│   ├── env/                # 环境配置
│   ├── requirements.txt    # Python 依赖
│   └── manage.py          # Django 管理脚本
│
├── backend-fastapi/         # FastAPI 后端（可选）
│   ├── app/                # 核心应用模块
│   ├── core/               # 核心业务模块
│   ├── scheduler/          # 定时任务模块
│   ├── scripts/            # 工具脚本
│   ├── alembic/            # 数据库迁移
│   ├── env/                # 环境配置
│   ├── requirements.txt    # Python 依赖
│   └── main.py            # 应用入口
│
└── web/                    # Vue 前端 (Monorepo)
    ├── apps/
    │   └── web-ele/        # Element Plus 版本主应用
    │       ├── src/
    │       │   ├── api/    # API 接口
    │       │   ├── views/  # 页面组件
    │       │   ├── router/ # 路由配置
    │       │   └── store/  # 状态管理
    │       └── package.json
    ├── packages/           # 共享包
    │   ├── @core/          # 核心包
    │   ├── effects/        # 副作用包
    │   ├── hooks/          # Hooks
    │   ├── icons/          # 图标
    │   ├── locales/        # 国际化
    │   ├── stores/         # 状态管理
    │   └── utils/          # 工具函数
    ├── internal/           # 内部工具
    └── package.json        # 根配置
```

## 🚀 快速开始

### 方式一：Docker Compose 一键启动（推荐）

仓库根目录提供 [`docker-compose.yml`](docker-compose.yml)，一条命令拉起 PostgreSQL、Redis、Django 后端、APScheduler、抖音 worker（可选）与 Vue 前端 dev server：

```bash
# 1. 复制环境变量模板（首次使用）
cp .env.example .env
# 按需修改 .env 中的密码、JWT 密钥等

# 2. 构建并启动所有服务（后台）
docker compose up -d --build

# 3. 查看日志
docker compose logs -f backend

# 4. 首次启动自动执行 migrate；如需初始化账号数据
docker compose exec backend python manage.py loaddata db_init.json

# 5. 启动抖音 worker 进程（M2 里程碑之后，可选 profile）
docker compose --profile douyin up -d douyin-worker
```

启动完成后访问：

| 服务 | 地址 |
| ---- | ---- |
| 前端 Web | http://localhost:5777 |
| 后端 API | http://localhost:8000/api/docs |
| PostgreSQL | `localhost:5432`（账号见 `.env`） |
| Redis | `localhost:6379` |

常用命令：

```bash
docker compose down          # 停止但保留数据卷
docker compose down -v       # 同时清空 PostgreSQL/Redis/日志（慎用）
docker compose restart backend
docker compose exec backend python manage.py createsuperuser
```

### 方式二：本地原生部署

#### 环境要求

- **后端**
  - Python >= 3.10
  - MySQL >= 5.7 / PostgreSQL >= 12 / SQL Server
  - Redis >= 5.0

- **前端**
  - Node.js >= 20.10.0
  - pnpm >= 9.12.0

### 后端安装

#### 选项 1: Django 后端（推荐用于生产环境）

1. **克隆项目**
```bash
git clone https://github.com/jiangzhikj/zq-platform.git
cd zq-platform/backend-django
```

2. **创建虚拟环境**
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate     # Windows
```

3. **安装依赖**
```bash
pip install -r requirements.txt
```

4. **配置环境变量**
```bash
cp env
# 编辑 .env 文件，配置数据库、Redis、JWT 密钥等
```

主要配置项：
```env

# JWT 密钥
JWT_ACCESS_SECRET_KEY=your-jwt-access-secret
JWT_REFRESH_SECRET_KEY=your-jwt-refresh-secret

# 数据库配置
DATABASE_TYPE=MYSQL  # MYSQL/POSTGRESQL/SQLSERVER/SQLITE3
DATABASE_HOST=127.0.0.1
DATABASE_PORT=3306
DATABASE_USER=root
DATABASE_PASSWORD=password
DATABASE_NAME=zq_admin

# Redis 配置
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_DB=2
```

5. **数据库迁移**
```bash
python manage.py makemigrations core scheduler
python manage.py migrate
```

6. **初始化数据**
```bash
python manage.py loaddata db_init.json
```

7. **启动服务**
```bash
# 开发环境
python manage.py runserver 0.0.0.0:8000

```

8. **启动任务调度器（可选）**
```bash
# 生产环境
python start_scheduler.py
```

#### 选项 2: FastAPI 后端（推荐用于高性能场景）

1. **进入 FastAPI 目录**
```bash
cd zq-platform/backend-fastapi
```

2. **创建虚拟环境**
```bash
conda create -n zq-fastapi python=3.12
conda activate zq-fastapi
```

3. **安装依赖**
```bash
pip install -r requirements.txt
```

4. **配置环境变量**
```bash
cp env/example.env env/dev.env
# 编辑 env/dev.env 配置数据库连接
```

5. **数据库迁移**
```bash
alembic revision --autogenerate -m "init tables"
alembic upgrade head

# 导入初始数据（可选）
python scripts/loaddata.py db_init.json
```

6. **启动服务**
```bash
python main.py
# 或
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

7. **访问 API 文档**
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### 前端安装

1. **进入前端目录**
```bash
cd zq-platform/web
```

2. **安装依赖**
```bash
pnpm install
```

3. **配置环境变量**
```bash
cd apps/web-ele
cp .env.development .env
# 编辑 .env 文件，配置后端 API 地址
```

4. **启动开发服务器**
```bash
# 在 web 根目录下
pnpm dev
```

5. **构建生产版本**
```bash
pnpm build:ele
```

## 📝 默认账号

初始化数据后，可使用以下账号登录：

- 账号: `superadmin`
- 密码: 请查看 `123456` 或联系管理员

## 🔧 主要功能模块

### 系统管理
- **用户管理**: 用户的增删改查、密码重置、状态管理
- **角色管理**: 角色权限分配、数据权限控制
- **权限管理**: 接口权限、按钮权限细粒度控制
- **部门管理**: 树形部门结构管理
- **岗位管理**: 岗位信息维护
- **菜单管理**: 动态菜单配置、路由管理
- **字典管理**: 系统字典维护

### 系统监控
- **服务器监控**: CPU、内存、磁盘、网络实时监控
- **Redis 监控**: Redis 性能指标、键值管理
- **数据库监控**: 数据库连接、性能监控
- **登录日志**: 用户登录记录、IP 地理位置

### 任务调度
- **定时任务**: Cron 表达式配置
- **任务日志**: 执行历史、结果查看
- **任务管理**: 启动、停止、立即执行

### 文件管理
- **文件上传**: 支持多文件上传
- **文件预览**: 图片、文档在线预览
- **文件下载**: 批量下载功能

### 抖音私信自动回复（`core.douyin`）

**基础能力**
- **账号托管**: 扫码登录创作者中心，加密保存 Playwright `storage_state`，支持多账号并发多会话
- **关键词规则**: 包含/正则/兜底三类匹配，可引用模板或直接配置文本 + 多链接、优先级与冷却时间
- **风控策略**: 每日回复上限、最小/最大间隔、静默时段、失败熔断
- **实时监控**: WebSocket 推送新消息 / 登录失效 / 心跳状态，前端实时展示
- **审计日志**: 每次回复的命中规则、耗时、结果、失败原因完整留痕
- **独立 worker**: Playwright 常驻进程（`start_douyin_worker.py`）托管每个账号一个浏览器上下文，订阅 Redis 命令、扫描收件箱、匹配规则并人类化发送回复

**多账号托管增强（已交付）**

| 模块 | 说明 | 路径 |
| --- | --- | --- |
| 托管看板 | 今日实时概览（账号/会话/消息/成功率）、近 7 日趋势、账号排行、规则命中分布 | `/douyin/dashboard` |
| 账号管理 | 多账号录入、扫码登录、状态切换、批量导入（JSON/CSV），支持标签、代理、UA、优先级、工作模式（全自动/人工/混合） | `/douyin/account` |
| 账号分组 | 按业务线/团队分组，分组内统一下发默认策略（日上限、间隔、静默时段） | `/douyin/account-group` |
| 会话监控 | 实时显示 worker 托管的每个浏览器会话（心跳、CPU、内存、今日消息/回复/错误），支持暂停/恢复/重启/停止 | `/douyin/session` |
| 回复模板 | 可复用的"文本 + 链接 + 变量占位符（`{{nickname}}` 等）"，分类管理，支持预览渲染；规则与快捷回复均可引用 | `/douyin/template` |
| 快捷回复 | 人工客服介入时的短语片段，带快捷键（`/hi`、`/price`） | `/douyin/quick-reply` |
| 回复规则 | 关键词/正则匹配，引用模板，支持时间窗口、周规则、渠道（私信/评论） | `/douyin/rule` |
| 黑名单 | sec_uid / 昵称关键词 / 内容关键词，作用范围可选全局/分组/账号 | `/douyin/blacklist` |
| 运行事件 | 登录、掉线、风控告警、发送失败等事件流，支持级别筛选与已读 | `/douyin/event` |
| 回复日志 | 每次自动回复的详细审计记录 | `/douyin/reply-log` |

**关键接口（worker 对接）**

```text
POST /api/core/douyin/session/heartbeat        # worker 定期上报心跳 + 指标
POST /api/core/douyin/session/{id}/control     # 后台下发 pause/resume/stop/restart
POST /api/core/douyin/event/report             # worker 上报运行时事件
POST /api/core/douyin/account/batch/import     # 批量导入账号（支持 skip_duplicate）
POST /api/core/douyin/template/preview         # 模板变量渲染预览
GET  /api/core/douyin/dashboard/overview       # 看板概览
GET  /api/core/douyin/dashboard/trend          # 趋势数据
GET  /api/core/douyin/dashboard/account-rank   # 账号排行
```

**数据模型一览**

`DouyinAccount` · `DouyinAccountGroup` · `DouyinRule` · `DouyinTemplate` · `DouyinTemplateCategory` · `DouyinQuickReply` · `DouyinBlacklist` · `DouyinConversation` · `DouyinMessage` · `DouyinReplyLog` · `DouyinSession` · `DouyinEvent` · `DouyinDailyStat`

> 菜单由 `core/migrations/0003_seed_douyin_menus.py` 自动注入，`migrate` 完成后登录后台即可看到"抖音托管"目录。

#### 启动抖音 Worker（业务引擎）

`backend-django` 仅提供管理后台与 API，真正跑浏览器自动化的是独立进程 `start_douyin_worker.py`。它会：

- 订阅 Redis `douyin:cmd:*` 频道接收登录/登出/会话控制指令
- 每 15 秒扫描 DB，按账号 `status + work_mode + priority` 决定启停协程
- 每账号一个 Playwright `BrowserContext`（独立 user_data_dir + storage_state）
- 扫码登录时把二维码实时推送到前端的 `/ws/douyin/`
- 循环扫描私信 → 规则匹配 → 冷却/黑名单/配额校验 → 人类化输入发送 → 写回复日志

**本地启动（需先 `pip install -r requirements.txt` 并 `playwright install chromium`）：**

```bash
# 1. 生成 Fernet 登录态加密密钥，写入 .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# 把输出粘贴到 .env 的 DOUYIN_STORAGE_ENCRYPTION_KEY=...

# 2. 首次扫码登录需要有头浏览器
echo "DOUYIN_WORKER_HEADLESS=False" >> .env

# 3. 启动 worker
cd backend-django
python start_douyin_worker.py
```

**前端扫码登录操作流程：**

1. `/douyin/account` 页面点击"新增账号"录入一个抖音账号（昵称任意）
2. 点击该账号行的"扫码登录"按钮
3. 弹窗内会实时显示二维码（通过 WebSocket `/ws/douyin/` 推送），用抖音 APP 扫码即可
4. 登录成功后 worker 自动保存加密登录态并开始托管
5. 之后进入"会话监控"可看到账号的 CPU/内存/心跳等实时状态

**调度任务（建议在 APScheduler 里配置）：**

| 任务代码 | 建议 cron | 作用 |
| --- | --- | --- |
| `scheduler.tasks.douyin_reset_daily_quota` | `0 0 * * *` | 每日零点重置所有账号的 `reply_today` 与会话日计数 |
| `scheduler.tasks.douyin_aggregate_daily_stats` | `5 * * * *` | 每小时聚合当日消息/回复/错误指标到 `DouyinDailyStat` |

> 抖音创作者中心页面会改版，`core/douyin/runtime/selectors.py` 集中维护了所有 DOM 选择器，每类都给了多个候选；若扫描失败请优先更新此文件。

## 🔐 API 文档

**Django 后端**
- Swagger UI: `http://localhost:8000/api/docs`
- ReDoc: `http://localhost:8000/api/redoc`

**FastAPI 后端**
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## 🛠️ 开发指南

### 后端开发

1. **添加新模块**
   - 在 `core/` 或创建新 app
   - 定义 models、schemas、services、api
   - 在 router 中注册路由

2. **API 开发规范**
   - 使用 Django Ninja 装饰器
   - 统一返回格式
   - 异常处理
   - 权限验证

### 前端开发

1. **添加新页面**
   - 在 `src/views/` 创建页面组件
   - 在 `src/router/routes/modules/` 添加路由
   - 在 `src/api/` 添加接口定义

2. **组件开发规范**
   - 使用 Element Plus 组件
   - 优先使用 Tailwind CSS
   - 支持暗黑模式
   - 图标从 `@vben/icons` 导入

## 📦 部署
1. **后端部署**
   - 使用 Gunicorn + Nginx
   - 配置 Supervisor 进程守护
   - 配置 SSL 证书

2. **前端部署**
   - 执行 `pnpm build` 构建
   - 将 `dist` 目录部署到 Nginx
   - 配置反向代理

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！

1. Fork 本项目
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request


## 🙏 致谢

- [Django](https://www.djangoproject.com/) - 强大的 Python Web 框架
- [Django Ninja](https://django-ninja.rest-framework.com/) - 快速的 Django REST 框架
- [Vue Vben Admin](https://github.com/vbenjs/vue-vben-admin) - 优秀的 Vue3 后台管理模板
- [Element Plus](https://element-plus.org/) - 基于 Vue 3 的组件库
---

<div align="center">
  Made with ❤️ by ZQ Team
</div>
