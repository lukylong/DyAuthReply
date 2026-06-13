# 🔧 导入账号问题排查与修复

## ✅ 已修复的问题

### 1. Bundle 空字符串导致解析失败
**问题**: 当用户没有填写一键导入串时，前端发送空字符串，后端尝试解析空字符串导致 JSON 解析错误

**修复**: 添加了空字符串检查
```python
# 修复前
if data.bundle:
    unpacked = parse_credential_bundle(data.bundle)  # 空字符串会失败

# 修复后
if data.bundle and data.bundle.strip():
    try:
        unpacked = parse_credential_bundle(data.bundle)
    except ValueError as e:
        account.delete()
        raise HttpError(400, f"一键导入串解析失败：{e}")
```

### 2. 异步函数异常处理不足
**问题**: `asyncio.run(_fetch_profile())` 可能因为网络问题或 Cookie 无效而挂起或失败，没有友好的错误提示

**修复**: 添加了异常捕获和详细错误信息
```python
try:
    profile = asyncio.run(_fetch_profile())
except Exception as e:
    logger.warning(f"[quick_create] 获取账号信息失败: {e}")
    account.delete()
    raise HttpError(500, f"无法获取账号信息：{str(e)}")
```

## 📋 正确的测试步骤

### 方式1: 使用一键导入串（推荐）
1. 刷新浏览器（Ctrl+Shift+R）
2. 进入"抖音账号管理"页面
3. 点击"新增账号"
4. 在"一键导入串"输入框粘贴扩展生成的导入串（以 `DYCRED1.` 开头）
5. 点击提交

**预期**: 
- ✅ 成功创建账号
- ✅ 昵称自动获取
- ✅ 头像自动获取
- ✅ sec_uid 自动获取

### 方式2: 手动填写 Cookie
1. 点击"新增账号"
2. 点击"手动填写（高级）"展开
3. 在"Cookie"输入框粘贴完整的 Cookie 字符串（**必须包含 sessionid**）
4. 点击提交

**预期**: 
- ✅ 成功创建账号
- ✅ 昵称自动获取

### ❌ 常见错误

#### 错误1: 什么都不填就提交
**现象**: 提示"请粘贴扩展生成的「一键导入串」，或展开手动填写 Cookie"

**原因**: 前端验证拦截

#### 错误2: Cookie 缺少 sessionid
**现象**: 提示"Cookie 缺少 sessionid，请确认已登录抖音后再从浏览器复制完整 Cookie"

**原因**: Cookie 不完整或未登录

**解决**: 
1. 确保已在浏览器登录抖音
2. 使用浏览器开发者工具复制完整 Cookie
3. 或使用浏览器扩展生成一键导入串

#### 错误3: 无法获取账号信息
**现象**: 提示"无法获取账号信息，请检查 Cookie 是否有效"

**可能原因**:
1. Cookie 已过期
2. 网络连接问题
3. 抖音 API 限流
4. Cookie 对应的账号已注销

**解决**:
1. 重新登录抖音并获取新的 Cookie
2. 检查网络连接
3. 等待几分钟后重试

#### 错误4: 该抖音号已被占用
**现象**: 提示"该抖音号已被账号「XXX」占用，请勿重复导入同一登录态"

**原因**: 同一个抖音账号的 Cookie 已经导入过了

**解决**: 
1. 检查是否已存在该账号
2. 如果需要更新 Cookie，使用"导入Cookie"功能而不是"新增账号"

## 🔍 调试步骤

如果仍然遇到问题，请按以下步骤排查：

### 1. 查看浏览器控制台
按 F12 打开开发者工具，查看 Network 标签：
- 找到 `/api/core/douyin/account/quick-create` 请求
- 查看请求体（Request Payload）
- 查看响应（Response）

### 2. 查看后端日志
```bash
docker logs zq-backend --tail 100 | grep -E "quick_create|ERROR"
```

### 3. 测试 Cookie 是否有效
使用"导入Cookie"功能测试已有账号，看是否能成功更新

## 📊 修复状态

- ✅ Bundle 空字符串问题已修复
- ✅ 异常处理已增强
- ✅ 错误提示更友好
- ✅ 路由顺序已修复
- ✅ 后端已重启（16:59:13）

## 🎯 下一步

1. **刷新浏览器**，清除缓存
2. 使用**有效的 Cookie 或一键导入串**测试
3. 如果仍有问题，提供：
   - 浏览器控制台的错误信息
   - 后端日志中的错误堆栈
   - 使用的是一键导入串还是手动 Cookie

---
**最后更新**: 2026-06-13 16:59  
**状态**: 所有已知问题已修复，等待用户测试
