# ✅ v1.5.0 - 统一域名规范：只在 creator.douyin.com 抓取

## 🎯 核心改进

**统一规范：只在 creator.douyin.com 抓取**

### 为什么？

1. **发送私信必需**：web_protect 和 keys 只在 creator 站点有
2. **避免 Cookie 混乱**：多域名会有多套 Cookie，容易搞混
3. **后台对接 creator**：DyAuthReply 主要对接 creator 的私信功能
4. **一致性**：统一规范，避免用户困惑

## 📋 标准操作流程

### 1. 访问指定页面
```
https://creator.douyin.com/creator-micro/chat
（创作者中心 → 私信页面）
```

### 2. 确认已登录正确的账号
- 查看页面右上角的昵称
- 确认是你要抓取的账号

### 3. 硬刷新页面
```
Ctrl+Shift+R (Windows)
Cmd+Shift+R (Mac)
```

### 4. 打开扩展抓取
```
点击扩展图标
点击"重新抓取"
核对昵称、抖音号、指纹
```

### 5. 复制导入串
```
点击"复制一键导入串"
粘贴到后台"新增账号"
```

## ⚠️ 域名检查

### 在 creator.douyin.com 抓取 ✅
- 正常抓取
- 无警告

### 在其他域名抓取 ⚠️
例如：www.douyin.com

**显示警告**：
```
⚠️ 建议在 creator.douyin.com 抓取
当前页面：www.douyin.com

强烈建议切换到 creator.douyin.com 再抓取，原因：
1. web_protect / keys 只在 creator 站点有（发送私信必需）
2. 避免多域名 Cookie 混乱
3. 后台对接的是 creator 站点

推荐页面：creator.douyin.com/creator-micro/chat

如果仅做「仅接收」导入（不发送私信），可继续抓取。
```

**仍然可以抓取**，但：
- 可能没有 web_protect / keys
- 导入后只能"仅接收"（不能发送私信）
- Cookie 可能混乱

## 🔧 修改内容

### 1. popup.js
添加域名检查逻辑：
```javascript
const url = new URL(tab.url);
if (url.hostname !== 'creator.douyin.com') {
  // 显示警告，但允许继续抓取
  $('tabHint').innerHTML = '⚠️ 建议在 creator.douyin.com 抓取...';
  $('tabHint').hidden = false;
}
```

### 2. popup.html
更新底部说明：
```
⚠️ 统一规范：只在 creator.douyin.com 抓取
推荐页面：creator.douyin.com/creator-micro/chat（私信页面）
```

### 3. manifest.json
版本号：`1.5.0`

## 📊 对比

| 场景 | 之前 | 现在 |
|------|------|------|
| creator.douyin.com | ✅ 正常 | ✅ 正常 |
| www.douyin.com | ✅ 正常 | ⚠️ 警告（但可继续） |
| 多域名混用 | ❌ 混乱 | ⚠️ 明确提示 |
| 用户困惑 | ❌ 不知道在哪抓 | ✅ 明确指引 |

## 🎯 解决的问题

### 问题1: Cookie 混乱
- **之前**：用户在 www 和 creator 来回切换，Cookie 混乱
- **现在**：统一在 creator 抓取，避免混乱

### 问题2: 凭证不完整
- **之前**：在 www 抓取，没有 web_protect / keys
- **现在**：统一在 creator 抓取，凭证完整

### 问题3: 用户困惑
- **之前**：不知道该在哪个域名抓取
- **现在**：明确规范 + 警告提示

## 🚀 部署步骤

### 方法1: 重新加载（如果之前已安装）
```
chrome://extensions/
找到扩展 → 点击"重新加载" 🔄
```

### 方法2: 完全重装（推荐）
```
chrome://extensions/
移除旧扩展
加载已解压的扩展程序
选择 browser-extension/douyin-cred-extractor/
```

### 验证
- 版本号：**v1.5.0**
- 在 www.douyin.com 打开扩展 → 应显示警告

## 🧪 测试场景

### 测试1: 在 creator 抓取（正常）
```
1. 访问 creator.douyin.com/creator-micro/chat
2. 打开扩展
3. 点击"重新抓取"
预期：
  - ✅ 无警告
  - ✅ 正常抓取
  - ✅ web_protect / keys 都有
```

### 测试2: 在 www 抓取（警告）
```
1. 访问 www.douyin.com
2. 打开扩展
3. 点击"重新抓取"
预期：
  - ⚠️ 显示黄色警告框
  - ⚠️ 提示建议在 creator 抓取
  - ✅ 仍然可以抓取（但可能没有 web_protect / keys）
```

### 测试3: 切换账号（creator）
```
1. 在 creator 切换账号
2. 硬刷新（Ctrl+Shift+R）
3. 打开扩展，点击"重新抓取"
预期：
  - ⚠️ 检测到账号切换
  - ❌ 头像隐藏
  - ⚠️ 警告提示
```

## 📝 用户指南

### 标准流程（推荐）
```
1. 访问 creator.douyin.com/creator-micro/chat
2. 确认已登录正确账号
3. Ctrl+Shift+R 硬刷新
4. 打开扩展
5. 点击"重新抓取"
6. 核对昵称、抖音号
7. 复制一键导入串
8. 粘贴到后台
```

### 切换账号流程
```
1. 在 creator 页面切换账号
2. Ctrl+Shift+R 硬刷新（重要！）
3. 打开扩展
4. 点击"重新抓取"
5. 看到警告（头像隐藏）
6. 核对昵称和抖音号
7. 确认无误后复制
```

### 常见问题

**Q: 为什么只能在 creator 抓取？**
A: web_protect 和 keys 只在 creator 有，发送私信必需。

**Q: 我在 www 抓取了怎么办？**
A: 会显示警告，但仍可抓取。导入后只能"仅接收"（不能发送私信）。

**Q: 如何切换到 creator？**
A: 访问 https://creator.douyin.com/creator-micro/chat

**Q: Cookie 会混乱吗？**
A: Cookie 在全站共享，但 web_protect / keys 按站点分开。统一在 creator 抓取可避免混乱。

## 🎉 v1.5.0 总结

### 新特性
- ✅ 域名检查：非 creator 域名显示警告
- ✅ 明确规范：底部说明"只在 creator 抓取"
- ✅ 推荐页面：直接链接到私信页面

### 改进
- ✅ 避免多域名 Cookie 混乱
- ✅ 确保凭证完整性
- ✅ 用户操作规范化

### 向后兼容
- ✅ 仍可在其他域名抓取（显示警告）
- ✅ 不强制阻止，给出建议

---

**当前状态**: ✅ v1.5.0 代码已完成  
**核心改进**: 统一域名规范 + 警告提示  
**下一步**: 重新加载扩展，只在 creator.douyin.com 抓取
