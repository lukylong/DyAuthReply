## 下载

| 平台 | 文件 |
|------|------|
| macOS | `.dmg` 安装包（Apple Silicon / Intel 通用） |
| Windows | `.msi` 或 `.exe` 安装包 |

---

## macOS 安装说明（未签名应用）

CI 构建的安装包**未经过 Apple 公证**，首次打开可能提示「无法验证开发者」或「已损坏」。按以下步骤安装：

### 1. 安装

1. 打开 `.dmg`，将 **DyAuthReply** 拖入「应用程序」文件夹。

### 2. 解除隔离并 ad-hoc 签名（终端执行）

打开「终端」，复制粘贴以下命令（需输入 macOS 登录密码）：

```bash
/usr/bin/xattr -cr "/Applications/DyAuthReply.app"
/usr/bin/codesign -fs - "/Applications/DyAuthReply.app"
```

说明：
- `xattr -cr`：清除下载隔离属性（quarantine），解决「已损坏，无法打开」
- `codesign -fs -`：本地 ad-hoc 重签名，解决「无法验证开发者」

### 3. 首次启动

- 从「启动台」或「应用程序」打开 **DyAuthReply**
- 若仍被拦截：「系统设置 → 隐私与安全性」→ 点击 **仍要打开**

### 4. 数据目录

用户数据保存在：

`~/Library/Application Support/DyAuthReply/`

---

## Windows 安装说明

1. 运行 `.msi` 或 `.exe` 安装程序，按向导完成安装
2. 若 SmartScreen 提示未知发布者，点击「更多信息」→「仍要运行」
3. 首次启动后，应用会在 `%APPDATA%\DyAuthReply\` 创建本地数据库
