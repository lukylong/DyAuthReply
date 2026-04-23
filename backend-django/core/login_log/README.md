# 登录日志模块 - Login Log Module

## 概述

登录日志模块是一个完整的用户登录操作记录系统。它记录所有用户登录操作（成功和失败），包括登录时间、IP地址、设备信息等详细数据，支持灵活的查询和统计分析功能。

## 功能特性

### 1. 完整的登录记录

- **基础信息**：用户名、用户ID、登录状态
- **网络信息**：登录IP、IP属地（地理位置）
- **设备信息**：浏览器类型、操作系统、设备类型
- **时间信息**：登录时间、会话时长
- **错误信息**：失败原因、失败详细信息

### 2. 失败原因分类

```
0 - 未知错误
1 - 用户不存在
2 - 密码错误
3 - 用户已禁用
4 - 用户已锁定
5 - 用户不激活
6 - 账户异常
7 - 其他错误
```

### 3. 灵活的查询功能

- 按用户名、用户ID查询
- 按登录状态（成功/失败）查询
- 按IP地址查询
- 按设备类型、浏览器、操作系统查询
- 按时间范围查询
- 分页支持

### 4. 强大的统计分析

- **概览统计**：总数、成功率、用户数、IP数
- **IP统计**：登录最频繁的IP TOP N
- **设备统计**：各类设备的登录情况
- **用户统计**：登录最频繁的用户 TOP N
- **每日统计**：逐日登录趋势分析
- **可疑检测**：短时间内失败次数过多的登录尝试

### 5. 安全功能

- 记录详细的失败信息，便于安全审计
- 检测异常登录模式（短时间内失败次数过多）
- 支持自动账户锁定建议
- 可疑登录告警

## 数据库模型

```python
class LoginLog(RootModel):
    # 用户信息
    user_id          - 用户ID
    username         - 用户名
    
    # 登录结果
    status           - 登录状态（0-失败，1-成功）
    failure_reason   - 失败原因编码
    failure_message  - 失败详细信息
    
    # 网络信息
    login_ip         - 登录IP地址
    ip_location      - IP属地（地理位置）
    
    # 设备信息
    user_agent       - 用户代理字符串
    browser_type     - 浏览器类型
    os_type          - 操作系统类型
    device_type      - 设备类型（desktop/mobile/tablet/other）
    
    # 会话信息
    session_id       - 会话ID
    duration         - 会话时长（秒）
    
    # 审计字段（继承自RootModel）
    sys_create_datetime  - 创建时间
    sys_creator          - 创建人
```

## API 接口

### 1. 获取登录日志列表

```
GET /api/login-log?username=xxx&status=1&page=1&limit=20
```

**支持的过滤参数：**

- `username` - 用户名（模糊搜索）
- `user_id` - 用户ID
- `status` - 登录状态
- `failure_reason` - 失败原因
- `login_ip` - 登录IP（模糊搜索）
- `device_type` - 设备类型
- `browser_type` - 浏览器类型
- `os_type` - 操作系统
- `start_datetime` - 开始时间
- `end_datetime` - 结束时间

### 2. 获取单条登录日志详情

```
GET /api/login-log/{log_id}
```

### 3. 删除登录日志

```
DELETE /api/login-log/{log_id}
DELETE /api/login-log/batch/delete?ids=id1&ids=id2
```

### 4. 记录登录日志

```
POST /api/login-log/record
{
    "username": "admin",
    "user_id": "uuid",
    "status": 1,
    "login_ip": "192.168.1.1",
    "user_agent": "Mozilla/5.0...",
    "browser_type": "Chrome",
    "os_type": "macOS",
    "device_type": "desktop",
    "session_id": "session-id"
}
```

### 5. 获取登录统计概览

```
GET /api/login-log/stats/overview?days=30
```

**返回数据：**

```json
{
    "total_logins": 1000,
    "success_logins": 950,
    "failed_logins": 50,
    "success_rate": 95.0,
    "unique_users": 25,
    "unique_ips": 10
}
```

### 6. 获取IP登录统计 TOP N

```
GET /api/login-log/stats/ip?days=30&limit=10
```

### 7. 获取设备登录统计

```
GET /api/login-log/stats/device?days=30
```

### 8. 获取用户登录统计 TOP N

```
GET /api/login-log/stats/user?days=30&limit=10
```

### 9. 获取每日登录统计

```
GET /api/login-log/stats/daily?days=30
```

### 10. 获取用户的登录日志

```
GET /api/login-log/user/{user_id}?days=30&page=1&limit=20
```

### 11. 获取用户登录次数

```
GET /api/login-log/user/{user_id}/count?days=30
```

### 12. 获取用户最后一次登录

```
GET /api/login-log/user/{user_id}/last
```

### 13. 获取用户登录过的IP地址

```
GET /api/login-log/user/{user_id}/ips?days=30
```

### 14. 获取可疑登录记录

```
GET /api/login-log/suspicious?failed_threshold=5&hours=1
```

### 15. 清理旧登录日志

```
POST /api/login-log/clean?days=90
```

### 16. 根据用户名获取日志

```
GET /api/login-log/username/{username}?days=30&page=1&limit=20
```

### 17. 根据IP获取日志

```
GET /api/login-log/ip/{login_ip}?days=30&page=1&limit=20
```

### 18. 获取用户登录失败次数

```
GET /api/login-log/failed-attempts/{username}?hours=1
```

## 服务层接口

### LoginLogService

#### 基础操作

```python
from core.login_log.login_log_service import LoginLogService

# 记录登录
LoginLogService.record_login(
    username="admin",
    status=1,
    login_ip="192.168.1.1",
    user_id="uuid",
    ...
)

# 快速记录成功登录
LoginLogService.record_success_login(
    username="admin",
    user_id="uuid",
    login_ip="192.168.1.1",
    ...
)

# 快速记录失败登录
LoginLogService.record_failed_login(
    username="admin",
    login_ip="192.168.1.1",
    failure_reason=2,  # 密码错误
    failure_message="Password mismatch",
    ...
)
```

#### 统计查询

```python
# 获取用户登录次数
count = LoginLogService.get_user_login_count(
    user_id="uuid",
    days=30
)

# 获取用户失败登录次数
failed_count = LoginLogService.get_failed_login_count(
    username="admin",
    days=7
)

# 获取用户最后一次登录
last_login = LoginLogService.get_last_login(username="admin")

# 获取用户登录过的IP列表
ips = LoginLogService.get_login_ips(
    user_id="uuid",
    days=30
)

# 获取可疑登录
suspicious = LoginLogService.get_suspicious_logins(
    max_failed_attempts=5,
    hours=1
)

# 获取登录统计
stats = LoginLogService.get_login_stats(days=30)

# 获取IP统计
ip_stats = LoginLogService.get_ip_stats(days=30, limit=10)

# 获取设备统计
device_stats = LoginLogService.get_device_stats(days=30)

# 获取用户统计
user_stats = LoginLogService.get_user_stats(days=30, limit=10)

# 获取每日统计
daily_stats = LoginLogService.get_daily_stats(days=30)
```

#### 安全相关

```python
# 清理旧登录日志
deleted_count = LoginLogService.clean_old_logs(days=90)

# 检查用户是否应该被锁定
should_lock = LoginLogService.check_user_locked(
    username="admin",
    failed_threshold=5,
    hours=1
)
```

## 集成指南

### 1. 在认证系统中集成

在用户登录接口中集成登录日志记录：

```python
from core.login_log.login_log_service import LoginLogService
from core.login_log.login_log_model import LoginLog

@router.post("/login", response=dict)
def login(request, data: LoginIn):
    """用户登录"""
    username = data.username
    password = data.password
    login_ip = request.client[0]  # 获取客户端IP
    
    try:
        user = User.objects.get(username=username)
        
        # 检查账户状态
        if user.user_status == 0:  # 禁用
            LoginLogService.record_failed_login(
                username=username,
                login_ip=login_ip,
                failure_reason=LoginLog.FAILURE_REASON_CHOICES[2][0],  # 用户已禁用
                failure_message="用户已禁用",
                user_agent=request.META.get('HTTP_USER_AGENT'),
            )
            raise HttpError(401, "用户已禁用")
        
        # 检查密码
        if not user.check_password(password):
            LoginLogService.record_failed_login(
                username=username,
                login_ip=login_ip,
                failure_reason=LoginLog.FAILURE_REASON_CHOICES[1][0],  # 密码错误
                failure_message="密码错误",
                user_agent=request.META.get('HTTP_USER_AGENT'),
            )
            
            # 检查是否应该锁定账户
            if LoginLogService.check_user_locked(username, failed_threshold=5, hours=1):
                user.user_status = 2  # 锁定
                user.save()
            
            raise HttpError(401, "密码错误")
        
        # 登录成功
        session_id = generate_session_id()
        
        LoginLogService.record_success_login(
            username=username,
            user_id=str(user.id),
            login_ip=login_ip,
            user_agent=request.META.get('HTTP_USER_AGENT'),
            session_id=session_id,
        )
        
        return response_success("登录成功", data={"token": token, "session_id": session_id})
        
    except User.DoesNotExist:
        LoginLogService.record_failed_login(
            username=username,
            login_ip=login_ip,
            failure_reason=LoginLog.FAILURE_REASON_CHOICES[0][0],  # 用户不存在
            failure_message="用户不存在",
            user_agent=request.META.get('HTTP_USER_AGENT'),
        )
        raise HttpError(401, "用户不存在")
```

### 2. 在中间件中自动提取设备信息

创建一个中间件来自动提取用户代理和设备信息：

```python
from ua_parser.user_agent_parser import Parse

def extract_device_info(user_agent: str):
    """从User-Agent字符串提取设备信息"""
    if not user_agent:
        return None, None, None
    
    parsed = Parse(user_agent)
    browser = parsed.get('user_agent', {}).get('family', 'Unknown')
    os = parsed.get('os', {}).get('family', 'Unknown')
    device = parsed.get('device', {}).get('family', 'Other')
    
    return browser, os, device
```

### 3. 获取客户端IP地址

```python
def get_client_ip(request):
    """获取客户端真实IP地址"""
    # 优先取X-Forwarded-For（代理情况）
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip
```

## 使用示例

### 示例1：查询某用户最近7天的登录情况

```python
from core.login_log.login_log_service import LoginLogService

# 获取登录次数
login_count = LoginLogService.get_user_login_count(
    username="admin",
    days=7
)

# 获取失败次数
failed_count = LoginLogService.get_failed_login_count(
    username="admin",
    days=7
)

# 获取最后一次登录
last_login = LoginLogService.get_last_login(username="admin")

# 获取登录过的IP列表
ips = LoginLogService.get_login_ips(username="admin", days=7)

print(f"用户 admin 最近7天：")
print(f"  登录成功: {login_count - failed_count} 次")
print(f"  登录失败: {failed_count} 次")
print(f"  最后登录: {last_login.sys_create_datetime}")
print(f"  登录IP: {ips}")
```

### 示例2：检测异常登录

```python
# 获取可疑登录（1小时内失败超过5次）
suspicious_logins = LoginLogService.get_suspicious_logins(
    max_failed_attempts=5,
    hours=1
)

for record in suspicious_logins:
    username = record['username']
    ip = record['login_ip']
    failed_count = record['count']
    
    print(f"警告：用户 {username} 从 {ip} 在1小时内失败登录 {failed_count} 次")
    
    # 检查是否应该锁定
    should_lock = LoginLogService.check_user_locked(username)
    if should_lock:
        print(f"  建议：锁定用户 {username}")
```

### 示例3：生成安全报告

```python
from datetime import date

# 获取每日统计
daily_stats = LoginLogService.get_daily_stats(days=30)

print(f"登录安全报告 - {date.today()}")
print("=" * 50)

for stat in daily_stats:
    date_str = stat['date']
    total = stat['total_logins']
    success = stat['success_logins']
    failed = stat['failed_logins']
    users = stat['unique_users']
    success_rate = (success / total * 100) if total > 0 else 0
    
    print(f"{date_str}:")
    print(f"  总登录数: {total}")
    print(f"  成功: {success}")
    print(f"  失败: {failed}")
    print(f"  成功率: {success_rate:.1f}%")
    print(f"  用户数: {users}")
```

## 数据库迁移

模块创建后，需要创建数据库迁移：

```bash
# 创建迁移文件
python manage.py makemigrations core

# 应用迁移
python manage.py migrate
```

## 性能优化建议

1. **定期清理旧日志**：使用 `clean_old_logs()` 定期清理旧数据
   ```python
   # 在定时任务中执行
   LoginLogService.clean_old_logs(days=90)
   ```

2. **添加数据库索引**：模型已包含以下索引
    - `(user_id, sys_create_datetime)`
    - `(username, status)`
    - `(status, sys_create_datetime)`
    - `(login_ip, sys_create_datetime)`

3. **批量查询优化**：使用服务层方法而不是直接查询
    - 已优化的查询使用了合适的过滤和聚合

## 安全建议

1. **限制日志查看权限**：仅管理员可查看完整登录日志
2. **隐藏敏感信息**：在API返回中隐藏某些敏感字段
3. **审计操作**：记录谁访问了登录日志
4. **日志加密**：考虑对敏感信息进行加密存储
5. **定期备份**：定期备份登录日志数据

## 常见问题

### Q: 如何与现有认证系统集成？

A: 在认证模块的登录接口中调用 `LoginLogService.record_success_login()` 或 `record_failed_login()` 方法即可。

### Q: 日志会无限增长吗？

A: 不会。使用 `clean_old_logs()` 方法可以定期清理旧数据，建议保留最近90天的数据。

### Q: 如何获取IP的地理位置信息？

A: 集成第三方IP地理定位服务（如 IP138、淘宝IP库等）在 `record_login()` 时填充 `ip_location` 字段。

### Q: 是否支持自定义失败原因？

A: 可以在 `LoginLog` 模型的 `FAILURE_REASON_CHOICES` 中添加更多失败原因。

## 许可证

MIT License

