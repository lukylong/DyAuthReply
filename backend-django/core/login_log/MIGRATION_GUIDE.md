# 登录日志模块 - 迁移指南

## 快速开始

### 1. 创建数据库迁移

```bash
# 进入项目目录
cd /Users/zcl/Project/fuadmin-/backend-v5

# 创建迁移文件
python manage.py makemigrations core

# 应用迁移到数据库
python manage.py migrate
```

### 2. 验证表是否创建成功

```bash
# 进入数据库，检查 core_login_log 表是否存在
python manage.py dbshell

# 在数据库中执行
SHOW TABLES LIKE 'core_login_log';
DESC core_login_log;
```

## 集成到认证系统

### 第一步：导入服务

在 `backend-v5/core/auth/auth_api.py` 中导入登录日志服务：

```python
from core.login_log.login_log_service import LoginLogService
from core.login_log.login_log_model import LoginLog
```

### 第二步：修改登录接口

在登录接口中添加日志记录：

```python
@router.post("/login", response=dict, summary="用户登录")
def login(request, data: LoginInSchema):
    """
    用户登录
    """
    username = data.username
    password = data.password
    
    # 获取客户端IP
    login_ip = get_client_ip(request)
    user_agent = request.META.get('HTTP_USER_AGENT', '')
    
    try:
        # 获取用户
        user = User.objects.get(username=username)
        
        # 检查用户状态
        if user.user_status == 0:  # 禁用
            # 记录失败登录
            LoginLogService.record_failed_login(
                username=username,
                login_ip=login_ip,
                failure_reason=3,  # 用户已禁用
                failure_message="用户已禁用",
                user_agent=user_agent,
            )
            raise HttpError(401, "用户已禁用，请联系管理员")
        
        if user.user_status == 2:  # 锁定
            # 记录失败登录
            LoginLogService.record_failed_login(
                username=username,
                login_ip=login_ip,
                failure_reason=4,  # 用户已锁定
                failure_message="用户已锁定",
                user_agent=user_agent,
            )
            raise HttpError(401, "用户已锁定，请联系管理员")
        
        # 验证密码
        if not user.check_password(password):
            # 记录失败登录
            LoginLogService.record_failed_login(
                username=username,
                login_ip=login_ip,
                failure_reason=2,  # 密码错误
                failure_message="密码验证失败",
                user_agent=user_agent,
            )
            
            # 检查是否应该锁定账户（防止暴力破解）
            if LoginLogService.check_user_locked(
                username=username,
                failed_threshold=5,
                hours=1
            ):
                user.user_status = 2  # 锁定用户
                user.save()
                
                # 记录锁定事件
                LoginLogService.record_failed_login(
                    username=username,
                    login_ip=login_ip,
                    failure_reason=4,  # 用户已锁定
                    failure_message="登录失败次数过多，账户已锁定",
                    user_agent=user_agent,
                )
            
            raise HttpError(401, "用户名或密码错误")
        
        # 生成token
        token = generate_token(user)
        session_id = generate_session_id()
        
        # 记录成功登录
        LoginLogService.record_success_login(
            username=username,
            user_id=str(user.id),
            login_ip=login_ip,
            user_agent=user_agent,
            session_id=session_id,
        )
        
        # 更新用户最后登录时间
        user.last_login = timezone.now()
        user.last_login_ip = login_ip
        user.save(update_fields=['last_login', 'last_login_ip'])
        
        return response_success(
            "登录成功",
            data={
                "token": token,
                "session_id": session_id,
                "user": {
                    "id": str(user.id),
                    "username": user.username,
                    "name": user.name,
                }
            }
        )
        
    except User.DoesNotExist:
        # 记录失败登录
        LoginLogService.record_failed_login(
            username=username,
            login_ip=login_ip,
            failure_reason=1,  # 用户不存在
            failure_message="用户不存在",
            user_agent=user_agent,
        )
        raise HttpError(401, "用户不存在")
    
    except HttpError:
        raise
    
    except Exception as e:
        # 记录未知错误
        LoginLogService.record_failed_login(
            username=username,
            login_ip=login_ip,
            failure_reason=0,  # 未知错误
            failure_message=str(e),
            user_agent=user_agent,
        )
        raise HttpError(500, "登录失败，请稍后重试")
```

### 第三步：添加注销接口的日志

如果需要记录用户注销：

```python
@router.post("/logout", summary="用户注销")
def logout(request):
    """用户注销"""
    current_user = request.auth
    
    if current_user:
        # 可选：记录注销事件
        # 这可以通过更新对应登录日志的 duration 字段实现
        pass
    
    return response_success("注销成功")
```

## 辅助函数

在认证模块中添加以下辅助函数：

```python
# backend-v5/core/auth/auth_utils.py

def get_client_ip(request):
    """
    获取客户端真实IP地址
    
    考虑代理和反向代理的情况
    """
    # 首先检查 X-Forwarded-For（代理）
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        # 取第一个IP（最原始的客户端IP）
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        # 否则使用 REMOTE_ADDR
        ip = request.META.get('REMOTE_ADDR', '0.0.0.0')
    
    return ip


def generate_session_id():
    """生成唯一的会话ID"""
    import uuid
    return str(uuid.uuid4())


def extract_device_info(user_agent):
    """
    从User-Agent提取浏览器、操作系统、设备类型
    
    可选：需要安装 user-agents 库
    pip install user-agents
    """
    try:
        from user_agents import parse
        ua = parse(user_agent)
        
        browser_type = ua.browser.family if ua.browser else 'Unknown'
        os_type = ua.os.family if ua.os else 'Unknown'
        device_type = 'mobile' if ua.is_mobile else ('tablet' if ua.is_tablet else 'desktop')
        
        return browser_type, os_type, device_type
    
    except Exception:
        return None, None, None
```

## 定时任务配置

建议在定时任务中定期清理旧日志：

```python
# backend-v5/core/scheduler/scheduler_config.py

# 或在 Celery 配置中添加

@periodic_task(
    run_every=crontab(hour=2, minute=0),  # 每天凌晨2点执行
    name="clean-old-login-logs"
)
def clean_old_login_logs():
    """
    定期清理旧登录日志
    保留最近90天的数据
    """
    from core.login_log.login_log_service import LoginLogService
    deleted_count = LoginLogService.clean_old_logs(days=90)
    print(f"成功清理 {deleted_count} 条旧登录日志")
```

## 权限配置

建议为登录日志查询添加权限检查：

```python
from core.permission.permission_model import Permission

# 在管理后台添加以下权限
# 权限编码: login_log:view
# 权限名称: 查看登录日志
# 权限描述: 允许用户查看系统登录日志

# 权限编码: login_log:export
# 权限名称: 导出登录日志
# 权限描述: 允许用户导出登录日志

# 权限编码: login_log:delete
# 权限名称: 删除登录日志
# 权限描述: 允许用户删除登录日志
```

在API中添加权限检查：

```python
from common.fu_permission import check_permission

@router.get("/login-log", response=List[LoginLogSchemaOut])
@paginate(MyPagination)
def list_login_logs(request, filters: LoginLogFilters = Query(...)):
    """获取登录日志列表"""
    # 检查权限
    if not check_permission(request.auth, 'login_log:view'):
        raise HttpError(403, "您没有查看登录日志的权限")
    
    query_set = retrieve(request, LoginLog, filters)
    return query_set
```

## 前端集成

前端可以通过API获取登录日志数据：

```javascript
// 获取登录日志列表
async function getLoginLogs(page = 1, limit = 20) {
    const response = await fetch(
        `/api/login-log?page=${page}&limit=${limit}&status=1`,
        { headers: { 'Authorization': `Bearer ${token}` } }
    );
    return response.json();
}

// 获取登录统计
async function getLoginStats(days = 30) {
    const response = await fetch(
        `/api/login-log/stats/overview?days=${days}`,
        { headers: { 'Authorization': `Bearer ${token}` } }
    );
    return response.json();
}

// 获取可疑登录
async function getSuspiciousLogins() {
    const response = await fetch(
        `/api/login-log/suspicious?failed_threshold=5&hours=1`,
        { headers: { 'Authorization': `Bearer ${token}` } }
    );
    return response.json();
}
```

## 测试

创建测试文件 `backend-v5/core/login_log/test_login_log.py`：

```python
from django.test import TestCase
from core.login_log.login_log_service import LoginLogService
from core.login_log.login_log_model import LoginLog


class LoginLogServiceTestCase(TestCase):
    
    def test_record_success_login(self):
        """测试记录成功登录"""
        log = LoginLogService.record_success_login(
            username="testuser",
            user_id="test-uuid",
            login_ip="192.168.1.1",
        )
        self.assertEqual(log.status, 1)
        self.assertEqual(log.username, "testuser")
        self.assertEqual(log.login_ip, "192.168.1.1")
    
    def test_record_failed_login(self):
        """测试记录失败登录"""
        log = LoginLogService.record_failed_login(
            username="testuser",
            login_ip="192.168.1.1",
            failure_reason=2,  # 密码错误
        )
        self.assertEqual(log.status, 0)
        self.assertEqual(log.failure_reason, 2)
    
    def test_get_user_login_count(self):
        """测试获取用户登录次数"""
        # 创建测试数据
        LoginLogService.record_success_login("testuser", login_ip="192.168.1.1")
        LoginLogService.record_success_login("testuser", login_ip="192.168.1.1")
        
        count = LoginLogService.get_user_login_count(username="testuser")
        self.assertEqual(count, 2)
    
    def test_suspicious_logins(self):
        """测试检测可疑登录"""
        # 创建多个失败登录记录
        for i in range(6):
            LoginLogService.record_failed_login(
                username="testuser",
                login_ip="192.168.1.1",
                failure_reason=2,
            )
        
        suspicious = LoginLogService.get_suspicious_logins(
            max_failed_attempts=5,
            hours=1,
        )
        self.assertGreater(len(suspicious), 0)


if __name__ == '__main__':
    import django
    django.setup()
    
    # 运行测试
    # python manage.py test core.login_log.test_login_log
```

## 故障排除

### 迁移失败

如果迁移失败，检查以下几点：

1. Django版本兼容性
2. 数据库连接是否正常
3. 模型是否有语法错误

```bash
# 检查迁移文件
python manage.py showmigrations core

# 显示迁移详情
python manage.py sqlmigrate core 0013_loginlog
```

### 表创建失败

如果表未正确创建，可以手动创建：

```sql
CREATE TABLE core_login_log (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36),
    username VARCHAR(150) NOT NULL,
    status INT NOT NULL,
    failure_reason INT,
    failure_message VARCHAR(255),
    login_ip VARCHAR(15) NOT NULL,
    ip_location VARCHAR(100),
    user_agent TEXT,
    browser_type VARCHAR(50),
    os_type VARCHAR(50),
    device_type VARCHAR(20),
    duration INT,
    session_id VARCHAR(128) UNIQUE,
    remark VARCHAR(255),
    sys_creator_id VARCHAR(36),
    sys_create_datetime DATETIME NOT NULL,
    sys_modifier_id VARCHAR(36),
    sys_update_datetime DATETIME NOT NULL,
    is_deleted BOOLEAN DEFAULT FALSE,
    sort INT,
    KEY idx_user_datetime (user_id, sys_create_datetime),
    KEY idx_username_status (username, status),
    KEY idx_status_datetime (status, sys_create_datetime),
    KEY idx_ip_datetime (login_ip, sys_create_datetime)
);
```

## 总结

登录日志模块的集成包括：

1. ✅ 创建数据库迁移
2. ✅ 在认证系统中集成日志记录
3. ✅ 配置权限检查
4. ✅ 设置定时清理任务
5. ✅ 前端展示登录日志和统计

完成这些步骤后，系统将开始记录所有用户的登录活动。

