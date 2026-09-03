## 本次更新

- **修复新账号首条实时私信不回复**：WebSocket 已收到消息但 HTTP 兜底扫描尚未填充 `conversation_short_id` 时，自动回复会在真正发送前失败；现改为在消息交给 worker 前同步会话发送上下文。
- **区分本地竞态与平台拒绝**：缺少 `conversation_short_id` 属于客户端链路问题并已修复；抖音返回 `biz_status_code=8610/raw_check_code=2` 时仍按平台发送校验拒绝处理，不会误报为发送成功。
- **同步抖音创作者中心 PC IM 当前协议**：发送改为无 query 的 `v1/message/send`，先解析会话 `short_id` / `ticket`，并使用当前 `douyin_creator` 请求包与身份头。
- **修复凭证采集缺口**：同时保存 `creator.douyin.com`、`imapi.douyin.com`、`www.douyin.com` 三个域的 Cookie 快照，补齐 UIFEID、浏览器指纹、`bd-ticket`、`dtrait` 和 Web 签名链路。
- **适配 `im/user_token/v2` 新返回**：接口仅返回 `user_id` 时仍可建立收消息链路，不再把缺少旧 `token`、`sdk_cert`、`ts_sign` 当成登录失效。
- **修复风控误报**：`biz_status_code=8610/raw_check_code=2` 现明确标记为抖音平台发送风控，不再显示为 Cookie 失效或 `msg=OK`。
- **凭证导入保持原子绑定**：同一会话增量更新时保留有效私钥与 ticket，跨账号时清理旧签名材料。
- 随版本提供 **抖音登录态提取器 2.2.0**，可一键导出与本版协议匹配的主机级 Cookie 和签名材料。

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
