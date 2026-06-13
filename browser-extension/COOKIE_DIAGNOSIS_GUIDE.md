# 🔍 Cookie 获取问题诊断指南

## 🎯 问题描述

**插件获取的 Cookie 不是当前账号的，导入后把另一个账号顶掉了**

## 🔎 诊断步骤

### 步骤1: 检查浏览器中的实际 Cookie

1. **打开你要抓取的抖音页面**（确认已登录正确的账号）
   - 例如：https://creator.douyin.com/

2. **按 F12 打开开发者工具**

3. **Application → Cookies → https://creator.douyin.com**

4. **找到 `sessionid` 这个 Cookie**
   - 记下最后 8 位：例如 `40e54e5c`

5. **同时检查其他域名**
   - Application → Cookies → https://www.douyin.com
   - 看是否也有 `sessionid`
   - 如果有，记下它的最后 8 位

### 步骤2: 检查扩展抓取的 Cookie

1. **打开扩展**

2. **点击"重新抓取"**

3. **查看显示的指纹**
   - 例如：`指纹 …40e54e5c`

4. **对比**：
   - 扩展的指纹 vs 浏览器实际的 sessionid
   - **是否一致？**

### 步骤3: 检查扩展的 Console

1. **右键扩展图标 → "检查"**

2. **Console 标签**

3. **点击"重新抓取"**

4. **查看输出**：
   ```
   [扩展v1.3] 已加载历史指纹: {lastFingerprint: "…abc12345"}
   [扩展v1.3] 账号切换检测: {
     lastFingerprint: "…abc12345",
     currentFingerprint: "…40e54e5c",
     isSwitched: true/false
   }
   ```

5. **确认**：
   - `currentFingerprint` 是否与浏览器实际的 sessionid 一致？

## 🐛 可能的问题

### 问题1: 浏览器有多个账号的 Cookie

**现象**：
- creator.douyin.com 有一个 sessionid
- www.douyin.com 有另一个 sessionid
- 扩展拿到的是错误的那个

**解决方案A: 清除所有抖音 Cookie**
1. F12 → Application → Cookies
2. 右键 douyin.com 的所有域 → Clear
3. 重新登录你要抓取的账号
4. 再抓取

**解决方案B: 确保在正确的页面抓取**
- 如果要抓 creator 的账号，必须在 **creator.douyin.com** 页面打开扩展
- 不要在 www.douyin.com 页面抓取

### 问题2: Cookie 没有刷新

**现象**：
- 浏览器显示的 sessionid 是新的
- 但扩展拿到的是旧的

**解决方案**：
1. **硬刷新页面**（Ctrl+Shift+R / Cmd+Shift+R）
2. **关闭标签页重新打开**
3. **重启浏览器**

### 问题3: 扩展缓存了旧 Cookie

**现象**：
- 浏览器和扩展的指纹都是新的
- 但导入后还是把另一个账号顶掉了

**可能原因**：
- 扩展的 `chrome.storage.local` 缓存了旧指纹
- 导致误判为"同一账号"

**解决方案**：
1. 右键扩展图标 → "检查"
2. Console 执行：
   ```javascript
   chrome.storage.local.clear()
   ```
3. 刷新页面
4. 重新抓取

## 🔧 临时解决方案：手动复制 Cookie

如果扩展一直有问题，可以手动复制：

1. **F12 → Application → Cookies → creator.douyin.com**

2. **手动复制所有 Cookie**：
   - 点击 `sessionid` → 复制值
   - 点击 `sessionid_ss` → 复制值
   - ...（所有 Cookie）

3. **拼接成 Cookie 字符串**：
   ```
   sessionid=xxx; sessionid_ss=yyy; uid_tt=zzz; ...
   ```

4. **在后台"手动填写"区域粘贴**

## 🧪 验证方法

### 方法1: 对比指纹
```
浏览器 sessionid 最后8位：40e54e5c
扩展显示的指纹：        …40e54e5c
✅ 一致 → Cookie 正确
❌ 不一致 → Cookie 错误
```

### 方法2: 对比昵称
```
浏览器页面显示的昵称：哈啰
扩展显示的昵称：      哈啰
✅ 一致 → 账号信息正确
❌ 不一致 → 页面没刷新或获取错误
```

### 方法3: 导入测试
```
1. 导入 Cookie
2. 后台显示昵称：哈啰
3. 对比浏览器昵称：哈啰
✅ 一致 → 成功
❌ 不一致 → Cookie 错误
```

## 📋 检查清单

请按顺序检查：

- [ ] 1. 浏览器实际的 sessionid（F12 → Cookies）
- [ ] 2. 扩展抓取的指纹（扩展界面）
- [ ] 3. 两者是否一致？
- [ ] 4. Console 中的 `currentFingerprint` 是否正确？
- [ ] 5. 是否在正确的页面抓取？（creator vs www）
- [ ] 6. 是否清除了其他域名的 Cookie？
- [ ] 7. 是否清除了扩展的 storage 缓存？

## 🆘 如果还是不行

请提供以下信息：

1. **浏览器 sessionid 最后8位**（F12 → Cookies）
2. **扩展显示的指纹**（扩展界面）
3. **Console 的完整输出**（右键扩展 → 检查 → Console）
4. **导入后后台显示的昵称**
5. **浏览器页面显示的昵称**

---

**关键点**：扩展获取的指纹 **必须** 与浏览器实际的 sessionid 一致！
