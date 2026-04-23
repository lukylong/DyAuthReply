# 🚀 登录日志模块 - 快速开始指南

欢迎使用登录日志模块！这是一个完整的、生产级别的用户登录操作记录系统。

## 📚 文档导航

### 新手必读 📖

1. **本文件** - 快速开始指南（5分钟阅读）
2. **[README.md](README.md)** - 功能概述和完整文档（20分钟阅读）
3. **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** - API 快速查询（10分钟）

### 开发者指南 👨‍💻

4. **[MIGRATION_GUIDE.md](MIGRATION_GUIDE.md)** - 数据库迁移和认证系统集成
5. **[INTEGRATION_COMPLETE.md](INTEGRATION_COMPLETE.md)** - 集成完成报告
6. **[DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md)** - 部署清单

### 参考文档 📋

7. **[IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md)** - 实现总结

## ⚡ 3分钟快速开始

### 第一步：创建数据库表

```bash
cd /Users/zcl/Project/fuadmin-/backend-v5

# 生成迁移
python manage.py makemigrations core

# 应用迁移
python manage.py migrate
```

### 第二步：启动服务

```bash
python manage.py runserver 8000
```

### 第三步：测试登录

```bash
# 使用你的用户名和密码登录
curl -X POST "http://localhost:8000/api/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"your_password"}'
```

登录日志已自动记录！

## 📊 核心功能

### ✅ 自动记录

- ✅ 成功登录
- ✅ 失败登录（含失败原因）
- ✅ 用户IP地址
- ✅ 浏览器信息
- ✅ 设备类型
- ✅ 登录时间

### ✅ 查询功能

```bash
# 获取登录统计
curl http://localhost:8000/api/login-log/stats/overview?days=30 \
  -H "Authorization: Bearer YOUR_TOKEN"

# 查询用户登录日志
curl "http://localhost:8000/api/login-log?username=admin" \
  -H "Authorization: Bearer YOUR_TOKEN"

# 检测可疑登录（暴力破解）
curl http://localhost:8000/api/login-log/suspicious \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### ✅ Python 代码

```python
from core.login_log.login_log_service import LoginLogService

# 获取用户登录次数
count = LoginLogService.get_user_login_count(username="admin", days=30)

# 检测异常登录
suspicious = LoginLogService.get_suspicious_logins()

# 生成报告
stats = LoginLogService.get_login_stats(days=30)
print(f"登录成功率: {stats['success_rate']}%")
```

## 🎯 常见使用场景

### 场景1：查看用户登录历史

```bash
# 查看某用户的最后一次登录
curl http://localhost:8000/api/login-log/user/{user_id}/last \
  -H "Authorization: Bearer YOUR_TOKEN"

# 查看用户登录过的IP
curl http://localhost:8000/api/login-log/user/{user_id}/ips?days=30 \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### 场景2：生成安全报告

```python
from core.login_log.login_log_service import LoginLogService

# 获取每日统计
daily = LoginLogService.get_daily_stats(days=7)

for stat in daily:
    print(f"{stat['date']}: 登录 {stat['total_logins']} 次，成功 {stat['success_logins']} 次")
```

### 场景3：检测攻击

```python
from core.login_log.login_log_service import LoginLogService

# 检查1小时内失败5次以上的尝试
suspicious = LoginLogService.get_suspicious_logins(
    max_failed_attempts=5,
    hours=1
)

if suspicious:
    print("⚠️ 检测到可疑登录，建议立即审查！")
    for record in suspicious:
        print(f"  用户: {record['username']}, IP: {record['login_ip']}")
```

## 📈 API 端点速览

| 方法  | 端点                              | 说明     |
|-----|---------------------------------|--------|
| GET | `/api/login-log`                | 查询日志列表 |
| GET | `/api/login-log/stats/overview` | 登录统计   |
| GET | `/api/login-log/user/{user_id}` | 用户登录日志 |
| GET | `/api/login-log/suspicious`     | 可疑登录   |

更多端点？查看 **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)**

## 🔒 安全特性

- ✅ **自动防暴力破解** - 5次失败自动锁定
- ✅ **完整的审计记录** - 所有操作都被记录
- ✅ **失败原因分类** - 便于安全分析
- ✅ **异常检测** - 检测可疑登录模式

## 🆘 遇到问题？

### 问题1：表未创建

```bash
# 运行迁移
python manage.py migrate

# 验证表
python manage.py dbshell
# 执行：SHOW TABLES LIKE 'core_login_log';
```

### 问题2：登录日志未记录

```python
# 手动测试
python manage.py shell
from core.login_log.login_log_service import LoginLogService
log = LoginLogService.record_success_login("admin", "user-id", "127.0.0.1")
print("记录成功" if log else "记录失败")
```

### 问题3：查看详细错误

查看日志文件：

```bash
tail -f logs/auth.log
```

### 还有其他问题？

查看 **[DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md)** 中的"常见问题和解决"

## 📋 模块文件说明

```
login_log/
├── __init__.py                    # 模块初始化
├── login_log_model.py            # 数据库模型（核心）
├── login_log_schema.py           # 数据验证
├── login_log_service.py          # 业务逻辑（常用）
├── login_log_api.py              # REST API（接口）
├── README.md                     # 📖 完整文档
├── QUICK_REFERENCE.md            # 📖 快速查询
├── MIGRATION_GUIDE.md            # 📖 集成指南
├── IMPLEMENTATION_SUMMARY.md     # 📖 实现总结
├── INTEGRATION_COMPLETE.md       # 📖 集成报告
├── DEPLOYMENT_CHECKLIST.md       # 📖 部署清单
└── 00_START_HERE.md             # 👈 你在这里
```

## 🎓 学习路径

### 初级（理解功能）

1. 阅读本文件
2. 查看 [README.md](README.md) 的"功能特性"部分
3. 尝试查询日志

### 中级（自定义使用）

1. 查看 [QUICK_REFERENCE.md](QUICK_REFERENCE.md)
2. 学习 Service 方法
3. 编写自定义查询

### 高级（系统集成）

1. 阅读 [MIGRATION_GUIDE.md](MIGRATION_GUIDE.md)
2. 理解认证系统集成
3. 自定义告警规则

## 💡 常用代码片段

### 获取统计信息

```python
from core.login_log.login_log_service import LoginLogService

# 这个月的登录统计
stats = LoginLogService.get_login_stats(days=30)
print(f"成功率: {stats['success_rate']}%")
```

### 检测异常

```python
# 检查用户是否应该被锁定
should_lock = LoginLogService.check_user_locked(
    username="admin",
    failed_threshold=5,
    hours=1
)

if should_lock:
    print("用户应该被锁定")
```

### 清理旧数据

```python
# 清理90天前的日志
deleted = LoginLogService.clean_old_logs(days=90)
print(f"清理了 {deleted} 条记录")
```

## 📞 获取帮助

1. **文档** - 查看各个 .md 文件
2. **代码注释** - 查看源代码中的详细注释
3. **API 文档** - 访问 `/api/docs` 查看 Swagger 文档
4. **日志** - 查看 `logs/auth.log` 了解详细信息

## ✨ 特色功能

### 🛡️ 安全防护

- 自动防暴力破解
- 异常登录检测
- IP 地址追踪

### 📊 数据分析

- 登录趋势分析
- 设备统计
- IP 统计

### 🔍 审计追踪

- 完整的失败原因
- 用户行为记录
- 会话追踪

## 🚀 下一步

1. **立即开始** - 运行迁移，让系统记录登录日志
2. **查看数据** - 通过 API 查询登录日志
3. **设置监控** - 配置告警规则
4. **定期维护** - 清理旧日志，优化性能

## 📚 完整文档导航

| 文档                                                     | 内容       | 阅读时间  |
|--------------------------------------------------------|----------|-------|
| 本文件                                                    | 快速开始     | 3 分钟  |
| [README.md](README.md)                                 | 完整功能说明   | 20 分钟 |
| [QUICK_REFERENCE.md](QUICK_REFERENCE.md)               | API 快速查询 | 10 分钟 |
| [MIGRATION_GUIDE.md](MIGRATION_GUIDE.md)               | 迁移和集成    | 15 分钟 |
| [INTEGRATION_COMPLETE.md](INTEGRATION_COMPLETE.md)     | 集成报告     | 10 分钟 |
| [DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md)     | 部署清单     | 15 分钟 |
| [IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md) | 实现总结     | 10 分钟 |

---

## 🎉 你已准备好了！

登录日志模块已完全集成到认证系统。

**现在就开始吧**：

```bash
python manage.py migrate
python manage.py runserver 8000
```

然后访问 `/api/login-log` 查看你的登录日志！

---

**版本**：1.0.0  
**状态**：✅ 已完成，可部署  
**更新时间**：2024

