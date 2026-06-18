## 下载

| 平台 | 文件 | 适用 |
| --- | --- | --- |
| macOS | `*_aarch64.dmg` | Apple Silicon (M1/M2/M3/M4) |
| Windows | `*_x64-setup.exe` | 64 位 Windows 10/11 |

> 仅提供 `.dmg` / `.exe` 安装包，不含 `.msi` 或构建中间文件。

---

## macOS 安装说明（未签名应用）

CI 构建的安装包**未经过 Apple 公证**，首次打开可能提示「无法验证开发者」或「已损坏」。按以下步骤安装：

### 1. 安装

1. 下载 `*_aarch64.dmg`（Apple Silicon）
2. 打开 `.dmg`，将 **DyAuthReply** 拖入「应用程序」文件夹

### 2. 解除隔离（终端执行）

```bash
/usr/bin/xattr -cr "/Applications/DyAuthReply.app"
```

### 3. 首次启动

- 从「启动台」或「应用程序」打开 **DyAuthReply**
- 若仍被拦截：「系统设置 → 隐私与安全性」→ 点击 **仍要打开**

### 4. 数据与日志

- 数据目录：`~/Library/Application Support/DyAuthReply/`
- 日志目录：`~/Library/Application Support/DyAuthReply/logs/`

---

## Windows 安装说明

1. 64 位系统运行 `*_x64-setup.exe`
2. 若 SmartScreen 提示未知发布者，点击「更多信息」→「仍要运行」
3. 首次启动会初始化数据库，Splash 页可能等待 30–60 秒
4. 若启动失败，查看 `%APPDATA%\DyAuthReply\logs\launcher.log`
5. 用户数据保存在 `%APPDATA%\DyAuthReply\`
