# 抖音登录态提取器（DyAuthReply 导入助手）

Chrome/Edge MV3 扩展，用于生成 DyAuthReply 后台可直接粘贴的 `DYCRED1.` 登录态导入串。

## 当前凭证来源（v2.2.0）

| 字段 | 来源 | 用途 |
| --- | --- | --- |
| `cookie` | `chrome.cookies` API | 兼容字段；包含 creator 页面可见的 HttpOnly `sessionid` |
| `cookie_headers` | 按 creator/imapi/www URL 分别调用 `chrome.cookies` | 保留同名 Cookie 的域作用域，运行时按目标主机选择 |
| `ticket_guard_server_data` | 登录响应写入的 `bd_ticket_guard_server_data` Cookie | 提供 `ticket` / `ts_sign` / `client_cert` |
| `keys` | `localStorage['security-sdk/s_sdk_crypt_sdk']` | 提供发送签名所需 `ec_privateKey` |
| `dtrait_blob` / `session_dtrait` | document-start 页面钩子 + 请求头监听 | 身份安全 token 的动态设备特征；优先用 blob 按路径重算 |
| `web_protect` | 旧版 localStorage 字段 | 仅兼容已有浏览器登录态 |

`im/user_token/v2` 现在只返回 userid，扩展不再依赖该端点。发送凭证来自登录响应的
`bd-ticket-guard-server-data` 响应头/同名 Cookie；后端同时兼容旧 `web_protect` 数据。

## 安装

1. 打开 `chrome://extensions`（Edge：`edge://extensions`）。
2. 开启「开发者模式」。
3. 点「加载已解压的扩展程序」，选择本目录。
4. 每次更新扩展文件后，在扩展页点「重新加载」。

## 使用

1. 先在扩展页重载 v2.2.0，再重新打开 creator 页面，让 document-start 钩子捕获设备特征。
2. 打开 [creator 私信管理页](https://creator.douyin.com/creator-micro/data/following/chat)。
3. 打开扩展，确认 Cookie、server_data、keys 三个徽标均正常。
4. 复制 `DYCRED1.` 一键导入串，在 DyAuthReply `/douyin/account` 的「导入登录态」中粘贴。

一键导入串包含 `{cookie, cookie_headers, ticket_guard_server_data, dtrait_blob, keys, ua, ...}`。后台不会把
server_data 当作续期响应，而是在导入时一次性解析并加密保存。

如果只拿到 Cookie，账号仍可作为「仅接收」导入；要发送私信，必须同时具有
`ticket`、`ts_sign`、`client_cert` 和 `private_key`。

## 多账号

同一 Chrome 配置的普通窗口共享 Cookie。账号 A/B 应使用「普通窗口 + 无痕窗口」或两个
独立 Chrome 配置；无痕窗口需在扩展详情中开启「在无痕模式下启用」。导入前核对弹窗中的
账号昵称和 sessionid 指纹，两个账号的指纹应不同。

## 权限与数据流

- `cookies` + `host_permissions`：读取当前 Cookie store 的 douyin.com Cookie。为了覆盖登录
  子域写入的 server_data，只跨子域补取精确字段 `bd_ticket_guard_server_data`，不会合并其他 Cookie。
- `webRequest`：只读取 douyin.com 登录响应的 `bd-ticket-guard-server-data` 头，
  按普通/无痕 Cookie store 隔离存在 `storage.session`，浏览器会话结束即清空。
- `scripting` + `activeTab` + `tabs`：读取当前 creator 页面 localStorage 与账号信息。
- `storage`：按普通/无痕 Cookie store 保存非敏感账号指纹，用于切号提醒。

扩展不把凭证上传到第三方；内容仅在本地弹窗生成，复制由用户触发。
