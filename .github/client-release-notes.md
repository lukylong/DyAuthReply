## 本次更新

- **私信工作台会话列表**支持分页 / 搜索 / 滚动加载更多，解决会话数量多时**列表显示不全**
- 修复 **Windows 全新安装启动失败**：数据库迁移因控制台编码（GBK）无法输出特殊字符而崩溃，现统一 UTF-8 输出
- 新增**单实例守卫**：重复打开只唤回已运行的窗口，避免第二个实例占用端口导致「初始化启动失败」
- 新增**客户端在线检查更新**通道：左下角版本号可手动检查，发现新版本会弹窗引导下载覆盖安装
- 优化**多账号托管**性能与稳定性

---

## 下载

| 平台 | 文件 | 适用 |
| --- | --- | --- |
| macOS | `*_aarch64.dmg` | Apple Silicon (M1/M2/M3/M4) |
| Windows | `*_x64-setup.exe` | 64 位 Windows 10/11 |

> 仅提供 `.dmg` / `.exe` 安装包，不含 `.msi` 或构建中间文件。
> 升级到新版本时直接安装即可覆盖旧版，本地数据不会丢失。

---

## macOS 安装说明（未签名应用）

CI 构建的安装包**未经过 Apple 公证**，首次打开可能提示「无法验证开发者」或「已损坏」。按以下步骤安装：

### 1. 安装

1. 下载 `*_aarch64.dmg`（Apple Silicon）
2. 打开 `.dmg`，将 **D助手** 拖入「应用程序」文件夹

### 2. 解除隔离（终端执行）

```bash
/usr/bin/xattr -cr "/Applications/D助手.app"
```

### 3. 首次启动

- 从「启动台」或「应用程序」打开 **D助手**
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
