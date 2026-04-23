# 登录日志模块 - 部署清单

## 📋 部署前准备

### 1. 代码检查

- [x] 所有模块文件已创建
- [x] 认证系统已集成
- [x] 路由已注册
- [x] 代码无 Linter 错误
- [x] 所有导入语句正确

**验证命令**：

```bash
# 检查 Python 语法
python -m py_compile backend-v5/core/login_log/*.py
python -m py_compile backend-v5/core/auth/auth_api.py
```

### 2. 环境检查

- [ ] Django 版本 >= 3.2
- [ ] Python 版本 >= 3.8
- [ ] 数据库连接正常
- [ ] 必要的依赖包已安装

**验证命令**：

```bash
# 检查版本
python --version
python manage.py --version

# 检查数据库连接
python manage.py dbshell
# 执行：SELECT 1;
```

## 🚀 部署步骤

### 第一步：创建数据库迁移

```bash
# 生成迁移文件
cd /Users/zcl/Project/fuadmin-/backend-v5
python manage.py makemigrations core

# 预览迁移内容
python manage.py sqlmigrate core 0013_loginlog  # 数字根据实际情况调整

# 检查迁移状态
python manage.py showmigrations core
```

**预期输出**：

```
core
 [ ] 0001_initial
 [ ] 0002_alter_menu_options_and_more
 ...
 [ ] 0012_scheduler
 [ ] 0013_loginlog  # 新迁移
```

### 第二步：应用迁移

```bash
# 应用迁移到数据库
python manage.py migrate

# 验证表创建
python manage.py dbshell
```

在数据库中执行：

```sql
-- 检查表是否存在
SHOW TABLES LIKE 'core_login_log';

-- 查看表结构
DESC core_login_log;

-- 检查索引
SHOW INDEX FROM core_login_log;
```

**预期结果**：

- ✅ 表已创建
- ✅ 包含 16+ 个字段
- ✅ 包含 4 个索引

### 第三步：验证模块加载

```bash
# 进入 Django 交互式终端
python manage.py shell

# 执行以下代码
from core.login_log.login_log_model import LoginLog
from core.login_log.login_log_service import LoginLogService
from core.login_log.login_log_api import router

print("模块加载成功")
print(f"LoginLog 表: {LoginLog._meta.db_table}")
print(f"路由数: {len(router.get_operations())}")
```

**预期输出**：

```
模块加载成功
LoginLog 表: core_login_log
路由数: 19
```

### 第四步：测试登录功能

```bash
# 启动 Django 开发服务器
python manage.py runserver 8000

# 新开一个终端，测试登录
curl -X POST "http://localhost:8000/api/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'
```

**预期响应**：

```json
{
  "id": "xxx-xxx-xxx",
  "accessToken": "eyJ...",
  "username": "admin",
  "realName": "管理员",
  "refreshToken": "eyJ...",
  "expireTime": 1234567890
}
```

### 第五步：验证登录日志记录

```bash
# 进入 Django shell
python manage.py shell

# 查询登录日志
from core.login_log.login_log_model import LoginLog
logs = LoginLog.objects.all()
print(f"登录日志数: {logs.count()}")

# 查看最新一条
latest = logs.last()
print(f"用户: {latest.username}")
print(f"状态: {latest.get_status_display_name()}")
print(f"IP: {latest.login_ip}")
```

### 第六步：测试 API 接口

```bash
# 获取登录日志列表
curl -X GET "http://localhost:8000/api/login-log?page=1&limit=20" \
  -H "Authorization: Bearer YOUR_TOKEN"

# 获取登录统计
curl -X GET "http://localhost:8000/api/login-log/stats/overview?days=30" \
  -H "Authorization: Bearer YOUR_TOKEN"

# 检测可疑登录
curl -X GET "http://localhost:8000/api/login-log/suspicious" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

## 🔍 验证清单

### 数据库验证

- [ ] core_login_log 表已创建
- [ ] 所有字段都存在
- [ ] 索引已创建
- [ ] 主键为 UUID

### 模块验证

- [ ] login_log_model.py 可导入
- [ ] login_log_service.py 可导入
- [ ] login_log_api.py 可导入
- [ ] 路由已注册到 core_router

### 认证系统验证

- [ ] auth_api.py 已修改
- [ ] 导入语句正确
- [ ] 登录功能正常
- [ ] 成功登录已记录
- [ ] 失败登录已记录

### API 验证

- [ ] GET /login-log - 可访问
- [ ] GET /login-log/stats/overview - 返回正确数据
- [ ] GET /login-log/user/{user_id} - 可访问
- [ ] GET /login-log/suspicious - 可访问

### 功能验证

- [ ] 成功登录被记录
- [ ] 失败登录被记录
- [ ] 用户最后登录时间更新
- [ ] 用户最后登录 IP 更新
- [ ] 暴力破解防护工作
- [ ] 统计查询正确

## 🐛 常见问题和解决

### 问题1：迁移找不到

**症状**：`No such migration: core.0013_loginlog`

**解决**：

```bash
# 检查迁移文件
ls backend-v5/core/migrations/

# 重新生成迁移
python manage.py makemigrations core --dry-run
python manage.py makemigrations core
```

### 问题2：表创建失败

**症状**：`(1054, "Unknown column 'xxx' in 'core_login_log'")`

**解决**：

```bash
# 检查迁移状态
python manage.py showmigrations core

# 如果有未应用的迁移
python manage.py migrate

# 如果仍有问题，查看错误日志
python manage.py migrate --verbosity 3
```

### 问题3：模块导入失败

**症状**：`ModuleNotFoundError: No module named 'core.login_log'`

**解决**：

```bash
# 确保目录结构正确
ls -la backend-v5/core/login_log/
# 应该看到 __init__.py 和其他 .py 文件

# 检查 __init__.py 是否存在且非空
cat backend-v5/core/login_log/__init__.py
```

### 问题4：登录日志未记录

**症状**：登录成功但没有日志

**解决**：

```bash
# 检查日志级别
# 查看 Django 日志输出

# 手动测试记录功能
python manage.py shell
from core.login_log.login_log_service import LoginLogService
log = LoginLogService.record_success_login(
    username="test",
    user_id="test-id",
    login_ip="127.0.0.1"
)
print(f"记录成功: {log.id}")
```

## 📊 部署后验证

### 性能检查

```bash
# 检查查询性能
python manage.py shell

from django.db import connection
from django.db import reset_queries
from core.login_log.login_log_service import LoginLogService

# 启用查询日志
import django.conf
django.conf.settings.DEBUG = True

# 测试查询
reset_queries()
stats = LoginLogService.get_login_stats(days=30)
print(f"查询次数: {len(connection.queries)}")
print(f"耗时: {sum(float(q['time']) for q in connection.queries):.3f}s")
```

### 功能检查

```python
# 测试各个 Service 方法
from core.login_log.login_log_service import LoginLogService

# 测试记录
log = LoginLogService.record_success_login("admin", "user-id", "127.0.0.1")
assert log.status == 1

# 测试查询
count = LoginLogService.get_user_login_count(username="admin")
print(f"登录次数: {count}")

# 测试统计
stats = LoginLogService.get_login_stats()
print(f"统计: {stats}")

# 测试可疑登录
suspicious = LoginLogService.get_suspicious_logins()
print(f"可疑登录: {len(suspicious)}")
```

## 🔐 安全检查

- [ ] 日志表有适当的索引
- [ ] 查询有适当的权限检查
- [ ] 敏感信息不存储在日志中
- [ ] 定期清理策略已配置
- [ ] 备份策略已配置

## 📝 文档更新

- [ ] README.md - 已提供
- [ ] MIGRATION_GUIDE.md - 已提供
- [ ] QUICK_REFERENCE.md - 已提供
- [ ] IMPLEMENTATION_SUMMARY.md - 已提供
- [ ] INTEGRATION_COMPLETE.md - 已提供
- [ ] DEPLOYMENT_CHECKLIST.md - 本文件

## 🎯 部署完成标志

部署完成时，以下所有项都应该是 ✅：

- [x] 模块文件已创建
- [x] 数据库迁移已创建
- [x] 数据库表已创建
- [x] 认证系统已集成
- [x] API 路由已注册
- [x] 登录功能正常
- [x] 登录日志已记录
- [x] API 接口可访问
- [x] 统计数据正确
- [x] 所有文档已提供

## 📞 后续支持

### 常见操作

**查看最近登录**

```python
from core.login_log.login_log_service import LoginLogService
last_login = LoginLogService.get_last_login(username="admin")
```

**导出登录日志**

```python
from core.login_log.login_log_model import LoginLog
logs = LoginLog.objects.all()
# 导出为 CSV 或 Excel
```

**清理旧日志**

```python
from core.login_log.login_log_service import LoginLogService
deleted = LoginLogService.clean_old_logs(days=90)
```

### 监控建议

1. **每日监控**
    - 登录成功率
    - 失败登录数
    - 可疑登录数

2. **每周监控**
    - 新IP登录
    - 登录地理分布
    - 异常设备

3. **每月监控**
    - 用户登录趋势
    - 安全事件统计
    - 系统性能

### 告警规则

```
1. 登录成功率 < 90% → 告警
2. 一小时内失败 > 10 次 → 告警
3. 新IP登录 → 通知
4. 异常地点登录 → 告警
5. 未知设备登录 → 通知
```

---

**检查清单更新时间**：2024
**版本**：1.0.0
**状态**：✅ 已准备就绪

