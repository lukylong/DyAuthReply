# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

zq-platform (芷青开发平台) 是一个企业级后台管理系统，采用前后端分离架构：
- **后端**: Django 5.2.7 + Django Ninja (REST API)
- **前端**: Vue 3 + Vite + Element Plus (pnpm monorepo)
- **核心特性**: 抖音私信自动回复模块（纯 HTTP 协议，无浏览器依赖）

## 快速启动

### Docker Compose（推荐）
```bash
# 首次启动：复制环境变量模板并配置
cp .env.example .env
# 编辑 .env 设置数据库密码、JWT 密钥等

# 构建并启动所有服务（PostgreSQL + Redis + Django 后端 + 前端）
docker compose up -d --build

# 首次启动：初始化数据
docker compose exec backend python manage.py loaddata db_init.json

# 查看日志
docker compose logs -f backend

# 启动抖音 worker（可选）
docker compose --profile douyin up -d douyin-worker

# 停止服务（保留数据）
docker compose down

# 清空数据库和 Redis（慎用）
docker compose down -v
```

**默认端口**:
- 前端: http://localhost:5777
- 后端 API: http://localhost:8000/api/docs
- PostgreSQL: localhost:5432
- Redis: localhost:6379

**默认账号**: `superadmin` / `123456`

### 本地开发

#### 后端（Django）
```bash
cd backend-django

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # macOS/Linux
# 或 venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt

# 配置数据库（编辑 env/dev_env.py 或设置环境变量）
# DATABASE_TYPE, DATABASE_HOST, DATABASE_PORT 等

# 数据库迁移
python manage.py makemigrations core scheduler
python manage.py migrate

# 初始化数据
python manage.py loaddata db_init.json

# 启动后端（开发）
python manage.py runserver 0.0.0.0:8000
# 或（生产级）
python -m uvicorn application.asgi:application --host 0.0.0.0 --port 8000 --reload

# 启动调度器（独立进程）
python start_scheduler.py

# 启动抖音 worker（独立进程，需先配置凭证加密密钥）
python start_douyin_worker.py
```

#### 前端（Vue 3 Monorepo）
```bash
cd web

# 安装依赖（需要 pnpm >= 9.12.0, Node.js >= 20.10.0）
pnpm install

# 启动开发服务器（自动启动 web-ele 应用）
pnpm dev

# 构建生产版本
pnpm build:ele

# 类型检查
pnpm run check:type

# 代码格式化
pnpm format

# 代码检查
pnpm lint
```

## 测试

### 后端测试
```bash
cd backend-django

# 运行所有测试
python manage.py test core.tests

# 运行单个测试文件
python manage.py test core.tests.test_douyin_account

# 运行单个测试用例
python manage.py test core.tests.test_douyin_account.DouyinAccountTestCase.test_create_account
```

### 前端测试
```bash
cd web

# 单元测试
pnpm test:unit

# E2E 测试
pnpm test:e2e
```

## 架构设计

### 后端模块结构

```
backend-django/
├── application/          # Django 项目配置（settings, urls, asgi, wsgi）
├── core/                 # 核心业务模块（Django app）
│   ├── auth/            # JWT 认证
│   ├── user/            # 用户管理
│   ├── role/            # 角色管理
│   ├── permission/      # 权限管理
│   ├── dept/            # 部门管理
│   ├── menu/            # 菜单管理
│   ├── dict/            # 数据字典
│   ├── file_manager/    # 文件管理
│   ├── server_monitor/  # 服务器监控
│   ├── redis_monitor/   # Redis 监控
│   ├── database_monitor/# 数据库监控
│   ├── douyin/          # 抖音私信自动回复（核心业务模块）
│   ├── kuaishou/        # 快手模块
│   ├── websocket/       # WebSocket 支持
│   └── router.py        # API 路由注册
├── scheduler/           # APScheduler 任务调度模块
├── common/              # 公共工具和中间件
├── env/                 # 环境配置（dev_env.py, prd_env.py, uat_env.py）
├── manage.py            # Django 管理脚本
├── start_scheduler.py   # 调度器独立进程入口
└── start_douyin_worker.py  # 抖音 worker 独立进程入口
```

### 抖音模块架构（core.douyin）

**核心特性**: 纯 HTTP 协议直连抖音 imapi，**无浏览器依赖**，所有签名和协议交互通过 JS 引擎（Node.js）完成。

**模块结构**:
```
core/douyin/
├── douyin_account_*.py       # 账号管理（model, schema, api）
├── douyin_rule_*.py          # 回复规则（关键词匹配、正则、优先级）
├── douyin_template_*.py      # 回复模板（支持变量占位符）
├── douyin_session_*.py       # 会话监控（心跳、状态）
├── douyin_event_*.py         # 运行时事件（登录、掉线、风控告警）
├── douyin_reply_log_*.py     # 回复日志审计
├── douyin_blacklist_*.py     # 黑名单管理
└── runtime/                  # Worker 核心引擎
    ├── worker.py            # 主协程调度器（扫描收件箱→匹配规则→发送回复）
    ├── credential.py        # 登录态管理（Cookie + web_protect + keys）
    ├── matcher.py           # 规则匹配引擎
    ├── storage.py           # 加密凭证存储（Fernet）
    ├── health.py            # 健康检查与自动降级
    └── transport/           # 传输层（纯 HTTP 协议）
        ├── http_protocol.py # 抖音 imapi 协议实现（扫描/发送）
        ├── js_sign_provider.py  # JS 签名引擎（PyExecJS 调用 dy_ab.js）
        ├── sign/js/         # JS 签名脚本（dy_ab.js, bd-ticket-guard）
        └── wire/            # Protobuf 编解码（dy_request_pb2）
```

**Worker 工作流程**:
1. 从数据库读取账号列表（按 `status + work_mode + priority` 排序）
2. 订阅 Redis `douyin:cmd:*` 频道接收控制指令（pause/resume/stop）
3. 每账号启动独立协程：
   - 使用导入的登录态（Cookie + web_protect + keys）初始化会话
   - 循环扫描收件箱（`http_protocol.py` 直连 imapi）
   - 匹配回复规则（黑名单/冷却/配额校验）
   - Protobuf 编码并发送回复
   - 写回复日志和事件流
4. 定期上报心跳和指标到数据库

**关键依赖**:
- **Node.js**: 必须安装，用于执行 JS 签名脚本（`dy_ab.js` 算 a_bogus + bd-ticket-guard）
- **PyExecJS**: Python 调用 Node.js 引擎
- **protobuf**: IM 私信协议编解码
- **httpx**: 异步 HTTP 客户端
- **cryptography**: Fernet 加密登录态

**环境变量**（在 `.env` 或 `docker-compose.yml` 中配置）:
```bash
# 生成 Fernet 密钥：python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
DOUYIN_STORAGE_ENCRYPTION_KEY=<Fernet密钥>
DOUYIN_DATA_DIR=/var/lib/zq-platform/douyin  # 凭证存储目录
DOUYIN_TRANSPORT_BACKEND=http_protocol       # 纯协议后端（唯一支持）
DOUYIN_SIGN_BACKEND=js                       # JS 签名后端（唯一支持）
DOUYIN_HTTP_PROTOCOL_SEND_STRICT=true        # 发送失败直接报错
DOUYIN_HTTP_PROTOCOL_SCAN_STRICT=true        # 扫描失败直接报错
```

**导入登录态操作流程**:
1. 在浏览器登录抖音创作者中心
2. 导出 Cookie（完整）
3. 从 `localStorage` 取出：
   - `security-sdk/s_sdk_crypt_sdk` (keys)
   - `security-sdk/s_sdk_sign_data_key/web_protect`
4. 在后台 `/douyin/account` 页面点击"导入登录态"粘贴保存
5. Worker 自动加密保存凭证并开始托管

### 前端模块结构（pnpm monorepo）

```
web/
├── apps/
│   └── web-ele/          # 主应用（Element Plus 版本）
│       ├── src/
│       │   ├── api/      # API 接口定义（axios）
│       │   ├── views/    # 页面组件
│       │   │   ├── douyin/  # 抖音模块前端页面
│       │   │   │   ├── account/     # 账号管理
│       │   │   │   ├── session/     # 会话监控
│       │   │   │   ├── rule/        # 回复规则
│       │   │   │   ├── template/    # 回复模板
│       │   │   │   └── dashboard/   # 数据看板
│       │   │   └── ...
│       │   ├── router/   # Vue Router 配置
│       │   └── store/    # Pinia 状态管理
│       └── package.json
└── packages/             # 共享包
    ├── @core/           # 核心工具
    ├── effects/         # 副作用处理
    ├── hooks/           # Vue Composition API Hooks
    ├── icons/           # 图标库
    ├── locales/         # 国际化
    └── utils/           # 工具函数
```

## 开发规范

### 后端 API 开发（Django Ninja）

```python
# core/<module>/<module>_api.py
from ninja import Router
from django.db import transaction
from common.fu_response import FuResponse
from .<module>_schema import CreateSchema, UpdateSchema
from .<module>_model import Model

router = Router()

@router.get("", summary="列表查询")
def list_items(request, page: int = 1, page_size: int = 10):
    """
    列表查询接口
    """
    # 实现逻辑
    return FuResponse(data=result)

@router.post("", summary="新增")
@transaction.atomic
def create_item(request, payload: CreateSchema):
    """
    新增接口
    """
    # 实现逻辑
    return FuResponse(message="创建成功")
```

### 数据库迁移

```bash
# 创建迁移文件
python manage.py makemigrations core scheduler

# 应用迁移
python manage.py migrate

# 查看迁移状态
python manage.py showmigrations

# 回滚迁移
python manage.py migrate core <migration_name>
```

### 前端 API 调用

```typescript
// web/apps/web-ele/src/api/core/douyin/account.ts
import { requestClient } from '#/api/request';

export namespace DouyinAccountApi {
  export function getAccountList(params: QueryParams) {
    return requestClient.get<ListResponse>('/core/douyin/account', { params });
  }

  export function createAccount(data: CreateAccountDto) {
    return requestClient.post('/core/douyin/account', data);
  }
}
```

## 常见问题

### 抖音 Worker 无法启动
1. 检查是否已生成 `DOUYIN_STORAGE_ENCRYPTION_KEY`
2. 确认 Node.js 已安装（执行 `node --version`）
3. 查看日志：`docker compose logs -f douyin-worker` 或 `tail -f backend-django/logs/douyin_worker.log`

### 数据库连接失败
1. 确认数据库服务已启动（Docker: `docker compose ps`）
2. 检查 `.env` 或 `env/dev_env.py` 中的数据库配置
3. 测试连接：`docker compose exec backend python manage.py dbshell`

### 前端构建失败
1. 确认 Node.js >= 20.10.0, pnpm >= 9.12.0
2. 清理依赖重新安装：`pnpm clean && pnpm install`
3. 检查 TypeScript 类型错误：`pnpm run check:type`

### 签名失败（a_bogus 或 bd-ticket-guard）
1. 确认 Node.js 已安装且版本 >= 18
2. 检查 `backend-django/core/douyin/runtime/transport/sign/js/` 下的 JS 签名脚本是否完整
3. 查看 worker 日志中的签名错误详情
4. 如果抖音更新了签名算法，需要更新 `dy_ab.js` 和 `bd-ticket-guard` 脚本

## API 文档

- **Swagger UI**: http://localhost:8000/api/docs
- **ReDoc**: http://localhost:8000/api/redoc

## 数据导出/导入

```bash
# 导出数据
python manage.py dumpdata > db_backup.json

# 导入数据
python manage.py loaddata db_backup.json

# 仅导出特定 app
python manage.py dumpdata core scheduler > core_scheduler_data.json
```

## 调度任务配置

在 `scheduler/tasks.py` 中定义定时任务，APScheduler 会自动加载：

```python
# scheduler/tasks.py
from apscheduler.triggers.cron import CronTrigger

def douyin_reset_daily_quota():
    """每日零点重置所有账号的回复配额"""
    # 实现逻辑
    pass

# 任务注册（在 start_scheduler.py 中通过装饰器或手动添加）
```

**推荐任务**:
- `douyin_reset_daily_quota`: 每日零点（cron: `0 0 * * *`）
- `douyin_aggregate_daily_stats`: 每小时（cron: `5 * * * *`）

## 生产部署

参考项目根目录的以下文档：
- `DEPLOY.zh-CN.md`: 完整部署指南
- `MIGRATION_DEPLOY.zh-CN.md`: 迁移部署指南
- `docker-compose.yml`: Docker 部署配置

## 相关文档

- 抖音 IM 协议分析：`backend-django/core/douyin/IM_PROTOCOL_ANALYSIS.md`
- 抖音发送协议分析：`backend-django/core/douyin/IM_SEND_PROTOCOL_ANALYSIS.md`
