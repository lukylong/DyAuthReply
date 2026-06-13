# 🎯 快速参考 - 浏览器扩展使用指南

## 📋 标准操作流程（必看！）

### 1. 访问指定页面
```
https://creator.douyin.com/creator-micro/data/following/chat
（创作者中心 → 私信管理）
```

### 2. 确认账号
- 查看页面右上角昵称
- 确认是你要抓取的账号

### 3. 硬刷新页面
```
Ctrl+Shift+R (Windows)
Cmd+Shift+R (Mac)
```

### 4. 打开扩展
- 点击浏览器工具栏的扩展图标

### 5. 点击"重新抓取"
- 不会自动抓取，必须手动点击

### 6. 核对信息
- 昵称
- 抖音号
- Cookie 指纹

### 7. 复制导入串
- 点击"复制一键导入串"

### 8. 粘贴到后台
- DyAuthReply 后台 → 抖音账号管理 → 新增账号
- 粘贴一键导入串

## ⚠️ 切换账号流程

### 1. 在抖音页面切换账号
- 点击右上角头像
- 切换到新账号

### 2. 硬刷新页面（重要！）
```
Ctrl+Shift+R (Windows)
Cmd+Shift+R (Mac)
```
**必须硬刷新！普通 F5 不够！**

### 3. 打开扩展，点击"重新抓取"

### 4. 看到警告（正常）
- 头像被隐藏（避免缓存误导）
- 昵称后有 ⚠️ 图标
- 黄色警告框

### 5. 核对信息
- 昵称是否是新账号？
- 抖音号是否是新账号？
- 指纹是否变化？

### 6. 确认无误后复制

## 🚫 常见错误

### ❌ 错误1: 在错误的域名抓取
```
❌ www.douyin.com
✅ creator.douyin.com/creator-micro/data/following/chat
```

### ❌ 错误2: 不刷新页面
```
切换账号 → ❌ 直接抓取
切换账号 → ✅ Ctrl+Shift+R → 抓取
```

### ❌ 错误3: 普通刷新（F5）
```
❌ F5
✅ Ctrl+Shift+R（硬刷新）
```

### ❌ 错误4: 忽略警告
```
看到警告 → ❌ 直接复制
看到警告 → ✅ 核对昵称和抖音号 → 确认无误 → 复制
```

## 🔍 检查清单

抓取前：
- [ ] 在 creator.douyin.com 页面？
- [ ] 已登录正确的账号？
- [ ] 页面已硬刷新（Ctrl+Shift+R）？

抓取后：
- [ ] 昵称正确？
- [ ] 抖音号正确？
- [ ] 指纹是否合理？
- [ ] 如有警告，已核对？

## 💡 提示

### 推荐页面
```
https://creator.douyin.com/creator-micro/data/following/chat
```
**点击链接可直达**

### 快捷键
```
硬刷新：Ctrl+Shift+R (Windows) / Cmd+Shift+R (Mac)
```

### 版本号
当前版本：**v1.5.0**

### 支持的凭证
- ✅ Cookie（必需）
- ✅ web_protect（发送私信必需）
- ✅ keys（发送私信必需）

## 🆘 故障排除

### 问题：Cookie 不对
**症状**：导入后显示的昵称不对

**解决**：
1. 清除所有抖音 Cookie（F12 → Application → Cookies → 右键 Clear）
2. 重新登录
3. 硬刷新
4. 重新抓取

### 问题：头像显示旧账号
**症状**：切换账号后头像还是旧的

**解决**：
- 这是正常的！扩展会隐藏头像（避免浏览器缓存误导）
- 只需核对昵称和抖音号

### 问题：没有 web_protect / keys
**症状**：扩展提示"未取到 web_protect / keys"

**解决**：
1. 确认在 **creator.douyin.com** 页面
2. 访问私信管理页面：https://creator.douyin.com/creator-micro/data/following/chat
3. 硬刷新
4. 重新抓取

### 问题：提示"不是 creator.douyin.com"
**症状**：扩展显示黄色警告

**解决**：
1. 点击警告中的链接
2. 或手动访问：https://creator.douyin.com/creator-micro/data/following/chat
3. 重新抓取

## 📞 技术支持

如果遇到问题，请提供：
1. 浏览器 sessionid 最后8位（F12 → Application → Cookies）
2. 扩展显示的指纹
3. Console 日志（右键扩展图标 → "检查" → Console）
4. 当前页面 URL

---

**记住**：只在 **creator.douyin.com/creator-micro/data/following/chat** 抓取！
