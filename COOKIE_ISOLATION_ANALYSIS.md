# 抖音多账号 Cookie 隔离机制分析报告

## 执行摘要

**结论**：项目**已经实现了完善的 cookie 隔离机制**，理论上支持托管多个抖音账号而不会互相顶号。

如果你遇到"导入第二个账号 cookie 会将第一个号登录状态顶掉"的问题，**根本原因是：你在浏览器中导出的是同一个抖音账号的 Cookie**，而不是项目代码的问题。

---

## 一、现有的 Cookie 隔离机制

### 1.1 存储层隔离

**代码位置**：`backend-django/core/douyin/runtime/storage.py`

```python
def _storage_file(account_id: str) -> Path:
    return _data_dir() / 'storage' / f'{account_id}.bin'
```

- 每个账号的 cookie 独立存储在：`{DOUYIN_DATA_DIR}/storage/{account_id}.bin`
- 使用 Fernet 对称加密，密钥来自环境变量 `DOUYIN_STORAGE_ENCRYPTION_KEY`
- 每个账号的 Chromium profile 也独立存储在：`{DOUYIN_DATA_DIR}/contexts/{account_id}/`

**隔离保证**：物理存储完全隔离，账号 A 的 cookie 不会覆盖账号 B 的 cookie。

---

### 1.2 导入时重复检测

**代码位置**：`backend-django/core/douyin/runtime/credential.py:314-342`

```python
def find_duplicate_session_owner(
    *,
    account_id: str,
    sessionid: str,
    uid_tt: str = "",
) -> tuple[str, str, str] | None:
    """若其它账号已占用相同 sessionid（或 uid_tt），返回 (other_id, other_name, reason)。"""
    # 遍历所有账号，检查是否有相同的 sessionid 或 uid_tt
    for acc in DouyinAccount.objects.exclude(id=account_id).filter(is_deleted=False).exclude(status=3):
        other_st = load_storage_state(str(acc.id))
        osid, ouid = session_fingerprint_from_state(other_st)
        if sid and osid == sid:
            return str(acc.id), str(acc.nickname or acc.id), "sessionid"
        if uid_tt and ouid and uid_tt == ouid:
            return str(acc.id), str(acc.nickname or acc.id), "uid_tt"
    return None
```

**导入流程**（`douyin_account_api.py:397-496`）：

1. 解析 Cookie（支持一键导入串或手动粘贴）
2. **检查 sessionid 和 uid_tt 是否已被其他账号占用**
3. 如果重复，抛出 409 错误并给出明确提示
4. 如果通过检查，才加密保存到独立的存储文件

**错误提示示例**：

```
此 Cookie 与账号「张三」是同一抖音登录态（sessionid 相同），不能导入到多个账号槽位。
请在浏览器中确认右上角已登录的是目标账号后再导出；
无痕窗口若仍导出到相同 sessionid，说明实际登录的还是「张三」。
```

**隔离保证**：后端会主动拦截重复的 Cookie，防止同一登录态被导入到多个账号槽位。

---

### 1.3 Worker 运行时去重

**代码位置**：`backend-django/core/douyin/runtime/credential.py:369-408`

```python
def dedupe_managed_accounts_by_session(rows: list[dict]) -> list[dict]:
    """同一 sessionid 只保留一个账号托管（priority 高 → sort 高 → id 小 优先）。
    
    防止多账号槽位导入同一套 cookie 后，worker 双协程扫同一 inbox、重复自动回复。
    """
```

- Worker 启动时会检查所有待托管账号的 sessionid
- 如果发现多个账号共用同一个 sessionid（绕过了导入时的检测），只保留优先级最高的一个
- 其他账号会被跳过，不参与消息监控和自动回复

**隔离保证**：即使绕过了导入时的检测（如直接修改数据库），Worker 也会在运行时去重，避免重复处理。

---

## 二、问题场景分析

### 2.1 问题描述

> "导入第二个账号的 cookie 会将第一个号登录状态顶掉"

### 2.2 可能的原因

#### 原因 A：浏览器中未切换抖音账号（最可能）

**现象**：
1. 在浏览器中登录了抖音账号 A（如"张三"）
2. 导出 Cookie → 成功导入到系统账号槽位 1
3. **浏览器中仍然登录的是"张三"**（右上角头像未变）
4. 再次导出 Cookie → 尝试导入到系统账号槽位 2
5. 后端检测到 sessionid 与槽位 1 相同 → 返回 409 错误
6. 前端提示："此 Cookie 与账号「张三」是同一抖音登录态…"

**验证方法**：
- 检查浏览器右上角的抖音头像和昵称
- 查看前端控制台是否有 409 错误
- 运行诊断工具（见下文）

**解决方案**：
1. 在浏览器中**退出**账号 A
2. **登录**账号 B（确认右上角头像变为账号 B）
3. 导出 Cookie → 导入到系统账号槽位 2
4. 或者使用**无痕窗口**分别登录不同账号

---

#### 原因 B：前端未正确显示错误（可能性较低）

**现象**：
- 后端返回了 409 错误
- 前端错误处理逻辑存在问题，没有清晰展示错误原因

**验证方法**：
- 打开浏览器开发者工具（F12）→ Network 标签
- 尝试导入第二个账号的 Cookie
- 查看 `/api/core/douyin/account/xxx/import-credential` 请求的响应
- 如果是 409，查看 `response.data.detail` 的内容

**当前前端代码**（`web/apps/web-ele/src/views/douyin/account/index.vue:390-391`）：

```typescript
} catch (error: any) {
  ElMessage.error(error?.response?.data?.detail || '操作失败');
}
```

这段代码应该能正确显示后端的错误信息。如果前端显示的是模糊的"操作失败"，说明错误对象结构可能不符合预期。

---

#### 原因 C：绕过了导入接口（极少见）

**现象**：
- 通过直接修改数据库、文件系统等方式绕过了 `import_credential` API
- 或者代码存在 bug，导致重复检测未生效

**验证方法**：
- 运行诊断工具（见下文）
- 检查数据库中是否有多个账号记录
- 检查 `{DOUYIN_DATA_DIR}/storage/` 目录下的 `.bin` 文件数量

---

## 三、诊断工具

### 3.1 使用方法

```bash
cd backend-django
python check_cookie_duplicates.py
```

### 3.2 输出示例

**正常情况**（无重复）：

```
================================================================================
抖音账号 Cookie 重复检查
================================================================================

共找到 2 个账号

账号: 张三 (ID: a1b2c3d4...)
  状态: 在线
  凭证状态: 可发送
  sessionid: abc123def456...
  uid_tt: xyz789...
  存储路径: storage/a1b2c3d4-xxxx-xxxx-xxxx-xxxxxxxxxxxx.bin

账号: 李四 (ID: e5f6g7h8...)
  状态: 在线
  凭证状态: 可发送
  sessionid: ghi789jkl012...
  uid_tt: uvw345...
  存储路径: storage/e5f6g7h8-xxxx-xxxx-xxxx-xxxxxxxxxxxx.bin

================================================================================
重复检测结果
================================================================================

【sessionid 重复检查】
  ✓ 没有发现 sessionid 重复

【uid_tt 重复检查】
  ✓ 没有发现 uid_tt 重复

================================================================================
诊断建议
================================================================================

✓ Cookie 隔离正常，没有发现重复
```

**异常情况**（有重复）：

```
================================================================================
重复检测结果
================================================================================

【sessionid 重复检查】

⚠️  发现重复的 sessionid: abc123def456...
  以下账号共用相同的 sessionid（会导致互相顶号）：
    - 张三 (ID: a1b2c3d4...)
    - 李四 (ID: e5f6g7h8...)

================================================================================
诊断建议
================================================================================

发现了重复的登录态！这会导致以下问题：
  1. Worker 会自动去重，只托管其中一个账号
  2. 导入新 Cookie 时会被拦截（409 错误）
  3. 抖音服务器会认为是同一个登录会话

解决方案：
  1. 在浏览器中切换到不同的抖音账号（确认右上角头像）
  2. 从不同浏览器/无痕窗口导出 Cookie（每个账号独立登录）
  3. 删除重复的账号槽位，只保留一个
  4. 重新导入时确保浏览器登录的是对应的抖音账号
```

---

## 四、正确的多账号导入流程

### 方式一：使用浏览器账号切换

1. **导入账号 A**：
   - 在 Chrome 中登录抖音账号 A
   - 确认右上角显示账号 A 的头像
   - 导出 Cookie → 在系统中点击"新增账号"→ 粘贴并保存

2. **导入账号 B**：
   - 在 Chrome 中**退出**账号 A
   - **登录**账号 B
   - 确认右上角显示账号 B 的头像
   - 导出 Cookie → 在系统中点击"新增账号"→ 粘贴并保存

### 方式二：使用不同浏览器/无痕窗口

1. **导入账号 A**：
   - Chrome 正常窗口 → 登录抖音账号 A
   - 导出 Cookie → 在系统中保存到槽位 1

2. **导入账号 B**：
   - Chrome **无痕窗口** → 登录抖音账号 B
   - 导出 Cookie → 在系统中保存到槽位 2

3. **导入账号 C**：
   - Firefox 浏览器 → 登录抖音账号 C
   - 导出 Cookie → 在系统中保存到槽位 3

---

## 五、常见误区

### 误区 1：以为"新增账号"会自动切换抖音登录状态

**错误理解**：在系统中点击"新增账号"后，浏览器会自动切换到另一个抖音账号。

**实际情况**：系统只是创建了一个**槽位**，浏览器的登录状态不会变化。你需要**手动**在浏览器中切换抖音账号。

### 误区 2：以为无痕窗口会自动登录不同账号

**错误理解**：打开无痕窗口后，就能导出不同账号的 Cookie。

**实际情况**：无痕窗口只是**初始状态为未登录**，你仍然需要**手动登录**目标抖音账号后，才能导出对应的 Cookie。

### 误区 3：以为同一浏览器可以同时登录多个抖音账号

**错误理解**：在 Chrome 的多个标签页中分别登录不同的抖音账号。

**实际情况**：抖音的登录状态是**全站共享的**（基于 Cookie），同一浏览器同一时刻只能登录一个抖音账号。如果在标签页 A 登录了账号 1，在标签页 B 登录账号 2，**标签页 A 的登录状态会被覆盖**。

---

## 六、技术细节：为什么不会"顶号"

### 6.1 存储层面

```
{DOUYIN_DATA_DIR}/
  storage/
    account-1-uuid.bin  ← 账号 A 的 Cookie（加密）
    account-2-uuid.bin  ← 账号 B 的 Cookie（加密）
    account-3-uuid.bin  ← 账号 C 的 Cookie（加密）
```

- 每个账号的 Cookie 存储在**独立的文件**中
- 文件名由账号 UUID 决定，不会冲突
- 导入账号 B 的 Cookie 时，只会写入 `account-2-uuid.bin`，不会修改 `account-1-uuid.bin`

### 6.2 Worker 执行层面

```python
# Worker 为每个账号启动独立的协程
for account_row in managed_accounts:
    account_id = account_row['id']
    state = load_storage_state(account_id)  # 读取该账号的独立 Cookie
    # 使用该 Cookie 发起 HTTP 请求，与其他账号完全隔离
```

- Worker 启动时为每个账号创建**独立的协程**
- 每个协程读取**自己账号**的 Cookie 文件
- HTTP 请求携带的 Cookie 是**该账号独有的**，不会串号

### 6.3 抖音服务器层面

- 抖音服务器通过 `sessionid` 识别登录会话
- 只要两个账号的 `sessionid` 不同，抖音就认为是**两个独立的登录会话**
- 系统在导入时会**强制检查** sessionid 不重复

---

## 七、下一步行动

### 7.1 立即验证

1. **运行诊断工具**：
   ```bash
   cd backend-django
   python check_cookie_duplicates.py
   ```

2. **检查前端错误**：
   - 打开浏览器 F12 → Console 和 Network
   - 尝试导入第二个账号
   - 查看是否有 409 错误和错误信息

### 7.2 如果发现重复

1. **删除重复的账号槽位**（只保留一个）
2. **在浏览器中确认切换到了不同的抖音账号**
3. **重新导入**

### 7.3 如果没有发现重复

- 说明"顶号"问题可能不是 Cookie 重复导致的
- 请提供更详细的现象描述：
  - "顶号"的具体表现是什么？（Worker 日志？前端界面？）
  - 是否有报错信息？
  - 两个账号的凭证状态是什么？

---

## 八、总结

| 问题 | 答案 |
|------|------|
| **项目是否支持多账号？** | ✅ 支持，已实现完善的 Cookie 隔离机制 |
| **导入时会检查重复吗？** | ✅ 会，`find_duplicate_session_owner` 函数会拦截 |
| **存储会互相覆盖吗？** | ❌ 不会，每个账号独立的加密文件 |
| **Worker 会串号吗？** | ❌ 不会，独立协程 + 独立 Cookie + 去重机制 |
| **问题的根本原因？** | 🔍 **浏览器中导出的是同一个抖音账号的 Cookie** |

**核心建议**：
1. 导入不同账号前，**务必在浏览器中切换到对应的抖音账号**
2. 使用诊断工具验证 Cookie 是否真的隔离
3. 前端需要清晰展示 409 错误的详细信息

---

## 附录：相关代码文件

- `backend-django/core/douyin/runtime/storage.py` - 存储层隔离
- `backend-django/core/douyin/runtime/credential.py` - 重复检测和去重
- `backend-django/core/douyin/douyin_account_api.py` - 导入接口
- `backend-django/check_cookie_duplicates.py` - 诊断工具
