## 本次更新

- **修复卡片回复无法发送**：协议更新后发送日志仍引用已移除的旧模板变量，导致请求在发往抖音前直接异常；现已清理旧字段并补充发送链路回归测试
- **影响范围**：同时修复卡片落地页、普通自动回复和手动文本发送共用的 HTTP 私信发送通道
- **同步抖音 PC 私信新协议**：`im/user_token/v2` 仅返回 `user_id` 时也可继续建立私信会话，不再依赖旧响应中的 `token`、`sdk_cert`、`ts_sign`
- **迁移发送凭证来源**：从登录回包 Cookie `bd-ticket-guard-server-data` 解析 `token`、`sdk_cert`、`ts_sign`，并兼容 `server_data` / `bd_ticket_guard_server_data` 别名
- **凭证导入改为原子绑定**：扩展导入时一次性提交账号身份、Cookie 与签名材料，切换账号时清理旧签名，避免串号
- 随版本提供 **抖音登录态提取器 2.1.0**，新增登录回包监听并自动保存新协议所需凭证
- 修复卡片落地页被客户端整页跳转的问题，并保留抖音爬虫可读取的自定义标题与描述

---

## 下载

| 平台 | 文件 | 适用 |
| --- | --- | --- |
| macOS | `*_aarch64.dmg` | Apple Silicon (M1/M2/M3/M4) |
| Windows | `*_x64-setup.exe` | 64 位 Windows 10/11 |
| Chrome / Edge 扩展 | `douyin-cred-extractor.zip` | 解压后通过“加载已解压的扩展程序”安装 |

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
