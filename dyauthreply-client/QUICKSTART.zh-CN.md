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

浏览器访问：http://127.0.0.1:8765/app/

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

CI 构建包未 Apple 公证，首次打开可能提示无法验证开发者。安装 `.dmg` 并拖入「应用程序」后，在终端执行：

```bash
/usr/bin/xattr -cr "/Applications/DyAuthReply.app"
/usr/bin/codesign -fs - "/Applications/DyAuthReply.app"
```

- `xattr -cr`：清除下载隔离，解决「已损坏，无法打开」
- `codesign -fs -`：本地 ad-hoc 重签名，解决「无法验证开发者」

若仍被拦截：「系统设置 → 隐私与安全性 → 仍要打开」。

## 数据目录

- 开发默认：`DyAuthReply/data/client/`
- macOS 正式：`~/Library/Application Support/DyAuthReply/`

## API

- 健康检查：`GET /api/client/v1/health`
- 抖音账号：`GET /api/client/v1/douyin/account/all`

仅允许 `127.0.0.1` 访问，无需登录 Token。
