# DyAuthReply 桌面客户端 — 产品与技术规划

> 版本：v0.1 规划稿  
> 分支建议：`feature/client-app`（与 `feature/standalone-local` 可合并或独立）  
> 原则：**新目录、新 UI、复用后端核心；不动现有 `web-ele` 管理后台。**  
> **开发顺序：先跑通业务功能，令牌 / RBAC / 权限菜单后置（见 §3.1）。**

---

## 3.1 开发优先级（已确认）

**当前阶段：功能实现优先，鉴权与权限体系暂不投入。**

| 优先级 | 范围 | 说明 |
|--------|------|------|
| **P0（现在）** | 账号导入、会话、手动/自动回复、规则、模板、日志 | 客户端 UI + 复用 `core.douyin` API |
| **P1** | 托盘、打包 `.app`/`.exe`、首次向导 | 可交付给非技术用户 |
| **P2（后置）** | JWT 登录、本机访问密码、多用户 | 单机客户端默认不需要 |
| **P2（后置）** | 角色 / 菜单 / 接口权限校验 | 管理后台已有；客户端 **不暴露** 用户/角色 API 即可 |
| **P3（后置）** | 远程访问、多租户、云端同步 | 与「本地傻瓜版」目标无关 |

**M1 已采用的简化鉴权（够用即可，后续可换）：**

- 客户端 API 仅监听 `127.0.0.1`，`LocalDesktopAuth` 自动注入内置 `local` 超级用户
- 前端 **不传** Bearer Token，不做登录页
- 复用现有 `douyin_*_api` 时，`request.auth.id` 指向该本地用户，满足 `owner_id` 写入

**后续补权限时**：在 `core/client/` 增加可选 JWT 或本机 PIN，默认仍可无登录；不影响已实现的业务接口路径。

---

## 1. 为什么要单独做客户端

| 现状（zq-platform 管理后台） | 客户端目标 |
|------------------------------|------------|
| 面向运维/管理员，功能全、菜单多 | 面向运营/店主，只关心「账号 + 私信 + 自动回复」 |
| 需 Docker / 服务器 / Nginx | 双击安装，本机运行 |
| 阿里云机房易 7911 | 家用宽带出口，发信更稳 |
| 学习成本高 | 3 步上手：安装 → 导登录态 → 开自动回复 |

---

## 2. 与现有项目的关系（隔离 + 复用）

```text
DyAuthReply/                          ← 现有单体仓库，保持不变
├── backend-django/core/douyin/       ← 【复用】协议、Worker、凭证、规则引擎
├── web/apps/web-ele/                 ← 【不动】完整管理后台
├── docker-compose*.yml               ← 【不动】服务器部署
└── dyauthreply-client/               ← 【新建】桌面客户端（本规划）
    ├── desktop/                      Tauri 壳（.app / .exe）
    ├── client-ui/                    精简 Vue 客户端界面
    ├── slim-api/ 或 embed-backend/   可选：裁剪版 API 服务
    └── docs/
```

### 复用策略（推荐）

| 层级 | 做法 | 说明 |
|------|------|------|
| 抖音协议 / Worker | **Python 包引用或子进程** | 直接调用 `core.douyin.runtime`，不 fork 业务逻辑 |
| 数据存储 | **SQLite + 本地文件** | 单用户，无需 Postgres |
| Redis | **客户端无需安装**（SQLite 命令队列替代 pub/sub） | 服务器版仍用 Redis；安装包不依赖用户自备 Redis |
| 鉴权 / RBAC / 菜单 | **砍掉** | 本地单用户，启动即登录（或可选简单密码） |
| 前端 | **全新 `client-ui`** | 不引用 `web-ele` 路由与 layout |

### 明确不做的（第一期）

- 用户/角色/部门/权限/字典/监控大屏
- 快手模块、工作流、低代码表单、文件管理器
- 多租户、远程协作、云端同步账号

---

## 3. 核心功能范围（MVP）

### 3.1 必须有（P0）

| 功能 | 说明 |
|------|------|
| 一键启动/退出 | 托盘图标；退出时优雅停 Worker |
| 抖音账号管理 | 导入 `DYCRED1` 一键串；显示在线/失效/可发送 |
| 会话列表 | 最近私信、未读标记、点进看记录 |
| 手动回复 | 选中会话发文字 |
| 自动回复规则 | 关键词/正则、优先级、启用开关 |
| 回复模板 | 文本模板 + 变量占位 |
| 回复日志 | 成功/失败/跳过原因（配额、黑名单、静默时段） |
| 基础设置 | 回复间隔、日配额、静默时段 |

### 3.2 应该有（P1 — 功能向）

| 功能 | 说明 |
|------|------|
| 浏览器扩展联动 | 安装包内附带或引导安装 `douyin-cred-extractor` |
| 凭证过期提醒 | 托盘通知 + 页内引导重新导入 |
| 黑名单 | 用户 / 关键词 |
| 快捷回复 | 快捷键短语 |
| 开机自启 | 系统可选 |

### 3.3 后置（P2+ — 鉴权 / 权限 / 安全，**当前不做**）

| 功能 | 说明 |
|------|------|
| JWT / 刷新令牌 | 客户端本地单用户，暂不需要 |
| 本机访问密码 | 有需求再加 |
| RBAC / 菜单权限 | 仅管理后台需要 |
| API 细粒度权限表 | 客户端路由不暴露管理类接口 |

### 3.4 可后续（P2 产品向）

| 功能 | 说明 |
|------|------|
| 多账号（≤10） | 列表 + 分标签，单 Worker 轮询 |
| 数据备份/恢复 | 导出 `data/` 压缩包 |
| 自动更新 | Tauri 内置 updater |
| 7911 / 代理 | 高级设置里可选 HTTP 代理（给懂的人） |

---

## 4. 客户端信息架构（页面重构）

不用左侧大菜单，改为 **「任务导向」三栏/两步布局**：

```text
┌─────────────────────────────────────────────────────────┐
│  DyAuthReply          [账号 ▼]  [●运行中]  [设置]      │
├──────────┬──────────────────────────┬───────────────────┤
│ 会话列表  │  聊天区 + 输入框          │  侧栏（可折叠）    │
│          │                          │  · 本账号状态      │
│ 🔴 未读   │  [对方消息]              │  · 自动回复 开/关  │
│ 张三      │  [我的回复]              │  · 今日已回 N/M   │
│ 李四      │  ─────────────────       │  · 快捷回复       │
│ ...      │  [输入…] [发送]          │                   │
├──────────┴──────────────────────────┴───────────────────┤
│  底部：规则 | 模板 | 日志 | 账号与登录态                    │
└─────────────────────────────────────────────────────────┘
```

### 页面清单（约 6 个主屏 + 2 个向导）

| 路由 | 名称 | 备注 |
|------|------|------|
| `/` | 会话工作台 | 默认首页，三栏 IM 体验 |
| `/onboarding` | 首次向导 | ①装扩展 ②登录创作者 ③粘贴 DYCRED1 |
| `/accounts` | 我的抖音号 | 导入/更新凭证、昵称头像 |
| `/rules` | 自动回复规则 | 表格 + 简单表单，无复杂筛选 |
| `/templates` | 回复模板 | 卡片列表 |
| `/logs` | 回复记录 | 时间线，可搜会话 |
| `/settings` | 设置 | 间隔、配额、静默、代理（高级）、关于 |

**无登录页（M2 起）**：直接进会话工作台；本机访问密码 / JWT 见 §3.3 后置项。

---

## 5. 技术架构

```text
┌─────────────────────────────────────────────────────────┐
│  Tauri 2 桌面壳 (.app / .exe)                            │
│  · 启动画面 / 托盘 / 单实例锁 / 自动打开主窗口              │
│  · WebView 加载 client-ui（开发）或内嵌 dist（发布）         │
│  · 子进程：redis-server + Python 后端 + douyin-worker      │
└───────────────────────────┬─────────────────────────────┘
                            │ HTTP localhost:PORT
                            ▼
┌─────────────────────────────────────────────────────────┐
│  Slim Backend（裁剪 Django 或 FastAPI 包装层）            │
│  · 仅挂载 douyin 相关 API + 健康检查 + 静态 client-ui     │
│  · ZQ_ENV=client  或 独立 settings_client.py              │
│  · SQLite：~/Library/Application Support/DyAuthReply/     │
└───────────────────────────┬─────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│  core.douyin.runtime（复用现有仓库代码，pip -e 或拷贝）     │
│  Worker / http_protocol / frontier_ws / credential       │
└─────────────────────────────────────────────────────────┘
```

### 技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 桌面壳 | **Tauri 2**（**已定，不改用 PyQt**） | 原生 `.app`/`.exe`；Rust 管进程；比 Electron 轻 |
| 客户端 UI | **Vue 3 + Vite** | 团队熟悉；与 web-ele **代码不共享**，只共享 API 契约 |
| UI 组件 | **Naive UI 或 Element Plus 精简主题** | 偏 C 端，减少「后台感」 |
| 本地 API | **Django Ninja 子集**（第一期） | 复用现有 `douyin_*_api.py`，用新 `client_router` 聚合 |
| 数据库 | **SQLite** | 已支持 |
| 打包 | **tauri build** + CI 打 Mac/Win 安装包 | |

### API 设计原则

- 路由前缀 **`/api/client/v1/`**，聚合抖音相关 handler
- **不暴露** `/api/core/user`、`/api/core/role`、菜单/权限等管理接口
- **现阶段不做** 客户端专用 Token 签发与权限中间件；仅 `127.0.0.1` + 本地超级用户（见 §3.1）
- 现有 `douyin_*` schema 尽量复用，减少重复 DTO

---

## 6. 目录结构（规划）

```text
dyauthreply-client/
├── README.md
├── PLAN.zh-CN.md                 ← 本文档
├── docs/
│   ├── UI-WIREFRAME.md           线框图（后续）
│   └── BUILD.md                  打包与签名说明（后续）
│
├── desktop/                      Tauri 工程
│   ├── src-tauri/                Rust：进程管理、托盘、单实例
│   └── package.json
│
├── client-ui/                    全新 Vue 客户端
│   ├── src/
│   │   ├── views/                上表 6 个主屏
│   │   ├── components/           ChatList, MessageBubble, RuleForm…
│   │   ├── api/                  仅 client API
│   │   └── stores/
│   ├── package.json
│   └── vite.config.ts            开发时 proxy → localhost:8765
│
├── slim-api/                     可选：独立轻量入口
│   ├── manage_client.py          或 start_client_api.py
│   ├── router.py                 聚合 douyin 路由
│   └── settings_client.py        SQLite + 无 JWT 或固定本机 token
│
├── launcher/                     跨平台启动器（被 Tauri 调用）
│   ├── launcher.py               redis + migrate + api + worker
│   ├── runtime/                  发布时打入：python、node、redis（占位）
│   └── scripts/
│       ├── build-macos.sh
│       └── build-windows.ps1
│
└── extension/                    扩展安装指引（软链或复制 browser-extension）
```

> **约束**：`dyauthreply-client/**` 内不得 `import` 或修改 `web/apps/web-ele/**`；对后端的依赖仅通过 **HTTP API** 或 **子进程 + PYTHONPATH 指向 `backend-django`**。

---

## 7. 数据与安装路径

| 平台 | 用户数据目录 |
|------|-------------|
| macOS | `~/Library/Application Support/DyAuthReply/` |
| Windows | `%APPDATA%\DyAuthReply\` |

目录内容：

```text
data/
  db.sqlite3
  douyin/          加密凭证
  logs/
  media/
config.json        端口、自启、本机密码（可选）
```

与开发仓库 `data/standalone/` **路径不同**，避免误删开发数据。

---

## 8. 打包与分发形态

| 产物 | 平台 | 用户操作 |
|------|------|----------|
| `DyAuthReply_x.x.x_aarch64.dmg` | Mac Apple Silicon | 拖入应用程序 |
| `DyAuthReply_x.x.x_x64.dmg` | Mac Intel | 同上 |
| `DyAuthReply_x.x.x_x64-setup.exe` | Windows | 下一步安装向导 |

安装包体积预估：**350～600 MB**（含 Python + Node + Redis + 应用）。

首次启动：10～30 秒（解压/迁移/起服务），显示「正在启动，请稍候…」。

---

## 9. 里程碑

### M0 — 规划冻结

- [x] 新建 `dyauthreply-client/` 目录
- [x] 本文档评审
- [x] 确认：**先功能、后权限**（令牌/RBAC 后置）

### M1 — 骨架

- [x] Tauri 空壳 + 启动页
- [x] `launcher.py` 拉起 SQLite + Worker（无需 Redis）
- [x] `client-ui` 空壳 + 健康检查页
- [x] Mac 本地 API 跑通（`tauri dev`，无需 Redis）

### M2 — 核心闭环（**当前重点，不含鉴权**）

- [x] 账号导入（DYCRED1）+ 账号列表 + 更新登录态
- [x] 会话列表 + 手动回复（`/app/chat`）
- [x] 自动回复规则 + 模板
- [x] 回复日志
- [x] 侧栏：自动回复开关、配额展示

### M3 — 可交付内测

- [ ] 首次向导 onboarding
- [ ] 托盘与优雅退出
- [ ] `tauri build` 出 unsigned .dmg / .exe
- [ ] Windows 实机测试

### M4 — 发布与加固（含可选鉴权）

- [ ] 代码签名（Apple Developer / Windows Authenticode）
- [ ] 自动更新
- [ ] （可选）本机 PIN / JWT
- [ ] （可选）崩溃上报 Sentry

---

## 10. 风险与对策

| 风险 | 对策 |
|------|------|
| 安装包过大 | Tauri 非 Electron；运行时按需解压 |
| 用户未装扩展导不出 Cookie | 安装包附 `.crx` 或分步图文向导；必要时内置简化抓取页 |
| 抖音 7911 仍可能出现 | 客户端文案说明「家用网络更稳」；高级设置保留代理 |
| 与主项目 API 漂移 | `client-api` OpenAPI 快照 + 契约测试 |
| 维护两套前端 | 客户端 UI 极简，变更面小；业务逻辑只在 `core.douyin` |

---

## 11. 对主仓库的改动边界（保证「不影响现有项目」）

**允许（极小、可审查）：**

- `backend-django/env/client_env.py` + `ZQ_ENV=client`（可选，与 standalone 类似）
- `backend-django/core/client/client_api.py` 新文件，注册到 `router.py` 的 **独立前缀**
- 根目录 `.gitignore` 增加 `dyauthreply-client/**/target`、`data/client/`

**禁止：**

- 修改 `web/apps/web-ele` 现有页面与路由
- 修改 `docker-compose*.yml` 默认行为
- 删除或重命名现有 douyin API 路径（保持后台兼容）

---

## 12. 下一步行动

1. ~~确认 MVP 与权限策略~~ → **先 M2 功能，权限后置**
2. **M2**：从「导入账号 DYCRED1」→ 会话 → 手动回复 开始编码
3. 平台：Mac 先行，Windows 与 M3 打包并行

---

*文档维护：`dyauthreply-client/PLAN.zh-CN.md`*
