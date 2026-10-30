# 快捷登录浏览器：安装前按系统选版本

## 选择规则

版本清单：`dyauthreply-client/agent/quick-auth-component.json`。

| 系统 | 选择时机 | 浏览器版本 |
| --- | --- | --- |
| Windows x64，build 10240–21999 | 安装/修复发起时 | `windows_compat`：152.0.7977.169 |
| Windows x64，build ≥ 22000 | 安装/修复发起时 | 标准版：155.0.8059.79 |
| macOS arm64/x64 | 安装/修复发起时 | 原标准版及原归档摘要不变 |
| Windows build 缺失、异常或 < 10240 | 下载前返回明确原因 | 不猜测版本、不下载不适配包 |

Windows build 使用 sysinfo 读取注册表 CurrentBuildNumber。BF-202402272109 的 19045.3803 对应 build 19045，选择兼容版。Server 2016/2019/2022 使用对应低 build 的兼容包；Server 2025 使用标准包。这里不增加 Win7/8、Windows x86 或原生 Windows ARM 安装包支持。

## 不变的行为

- 本功能只在安装和修复时选包，不引入启动失败后自动切换或循环重试。
- 已有 155 组件不会在后台被替换；设置页显示系统推荐版本，用户点击“修复重装”后按系统规则安装兼容版本。
- 安装与修复共用 `sources::pinned`；同版本可复用，不同版本不复用；强制修复仍重新下载。
- 按版本建立新目录，再原子切换清单；原组件保留为 previous，可回退。
- 不更换系统浏览器，不修改账号、凭证、规则或已有登录资料；不关闭沙箱。
- 原有 current/previous 清理策略继续生效，不把两套浏览器塞进客户端安装包。

## 清单及分发

- 标准 pin 和 `windows_compat` 的版本、来源、长度及 SHA256 独立维护。
- 兼容包来自 Google 官方 CfT 地址；兼容版本为 152.0.7977.169，而非较早的 152.0.7977.82。
- 兼容包原始字节发布在公共分发仓 `browser-components` 资源页，不将该页设为 latest，不修改 `client-latest` 更新清单。
- 兼容来源依次使用 gh-proxy.com、ghfast.top、Google 官方源；每个来源必须通过同一固定长度和 SHA256 校验。
- `pin_quick_auth_component.mjs --available` / `--check` 覆盖两组 pin；`--profile windows_compat` 可单独验证兼容包；更新标准 pin 保留兼容 pin。

## 可见反馈与上报

- 组件状态新增可选 `recommended_version` 和 `selection_reason`；旧客户端可忽略。
- 设置页区分当前安装版本和系统推荐版本；不自动打开登录窗口。
- 选包事件使用现有上报字段 `version`、`classification`、`reason`，不携带账号信息。
- 文件/归档校验与故障机成功启动仍是不同验收项，界面不把前者宣传为后者。

## 验证要求

- Rust 单测覆盖 build 10240、17763、19045、20348、21999、22000、22631、26100，未知/异常/旧系统拒绝，以及 macOS pin 不受影响。
- Windows Server 2022 实机 CI 应选择 152；较新的 Windows runner 应选择 155，下载后检查真实 Browser.getVersion。
- Windows CI 运行真实 Rust 选择器，核对 sysinfo 注册表 build；覆盖三个隔离空白配置及 PowerShell 诊断器。
- 兼容版本完整登录、账号识别及凭证校验仍需受影响 Win10 实机验收。CI 空白页成功不等同于已解决 BF 的退出码 3。

## 发布边界

本次实现不自动发布新客户端。旧版 0.1.55 的内置固定清单不变；用户必须收到包含此改动的客户端版本后，安装/修复才会按系统自动选包。
