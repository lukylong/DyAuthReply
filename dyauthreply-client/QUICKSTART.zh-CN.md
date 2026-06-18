# DyAuthReply Client — 快速开始（M1 骨架）

## 前置（开发机）

- Python 3.11+（已安装项目 `backend-django` 依赖）
- Node.js 20+（抖音 JS 签名；正式安装包会内置）
- Rust（仅打包 Tauri 时需要）：`rustup`

**不需要** 安装 Redis、PostgreSQL 或 Docker。客户端使用内置 **SQLite** 数据库和 **SQLite 命令队列**（API ↔ Worker 通信）。

## 1. 安装 Python 依赖

```bash
cd backend-django
pip install -r requirements.txt
cd ../dyauthreply-client/client-ui && npm install
cd ../desktop && npm install
```

## 2. 启动（开发）

**终端 A — 本地服务（API + Worker）：**

```bash
cd /path/to/DyAuthReply
python3 dyauthreply-client/launcher/launcher.py
```

浏览器访问：[http://127.0.0.1:8765/app/](http://127.0.0.1:8765/app/)

**终端 B — 桌面壳（Tauri，可选）：**

```bash
cd dyauthreply-client/desktop
npm run tauri dev
```

Tauri 会自动启动 `client-ui` 开发服务器，并尝试拉起 `launcher.py`。

## 3. 打包（后续 M3）

```bash
cd dyauthreply-client/client-ui && npm run build
cd ../desktop && npm run tauri build
```

产物：`desktop/src-tauri/target/release/bundle/`

正式版会在安装包内嵌 **Python + Node** 运行时，用户只需双击 DyAuthReply，无需单独装任何数据库或 Redis。

## macOS 正式安装（Release 包）

根据 Mac 芯片选择对应 `.dmg`：

- **Apple Silicon (M 系列)**：`DyAuthReply_*_aarch64.dmg`

CI 构建包未 Apple 公证，首次打开可能提示无法验证开发者。安装后拖入「应用程序」，在终端执行：

```bash
/usr/bin/xattr -cr "/Applications/DyAuthReply.app"
```

- `xattr -cr`：清除下载隔离，解决「已损坏，无法打开」
- `codesign -fs -`：本地 ad-hoc 重签名，解决「无法验证开发者」

若仍被拦截：「系统设置 → 隐私与安全性 → 仍要打开」。

## 数据目录

- 开发默认：`DyAuthReply/data/client/`
- macOS 正式：`~/Library/Application Support/DyAuthReply/`
- Windows 正式：`%APPDATA%\DyAuthReply\`

## Windows 正式安装

1. 从 Release 下载 `DyAuthReply_*_x64-setup.exe`（Windows 64 位）。
2. 安装后双击桌面/开始菜单图标启动。
3. 首次启动会初始化 SQLite 数据库，Splash 页可能等待 **30–60 秒**，属正常现象。

### 服务启动失败排查

若 Splash 显示「服务启动失败」：

1. **不要同时打开多个 DyAuthReply 窗口**（会触发单实例锁）。
2. 查看日志（按优先级）：
   - `%APPDATA%\DyAuthReply\logs\launcher.log` — 后端 sidecar 启动日志
   - `%APPDATA%\DyAuthReply\logs\server.log` — Django API 日志
   - `%APPDATA%\DyAuthReply\migration_error.log` — 数据库迁移失败详情
3. 确认 **8765 端口** 未被其他程序占用（如开发态残留的 `launcher.py`）。
4. 若曾异常退出，可删除 `%APPDATA%\DyAuthReply\launcher.lock` 后重试（勿在程序运行时删除）。
5. Windows Defender / 杀毒软件可能拦截 PyInstaller 解包，可将安装目录加入白名单后重装。

### 手动测试 sidecar（高级）

安装目录通常在 `C:\Users\<用户名>\AppData\Local\Programs\DyAuthReply\`，资源目录内含 `launcher-x86_64-pc-windows-msvc.exe`。可在 cmd 中直接运行该 exe，观察控制台输出与上述日志文件。

## API

- 健康检查：`GET /api/client/v1/health`
- 抖音账号：`GET /api/client/v1/douyin/account/all`

仅允许 `127.0.0.1` 访问，无需登录 Token。