# 抖音登录态提取器（DyAuthReply 导入助手）

一个 Chrome/Edge MV3 扩展，一键抓取 DyAuthReply 后台「导入登录态」所需的三件套，
免去手动在控制台逐条 `console.log` 复制。

| 字段 | 来源 | 说明 |
| --- | --- | --- |
| `cookie` | `chrome.cookies` API | **必填**，监控/接收 + 发送都需要。用浏览器 API 读取，能拿到 `document.cookie` 取不到的 **HttpOnly `sessionid`** |
| `web_protect` | `localStorage['security-sdk/s_sdk_sign_data_key/web_protect']` | 选填，bd-ticket-guard 凭证，仅「发送私信」需要 |
| `keys` | `localStorage['security-sdk/s_sdk_crypt_sdk']` | 选填，含 `ec_privateKey`，仅「发送私信」需要 |

> 为什么用扩展而不是控制台脚本：`sessionid` 是 HttpOnly cookie，页面 JS（`document.cookie`）读不到，
> 只有扩展的 `chrome.cookies` 权限能拿到完整 Cookie。

## 安装（加载已解压的扩展）

1. 打开 `chrome://extensions`（Edge 为 `edge://extensions`）。
2. 右上角打开「开发者模式」。
3. 点「加载已解压的扩展程序」，选择本目录 `browser-extension/douyin-cred-extractor`。
4. 扩展栏会出现「抖音登录态提取器」图标，建议固定到工具栏。

## 可用站点

**⚠️ 统一规范：只在 creator.douyin.com 抓取**

**推荐页面**：https://creator.douyin.com/creator-micro/data/following/chat（私信管理）

- **Cookie** 在 `douyin.com` 全站共享，但统一在 creator 抓取可避免混乱。
- **web_protect / keys** 存在各站点自己的 `localStorage`（按 origin 隔离），`creator` 与 `www` 各一份。
  **要发送私信**（需要这两项）请在 **`creator.douyin.com` 的私信管理页面**抓取；仅做消息接收只要 Cookie，但仍建议统一在 creator 抓取。
- 当前页没取到 web_protect/keys 时，弹窗会给出黄色提示，引导你去 creator 私信页。
- 如果在其他域名（如 www.douyin.com）抓取，扩展会显示警告，建议切换到 creator。

## 使用（推荐：一键导入串）

1. 在浏览器登录 **抖音创作者中心**：`https://creator.douyin.com/creator-micro/data/following/chat`（私信管理页面）。
2. 在该页面点扩展图标，弹窗会自动抓取三件套并生成「一键导入串」（`DYCRED1.` 开头的单行）。
3. 点 **「复制一键导入串」**。
4. 到 DyAuthReply 后台 `/douyin/account` →「导入登录态」，把它粘贴到 **「一键导入串」** 输入框，点导入。
   - 一行搞定，无需逐项粘贴 cookie/web_protect/keys，避免参数贴错位。
5. 后台校验通过即把账号置为在线；含 `web_protect`+`keys` 时为「可发送」，否则「仅接收」。

> 一键导入串 = `DYCRED1.` + base64url(`{cookie, web_protect, keys, ua}`)，
> 不可读、单行、含版本号，后台 `parse_credential_bundle` 自动展开。同时把页面 UA 一并带上。

### 兜底：单独复制三项

弹窗里展开「查看 / 单独复制三项原始字段」，可分别复制 cookie / web_protect / keys，
粘贴到导入对话框的「手动填写（高级）」区域（与一键导入串二选一，单项填写会覆盖串里同名字段）。

## 校验徽标含义

- Cookie：`含 sessionid ✓`（合格）/ `缺 sessionid`（未登录或没读到，发送/接收都会失败）。
- web_protect：检查能否解析出 `ticket`。
- keys：检查能否解析出 `ec_privateKey`。

## 权限说明

- `cookies` + `host_permissions: *.douyin.com` —— 读取抖音 Cookie（含 HttpOnly）。
- `scripting` + `activeTab` + `tabs` —— 在当前抖音标签页读取两个 `localStorage` 键、识别当前标签页。

扩展不联网、不上传任何数据，所有内容仅在本地弹窗内展示，复制动作由你手动触发。

## 切换账号 / 多账号抓取

⚠️ **切换账号后必须刷新页面，否则扩展会自动拦截并提示！**

抓不到新账号、或点「重新抓取」像没反应，多半是下面几种情况，弹窗已给出可核对的信息：

1. **切号后先刷新页面（最重要）**：
   - 在浏览器切换抖音账号后，**按 F5 或 Cmd+R 刷新页面**
   - 让页面 DOM、localStorage、`__INITIAL_STATE__` 按新账号重新加载
   - 再打开扩展或点「重新抓取」
   - **扩展会自动检测账号切换**：如果 Cookie 变了但页面没刷新，会显示黄色警告并阻止抓取，避免「新 Cookie + 旧昵称/头像」的混乱数据

2. **认准账号卡片**：抓取后弹窗会显示当前登录账号的 **头像 + 昵称 + 抖音号 + sessionid 指纹**
   （账号信息从页面缓存 best-effort 提取，可能只取到部分；指纹一定有）。切换账号后这些会变化——
   若点「重新抓取」后昵称/指纹没变，说明读到的还是旧账号。

3. **认准当前标签页**：扩展读的是「当前激活的标签页」。多个抖音标签时，先切到目标账号那个标签，
   再打开扩展抓取（弹窗里「当前页面」会显示读取的是哪个域名）。

4. **改完扩展要重载**：如果你更新了扩展文件，去 `chrome://extensions` 点该扩展的「重新加载」，
   并关掉再重新打开弹窗。

### 普通窗口 + 无痕窗口各登一个号（推荐做法）

1. 在 `chrome://extensions` 打开本扩展的 **「在无痕模式下启用」**（v1.6 起使用 split 模式，普通/无痕 cookie 完全隔离）。
2. **普通窗口**登录账号 A → 切到 creator 私信页 → 在该窗口点扩展抓取 → 弹窗应显示「浏览器环境：普通窗口」和 A 的 sessionid 指纹。
3. **无痕窗口**登录账号 B → 同样操作 → 应显示「无痕窗口」和 **不同的** sessionid 指纹。
4. 两个指纹前缀必须不同；若相同，说明抓错窗口或未在无痕启用扩展——**不要导入**，先核对。

> v1.5 及更早版本会把 `douyin.com` 全域 cookie 合并进来，同机双开时容易把普通窗口的 sessionid 混进无痕抓取结果，后台会报「与已有账号 session 相同」。请升级到 v1.6+ 并重载扩展。

## 凭证过期怎么办

抖音 cookie / bd-ticket 会过期掉线。重新在创作者中心登录后，再用本扩展抓取一次，
到后台点「重新导入」覆盖即可。
