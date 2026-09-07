## D助手 0.1.26

### Rust 原生客户端

- 客户端本地服务已切换为 Rust `dy-agent`，正式安装包不再携带 Python、Django、PyInstaller 或外部 Node 运行环境。
- Tauri 仅负责桌面界面、进程监督、安全 IPC 与更新；账号会话、协议收发、规则执行、存储和日志均由 Rust Agent 管理。
- 引入有界多账号调度、账号租约 fence、单实例引擎锁、崩溃安全 outbox、滚动历史存储和发送回执核对。
- 升级安装会按安装路径停止旧 `launcher`，迁移原账号/规则/会话/记录，并阻止 Python 与 Rust 双发。

### 快捷登录与账号稳定性

- 新增内置托管 Chromium 快捷登录，无需用户安装浏览器扩展即可创建、更新或恢复账号凭证。
- 修复快捷登录确认后 Agent 重启导致旧会话被误报为“会话不可用”的竞态。
- 修复旧授权状态文件权限导致 Rust 服务无法启动的问题；普通旧文件会安全收紧为仅当前用户可读写。
- 补齐缺失 `msToken` / `s_v_web_id` 的稳定会话级兼容值，并允许证书刷新期间使用协议支持的 ECDSA 发送模式。
- 修复历史发送风控状态跨新凭证永久锁死，以及 `raw_check_code=2` 覆盖有效送达回执的误判。

### 原生功能与安全

- 账号、私信、规则、卡片、回复记录、授权、公告、更新检查和本地管理接口已迁移至认证的 Rust IPC。
- 自动回复保持显式开关、日限额、冷却、静默时间、去重与不确定结果禁止盲重试。
- 安装包校验确保只包含一个 `dy-agent`，拒绝 Python/Node/旧 launcher 混入。

## 下载

| 平台 | 文件 | 适用 |
| --- | --- | --- |
| macOS | `DAssistant-macos-aarch64.dmg` | Apple Silicon（M1/M2/M3/M4） |
| Windows | `DAssistant-windows-x64-setup.exe` | 64 位 Windows 10/11 |
| Chrome / Edge 扩展 | `douyin-cred-extractor.zip` | 备用手动凭证导入 |

升级安装会覆盖旧版并保留 `DyAuthReply` 用户数据目录。macOS 包未经过 Apple 公证时，首次打开请在“系统设置 → 隐私与安全性”中选择仍要打开。
