# 登录日志模块 - 快速参考

## 概览

| 项目          | 说明                           |
|-------------|------------------------------|
| **模块位置**    | `backend-v5/core/login_log/` |
| **主表**      | `core_login_log`             |
| **服务类**     | `LoginLogService`            |
| **API路由前缀** | `/api/login-log`             |
| **标签**      | `Core-LoginLog`              |

## 文件结构

```
login_log/
├── __init__.py                 # 模块初始化
├── login_log_model.py          # 数据库模型
├── login_log_schema.py         # Pydantic 验证模式
├── login_log_service.py        # 业务逻辑服务
├── login_log_api.py            # REST API 接口
├── README.md                   # 完整文档
├── MIGRATION_GUIDE.md          # 迁移指南
└── QUICK_REFERENCE.md          # 快速参考（本文件）
```

## 快速开始

### 1. 创建迁移和表

```bash
python manage.py makemigrations core
python manage.py migrate
```

### 2. 记录登录

```python
from core.login_log.login_log_service import LoginLogService

# 成功登录
LoginLogService.record_success_login(
    username="admin",
    user_id="user-id",
    login_ip="192.168.1.1"
)

# 失败登录
LoginLogService.record_failed_login(
    username="admin",
    login_ip="192.168.1.1",
    failure_reason=2,  # 密码错误
    failure_message="Password incorrect"
)
```

### 3. 查询数据

```python
# 获取用户登录次数
count = LoginLogService.get_user_login_count(username="admin", days=30)

# 获取登录统计
stats = LoginLogService.get_login_stats(days=30)

# 获取可疑登录
suspicious = LoginLogService.get_suspicious_logins()
```

## 常用 API 端点

### 日志查询

| 方法     | 端点                               | 说明       |
|--------|----------------------------------|----------|
| GET    | `/login-log`                     | 列表查询（分页） |
| GET    | `/login-log/{log_id}`            | 获取详情     |
| GET    | `/login-log/username/{username}` | 按用户名查询   |
| GET    | `/login-log/ip/{login_ip}`       | 按IP查询    |
| DELETE | `/login-log/{log_id}`            | 删除记录     |
| DELETE | `/login-log/batch/delete`        | 批量删除     |

### 统计分析

| 方法  | 端点                          | 说明         |
|-----|-----------------------------|------------|
| GET | `/login-log/stats/overview` | 登录统计概览     |
| GET | `/login-log/stats/ip`       | IP统计 TOP N |
| GET | `/login-log/stats/device`   | 设备统计       |
| GET | `/login-log/stats/user`     | 用户统计 TOP N |
| GET | `/login-log/stats/daily`    | 每日统计       |

### 用户查询

| 方法  | 端点                                | 说明       |
|-----|-----------------------------------|----------|
| GET | `/login-log/user/{user_id}`       | 用户登录日志   |
| GET | `/login-log/user/{user_id}/count` | 登录次数统计   |
| GET | `/login-log/user/{user_id}/last`  | 最后一次登录   |
| GET | `/login-log/user/{user_id}/ips`   | 登录过的IP列表 |

### 安全相关

| 方法   | 端点                                      | 说明     |
|------|-----------------------------------------|--------|
| GET  | `/login-log/suspicious`                 | 可疑登录记录 |
| GET  | `/login-log/failed-attempts/{username}` | 失败次数   |
| POST | `/login-log/record`                     | 记录日志   |
| POST | `/login-log/clean`                      | 清理旧日志  |

## 数据库字段

### 核心字段

```
id                  UUID 主键
username            用户名 (VARCHAR 150)
user_id             用户ID (VARCHAR 36, 可选)
status              登录状态 (0=失败, 1=成功)
```

### 结果字段

```
failure_reason      失败原因编码 (0-7)
failure_message     失败详细信息
```

### 网络字段

```
login_ip            登录IP地址
ip_location         IP属地/地区
```

### 设备字段

```
user_agent          用户代理字符串
browser_type        浏览器类型
os_type             操作系统类型
device_type         设备类型 (desktop/mobile/tablet/other)
```

### 会话字段

```
session_id          会话ID (唯一)
duration            会话时长（秒）
```

## 失败原因代码

```
0 - 未知错误 (Unknown Error)
1 - 用户不存在 (User Not Found)
2 - 密码错误 (Password Incorrect)
3 - 用户已禁用 (User Disabled)
4 - 用户已锁定 (User Locked)
5 - 用户不激活 (User Not Active)
6 - 账户异常 (Account Abnormal)
7 - 其他错误 (Other Error)
```

## 服务方法速查

### 记录方法

```python
# 记录登录（通用）
LoginLogService.record_login(
    username="admin",
    status=1,
    login_ip="192.168.1.1",
    user_id=None,
    failure_reason=None,
    failure_message=None,
    ip_location=None,
    user_agent=None,
    browser_type=None,
    os_type=None,
    device_type=None,
    session_id=None,
    remark=None,
)

# 快速记录成功
LoginLogService.record_success_login(username, user_id, login_ip)

# 快速记录失败
LoginLogService.record_failed_login(username, login_ip, failure_reason)
```

### 查询方法

```python
# 登录次数
LoginLogService.get_user_login_count(user_id=None, username=None, days=30)

# 失败次数
LoginLogService.get_failed_login_count(user_id=None, username=None, days=30)

# 最后一次登录
LoginLogService.get_last_login(user_id=None, username=None)

# 登录过的IP
LoginLogService.get_login_ips(user_id=None, username=None, days=30)
```

### 统计方法

```python
# 登录统计
LoginLogService.get_login_stats(days=30)

# IP统计
LoginLogService.get_ip_stats(days=30, limit=10)

# 设备统计
LoginLogService.get_device_stats(days=30)

# 用户统计
LoginLogService.get_user_stats(days=30, limit=10)

# 每日统计
LoginLogService.get_daily_stats(days=30)
```

### 安全方法

```python
# 可疑登录
LoginLogService.get_suspicious_logins(max_failed_attempts=5, hours=1)

# 检查是否应锁定
LoginLogService.check_user_locked(username, failed_threshold=5, hours=1)

# 清理旧日志
LoginLogService.clean_old_logs(days=90)
```

## 常见场景

### 场景1：在登录接口中集成

```python
@router.post("/login")
def login(request, data: LoginIn):
    try:
        user = User.objects.get(username=data.username)
        if not user.check_password(data.password):
            # 记录失败
            LoginLogService.record_failed_login(
                username=data.username,
                login_ip=get_client_ip(request),
                failure_reason=2,  # 密码错误
            )
            raise HttpError(401, "用户名或密码错误")
        
        # 记录成功
        LoginLogService.record_success_login(
            username=data.username,
            user_id=str(user.id),
            login_ip=get_client_ip(request),
        )
        return {"token": token}
    except User.DoesNotExist:
        LoginLogService.record_failed_login(
            username=data.username,
            login_ip=get_client_ip(request),
            failure_reason=1,  # 用户不存在
        )
        raise HttpError(401, "用户不存在")
```

### 场景2：获取用户最近登录

```python
from django.http import JsonResponse

def user_login_history(request, user_id):
    # 最后一次登录
    last_login = LoginLogService.get_last_login(user_id=user_id)
    
    # 登录次数
    login_count = LoginLogService.get_user_login_count(user_id=user_id, days=30)
    
    # 登录IP列表
    ips = LoginLogService.get_login_ips(user_id=user_id, days=30)
    
    return JsonResponse({
        'last_login': last_login.sys_create_datetime if last_login else None,
        'login_count': login_count,
        'login_ips': ips,
    })
```

### 场景3：检测异常登录

```python
def check_suspicious_login():
    # 检查最近1小时内失败5次以上的登录
    suspicious = LoginLogService.get_suspicious_logins(
        max_failed_attempts=5,
        hours=1,
    )
    
    for record in suspicious:
        username = record['username']
        ip = record['login_ip']
        count = record['count']
        
        # 发送告警
        send_alert(f"警告：用户 {username} 从 {ip} 短时间内失败登录 {count} 次")
```

### 场景4：生成安全报告

```python
def generate_security_report(days=30):
    stats = LoginLogService.get_login_stats(days=days)
    daily = LoginLogService.get_daily_stats(days=days)
    suspicious = LoginLogService.get_suspicious_logins()
    
    report = {
        'overview': stats,
        'daily': daily,
        'suspicious_count': len(suspicious),
        'suspicious_records': suspicious,
    }
    return report
```

## 性能提示

1. **查询优化**：API已包含分页和过滤，大数据量时使用分页
2. **定期清理**：使用 `clean_old_logs()` 定期删除旧数据
3. **索引优化**：模型已添加必要的索引，无需额外配置
4. **批量操作**：批量删除使用 `batch/delete` 端点

## 部署检查清单

- [ ] 创建并应用数据库迁移
- [ ] 在认证系统中集成日志记录
- [ ] 配置日志查询权限
- [ ] 设置定时清理任务（可选）
- [ ] 测试登录日志记录功能
- [ ] 配置前端显示登录日志（可选）
- [ ] 备份现有登录数据（如有）

## 故障排除

| 问题     | 解决方案                             |
|--------|----------------------------------|
| 表不存在   | 运行迁移: `python manage.py migrate` |
| 日志记录失败 | 检查 `get_client_ip()` 的IP格式       |
| 查询很慢   | 添加日期范围过滤，或执行 `clean_old_logs()`  |
| IP信息缺失 | 集成IP地理定位服务                       |

## 相关配置

### Django Settings

```python
# settings.py

# 登录日志配置（可选）
LOGIN_LOG_RETENTION_DAYS = 90  # 日志保留天数
LOGIN_LOG_SUSPICIOUS_THRESHOLD = 5  # 失败次数阈值
LOGIN_LOG_SUSPICIOUS_HOURS = 1  # 时间范围（小时）
```

### API 权限

```python
# 推荐权限配置
PERMISSIONS = {
    'login_log:view': '查看登录日志',
    'login_log:export': '导出登录日志',
    'login_log:delete': '删除登录日志',
}
```

## 更新日志

- **v1.0.0** (2024)
    - 初始版本
    - 完整的CRUD操作
    - 统计分析功能
    - 安全检测功能

## 支持

如有问题，请参考：

- 完整文档: `README.md`
- 迁移指南: `MIGRATION_GUIDE.md`
- 代码注释: 查看相应源文件

