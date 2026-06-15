# 抖音账号托管问题诊断报告

## 🔍 问题现象

- 后台显示两个账号在托管中：`哈啊` 和 `用户7139479680080`
- 向 `用户7139479680080` 发送消息没有触发自动回复
- 实际上这个账号已经不在线

## 📋 根本原因分析

根据 Docker Worker 日志分析，发现了以下关键问题：

### 问题 1：两个账号使用了同一个抖音账号的 Cookie（已确认）

从历史日志中发现：
```
[2026-06-13] account=d2e80ae6-5be7-4d38-9854-81380b712973 self_uid=80549827440
[2026-06-13] account=3f4d65f1-6bdc-48a2-87da-54e96a029363 self_uid=80549827440
[2026-06-15] account=01e6e93f-fdfd-459a-9452-729f66da37e7 (哈啊) self_uid=80549827440
[2026-06-15] account=a7400003-1106-4a58-b92e-59b93b964346 (用户7139479680080) self_uid=80549827440
```

**结论**：多个账号ID对应的都是 `self_uid=80549827440`（同一个抖音账号）

**这意味着**：
- "用户7139479680080" 和 "哈啊" 实际上是同一个抖音账号
- 你在不同时间导入的是同一个抖音账号的 Cookie
- 这就是为什么给"用户7139479680080"发消息没有自动回复——因为它根本不是一个独立的账号

### 问题 2：Cookie 已经全部失效（当前状态）

最新的日志显示：
```
[2026-06-15 09:28:45] self_uid=0 (哈啊)
[2026-06-15 09:28:45] self_uid=0 (用户7139479680080)
```

**`self_uid=0` 表示**：
- Cookie 已失效，无法获取当前登录账号的 user_id
- 抖音服务器已经不认可这个登录态了
- Worker 仍在运行，但实际上已经无法接收消息

## ✅ 解决方案

### 方案 1：删除重复账号并重新导入（推荐）

1. **删除其中一个账号槽位**
   - 建议删除 `用户7139479680080`（因为它只是一个临时昵称）
   - 保留 `哈啊`

2. **重新导入 Cookie**
   - 在浏览器中确认已登录 `哈啊` 这个抖音账号
   - 导出最新的 Cookie
   - 更新 `哈啊` 账号的登录态

3. **如果需要托管第二个抖音账号**
   - 在浏览器中**退出当前账号**
   - **登录另一个抖音账号**（确认右上角头像变化）
   - 导出 Cookie
   - 在系统中创建新账号并导入

### 方案 2：检查并修复重复问题

运行诊断工具检查当前状态：

```bash
cd /Users/long/Work/DyAuthReply/backend-django
docker exec zq-backend python manage.py shell << 'PYEOF'
from core.douyin.douyin_account_model import DouyinAccount
from core.douyin.runtime.storage import load_storage_state
from core.douyin.runtime.credential import session_fingerprint_from_state

accounts = DouyinAccount.objects.filter(is_deleted=False)
print(f"账号数量: {accounts.count()}")

session_map = {}
for acc in accounts:
    state = load_storage_state(str(acc.id))
    if state:
        sessionid, uid_tt = session_fingerprint_from_state(state)
        if sessionid:
            if sessionid not in session_map:
                session_map[sessionid] = []
            session_map[sessionid].append(acc.nickname)

for sid, names in session_map.items():
    if len(names) > 1:
        print(f"\n⚠️  重复的 sessionid:")
        for name in names:
            print(f"  - {name}")
PYEOF
```

## 🎯 为什么会出现这个问题

### 常见原因

1. **浏览器中未切换账号**
   - 第一次导入后，浏览器仍然登录的是同一个抖音账号
   - 第二次导入时，导出的仍然是这个账号的 Cookie

2. **误以为"新增账号"会自动切换登录态**
   - 系统的"新增账号"只是创建了一个**槽位**
   - 浏览器的登录状态不会自动改变

3. **无痕窗口未重新登录**
   - 打开无痕窗口后，如果直接导出 Cookie
   - 可能仍然会获取到原账号的登录态（跨窗口共享）

### 系统的保护机制

项目**已经实现了重复检测**（`find_duplicate_session_owner`），应该会拦截重复的 Cookie。

**但是**，如果：
1. 你删除了旧账号后再导入相同 Cookie
2. 或者绕过了导入接口（直接修改数据库）
3. 或者两次导入之间抖音刷新了 sessionid（极少见）

就可能绕过检测，导致重复托管。

## 📊 Cookie 池机制说明

项目**确实支持多账号托管**，并且每个账号的 Cookie **物理隔离**：

```
{DOUYIN_DATA_DIR}/storage/
  ├── 01e6e93f-fdfd-459a-9452-729f66da37e7.bin  # 哈啊 的 Cookie
  └── a7400003-1106-4a58-b92e-59b93b964346.bin  # 用户7139479680080 的 Cookie
```

但是，如果两个 `.bin` 文件里存储的是**同一个抖音账号的 Cookie**，那就会出现你遇到的问题。

## 🔧 立即行动步骤

### 步骤 1：确认当前状态

```bash
# 查看 worker 日志中的 self_uid
docker logs zq-douyin-worker 2>&1 | grep "self_uid" | tail -20
```

如果看到 `self_uid=0`，说明 Cookie 已失效。

### 步骤 2：在后台查看账号列表

访问：http://localhost:8000/api/docs （或你的后台地址）
- 检查 `/core/douyin/account` 接口
- 查看两个账号的 `sec_uid` 字段
- **如果 sec_uid 相同**，确认是重复账号

### 步骤 3：删除重复账号

在后台账号管理页面：
1. 删除 `用户7139479680080`
2. 保留 `哈啊`

### 步骤 4：重新导入 Cookie

1. 在浏览器中登录抖音创作者中心
2. 确认右上角是 `哈啊` 这个账号
3. 导出 Cookie（使用浏览器扩展或手动复制）
4. 在后台点击 `哈啊` 账号的"导入登录态"
5. 粘贴 Cookie 并保存

### 步骤 5：添加第二个账号（可选）

如果你确实需要托管第二个抖音账号：

1. 在浏览器中**退出**当前账号
2. **登录**第二个抖音账号
3. 确认右上角头像和昵称变化
4. 导出 Cookie
5. 在系统中点击"新增账号"
6. 粘贴 Cookie 并保存

## ⚠️ 常见错误提示

如果导入时看到以下错误，说明检测到了重复：

```
此 Cookie 与账号「哈啊」是同一抖音登录态（sessionid 相同），不能导入到多个账号槽位。
请在浏览器中确认右上角已登录的是目标账号后再导出。
```

**正确做法**：
- 不是删除错误提示
- 而是确保浏览器登录的是**不同的抖音账号**

## 📝 总结

| 问题 | 现状 |
|------|------|
| **Cookie 是否隔离？** | ✅ 物理文件隔离，机制正常 |
| **是否有重复检测？** | ✅ 有，但可能被绕过 |
| **当前问题根因？** | ❌ 两个槽位导入了同一个抖音账号的 Cookie |
| **Cookie 是否有效？** | ❌ 当前 Cookie 已失效（self_uid=0） |
| **解决方案？** | ✅ 删除重复账号 + 重新导入 + 确保浏览器切换账号 |

**核心要点**：
- 项目的 Cookie 隔离机制是正常的
- 问题出在**操作流程**上：浏览器中未切换到不同的抖音账号
- "用户7139479680080" 不是一个独立的抖音账号，它和"哈啊"是同一个账号
