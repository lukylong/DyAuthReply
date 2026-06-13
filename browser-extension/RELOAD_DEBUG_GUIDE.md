# 🔧 扩展重新加载和调试指南

## 📦 已更新内容

### 版本号
- ✅ manifest.json: `1.0.0` → `1.3.0`
- ✅ popup.html: 标题显示 `v1.3.0`

### 核心功能
- ✅ 账号切换检测（对比 sessionid 指纹）
- ✅ 切换时隐藏头像（避免缓存误导）
- ✅ 添加调试日志（console.log）

## 🚀 重新加载扩展的正确步骤

### 方法1: 标准重新加载（推荐）

1. **打开扩展管理页面**
   ```
   chrome://extensions/
   ```

2. **找到扩展**
   - 名称：抖音登录态提取器 (DyAuthReply)
   - 应该显示版本：1.3.0（如果还是 1.0.0 说明没更新成功）

3. **点击"重新加载"按钮**
   - 扩展卡片右下角的刷新图标 🔄
   - 或者点击"详情" → "重新加载"

4. **关闭所有扩展 popup 窗口**
   - 如果扩展 popup 还开着，关掉它
   - 重新点击扩展图标打开

5. **清除浏览器缓存（可选但推荐）**
   - Ctrl+Shift+Delete (Windows) / Cmd+Shift+Delete (Mac)
   - 选择"缓存的图片和文件"
   - 点击"清除数据"

### 方法2: 完全卸载重装（最彻底）

1. **卸载旧扩展**
   - chrome://extensions/
   - 点击"移除"

2. **重新加载扩展**
   - 点击"加载已解压的扩展程序"
   - 选择 `browser-extension/douyin-cred-extractor/` 目录

3. **验证版本**
   - 应该显示 `1.3.0`

## 🔍 验证是否加载成功

### 1. 检查版本号
打开扩展 popup，标题应显示：
```
抖音登录态提取器 v1.3.0
```

### 2. 检查控制台日志
1. 右键点击扩展图标 → "检查"（或"审查元素"）
2. 打开 Console 标签
3. 点击"重新抓取"按钮
4. 应该看到日志：
   ```
   [扩展v1.3] 账号切换检测: {lastFingerprint: ..., currentFingerprint: ..., isSwitched: true/false}
   [扩展v1.3] 检测到账号切换，已隐藏头像  ← 如果是切换场景
   或
   [扩展v1.3] 显示头像，isSwitched=false  ← 如果不是切换场景
   ```

### 3. 测试切换场景
1. 登录账号A，抓取（记录指纹）
2. 切换到账号B，刷新抖音页面
3. 打开扩展
4. **应该看到**：
   - ❌ **头像消失了**（不再显示）
   - ⚠️ 昵称后有 ⚠️ 图标
   - ⚠️ 黄色警告框

## 🐛 如果还是不生效

### 问题1: 版本号还是 1.0.0
**原因**: manifest.json 没有被重新加载

**解决**:
1. 完全卸载扩展
2. 重新加载扩展目录
3. 验证版本号

### 问题2: 头像还是显示
**可能原因**:
1. popup.js 被浏览器缓存了
2. `isSwitched` 判断为 false（不是切换场景）

**调试步骤**:
1. 右键扩展图标 → "检查"
2. 查看 Console 日志
3. 看 `isSwitched` 的值是什么
4. 如果 `isSwitched=false`，说明：
   - `lastFingerprint` 为空（第一次抓取）
   - 或指纹相同（没有切换账号）

### 问题3: 没有调试日志
**原因**: popup.js 没有被更新

**解决**:
1. 确认文件修改时间：
   ```bash
   ls -la browser-extension/douyin-cred-extractor/popup.js
   ```
   应该是最新的时间

2. 完全卸载重装扩展

3. 清除浏览器缓存

## 📊 调试信息解读

### Console 输出示例

#### 首次抓取
```
[扩展v1.3] 账号切换检测: {
  lastFingerprint: null,          ← 第一次抓取，没有历史指纹
  currentFingerprint: "…abc12345",
  isSwitched: false,               ← 不是切换（没有历史指纹）
  nickname: "哈啰"
}
[扩展v1.3] 显示头像，isSwitched=false
```

#### 切换账号后
```
[扩展v1.3] 账号切换检测: {
  lastFingerprint: "…abc12345",    ← 上次的指纹
  currentFingerprint: "…40e54e5c",  ← 新的指纹
  isSwitched: true,                ← 检测到切换！
  nickname: "哈啰"
}
[扩展v1.3] 检测到账号切换，已隐藏头像
```

#### 同一账号重复抓取
```
[扩展v1.3] 账号切换检测: {
  lastFingerprint: "…abc12345",
  currentFingerprint: "…abc12345",  ← 指纹相同
  isSwitched: false,               ← 没有切换
  nickname: "哈啰"
}
[扩展v1.3] 显示头像，isSwitched=false
```

## 🎯 预期行为

### 场景1: 首次使用（账号A）
- ✅ 显示头像
- ✅ 显示昵称
- ✅ 无警告
- `isSwitched = false`

### 场景2: 切换到账号B
- ❌ **隐藏头像**
- ⚠️ 昵称后有 ⚠️
- ⚠️ 黄色警告框
- `isSwitched = true`

### 场景3: 再次抓取账号B（不切换）
- ✅ 显示头像（指纹相同，认为是同一账号）
- ✅ 显示昵称
- ✅ 无警告
- `isSwitched = false`

## 💡 最佳实践

### 测试流程
1. **完全卸载旧扩展**
2. **重新加载扩展目录**
3. **验证版本号 = 1.3.0**
4. **打开 Console（右键扩展图标 → 检查）**
5. **登录账号A，抓取**
   - 查看 Console：`isSwitched: false`
   - 看到头像显示
6. **切换到账号B，刷新抖音页面**
7. **打开扩展**
   - 查看 Console：`isSwitched: true`
   - **头像应该消失**

### 如果头像仍然显示
1. 查看 Console 的 `isSwitched` 值
2. 如果是 `false`，说明指纹没变化（可能同一个账号）
3. 如果是 `true` 但头像还在，说明代码没更新 → 完全卸载重装

---

**当前状态**: 代码已更新，版本 v1.3.0  
**下一步**: 完全卸载重装扩展，查看 Console 调试日志
