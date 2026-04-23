# 数据库管理系统后端实现完成

## 📋 实现概述

已完成一个可扩展的数据库管理系统后端，支持PostgreSQL、MySQL、SQL Server等多种数据库。采用**策略模式 + 工厂模式**
设计，便于扩展新的数据库类型。

## 🏗️ 架构设计

### 1. 设计模式

#### **策略模式（Strategy Pattern）**

- 定义抽象基类 `BaseDatabaseHandler`
- 每种数据库实现自己的具体策略类
- 统一接口，不同实现

#### **工厂模式（Factory Pattern）**

- `DatabaseManagerService` 作为工厂类
- 根据数据库类型自动创建对应的处理器
- 屏蔽创建细节

### 2. 目录结构

```
backend-v5/core/database_manager/
├── __init__.py
├── base_database_handler.py      # 抽象基类
├── postgresql_handler.py          # PostgreSQL实现
├── mysql_handler.py               # MySQL实现
├── sqlserver_handler.py           # SQL Server实现
├── database_manager_schema.py    # Pydantic Schema
├── database_manager_service.py   # 服务层（工厂）
└── database_manager_api.py       # API路由
```

## 🔧 核心组件

### 1. 抽象基类 (`base_database_handler.py`)

**职责**：定义所有数据库操作的统一接口

**核心方法**：

```python
class BaseDatabaseHandler(ABC):
    # 数据库管理
    @abstractmethod
    def get_databases() -> List[Dict]
    @abstractmethod
    def create_database(name, **kwargs) -> bool
    @abstractmethod
    def drop_database(name) -> bool
    
    # 表管理
    @abstractmethod
    def get_tables(schema_name) -> List[Dict]
    @abstractmethod
    def get_table_structure(table_name, schema_name) -> Dict
    @abstractmethod
    def get_table_columns(table_name, schema_name) -> List[Dict]
    @abstractmethod
    def get_table_indexes(table_name, schema_name) -> List[Dict]
    @abstractmethod
    def get_table_constraints(table_name, schema_name) -> List[Dict]
    
    # 数据操作（通用实现）
    def query_data(table_name, page, page_size, where, order_by) -> Dict
    def execute_sql(sql, is_query) -> Dict
    def insert_data(table_name, data, schema_name) -> Dict
    def update_data(table_name, data, where, schema_name) -> Dict
    def delete_data(table_name, where, schema_name) -> Dict
```

**特点**：

- 抽象方法：需要子类实现（数据库特定）
- 通用方法：基类提供默认实现（跨数据库通用）

### 2. PostgreSQL处理器 (`postgresql_handler.py`)

**特性**：

- ✅ 支持多数据库管理
- ✅ 支持Schema概念
- ✅ 使用 `pg_catalog` 和 `information_schema` 查询元数据
- ✅ 支持 `pg_size_pretty` 格式化大小
- ✅ 获取表的行数、大小、索引等详细信息
- ✅ 支持复杂的索引和约束查询

**关键SQL示例**：

```sql
-- 获取数据库列表
SELECT 
    d.datname as name,
    pg_catalog.pg_get_userbyid(d.datdba) as owner,
    pg_catalog.pg_size_pretty(pg_catalog.pg_database_size(d.datname)) as size
FROM pg_catalog.pg_database d
WHERE d.datistemplate = false;

-- 获取表列表
SELECT 
    schemaname as schema_name,
    tablename as table_name,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as total_size
FROM pg_catalog.pg_tables
WHERE schemaname = 'public';
```

### 3. MySQL处理器 (`mysql_handler.py`)

**特性**：

- ✅ 使用 `information_schema` 查询元数据
- ✅ 支持字符集和排序规则
- ✅ 计算表大小（data_length + index_length）
- ✅ 获取字段、索引、约束信息
- ✅ 自动格式化大小（GB/MB/KB）

**关键SQL示例**：

```sql
-- 获取数据库列表
SELECT 
    SCHEMA_NAME as name,
    DEFAULT_CHARACTER_SET_NAME as encoding,
    DEFAULT_COLLATION_NAME as collation
FROM information_schema.SCHEMATA
WHERE SCHEMA_NAME NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys');

-- 获取表列表
SELECT 
    TABLE_NAME as table_name,
    TABLE_ROWS as row_count,
    (DATA_LENGTH + INDEX_LENGTH) as total_size_bytes
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'your_database';
```

### 4. SQL Server处理器 (`sqlserver_handler.py`)

**特性**：

- ✅ 使用 `sys` 系统视图查询元数据
- ✅ 支持扩展属性（MS_Description）
- ✅ 计算表大小（allocation_units）
- ✅ 获取索引和约束信息
- ✅ 支持单用户模式删除数据库

**关键SQL示例**：

```sql
-- 获取数据库列表
SELECT 
    name,
    SUSER_SNAME(owner_sid) as owner,
    collation_name as collation
FROM sys.databases
WHERE name NOT IN ('master', 'tempdb', 'model', 'msdb');

-- 获取表列表
SELECT 
    SCHEMA_NAME(t.schema_id) as schema_name,
    t.name as table_name,
    p.rows as row_count,
    SUM(a.total_pages) * 8 * 1024 as total_size_bytes
FROM sys.tables t
INNER JOIN sys.indexes i ON t.object_id = i.object_id
INNER JOIN sys.partitions p ON i.object_id = p.object_id;
```

### 5. 服务层 (`database_manager_service.py`)

**工厂方法**：

```python
class DatabaseManagerService:
    @staticmethod
    def get_handler(db_name: str) -> BaseDatabaseHandler:
        """根据数据库类型返回对应的处理器"""
        connection = connections[db_name]
        engine = connection.settings_dict.get('ENGINE', '')
        
        if 'postgresql' in engine:
            return PostgreSQLHandler(db_name)
        elif 'mysql' in engine:
            return MySQLHandler(db_name)
        elif 'sql_server' in engine or 'mssql' in engine:
            return SQLServerHandler(db_name)
        else:
            raise ValueError(f"Unsupported database type: {engine}")
```

**配置管理**：

```python
@staticmethod
def get_database_configs():
    """获取所有配置的数据库信息"""
    # 从 Django settings.DATABASES 读取配置
    # 返回统一格式的配置列表
```

## 📡 API接口

### 路由前缀

```
/api/core/database_manager/
```

### 接口列表

#### 1. 数据库配置

| 端点                | 方法   | 功能        |
|-------------------|------|-----------|
| `/configs`        | GET  | 获取所有数据库配置 |
| `/{db_name}/test` | POST | 测试数据库连接   |

#### 2. 数据库管理

| 端点                            | 方法     | 功能      |
|-------------------------------|--------|---------|
| `/{db_name}/databases`        | GET    | 获取数据库列表 |
| `/{db_name}/databases`        | POST   | 创建数据库   |
| `/{db_name}/databases/{name}` | DELETE | 删除数据库   |

#### 3. Schema管理（PostgreSQL）

| 端点                   | 方法  | 功能         |
|----------------------|-----|------------|
| `/{db_name}/schemas` | GET | 获取Schema列表 |

#### 4. 表管理

| 端点                                     | 方法  | 功能    |
|----------------------------------------|-----|-------|
| `/{db_name}/tables`                    | GET | 获取表列表 |
| `/{db_name}/tables/{name}/structure`   | GET | 获取表结构 |
| `/{db_name}/tables/{name}/columns`     | GET | 获取表字段 |
| `/{db_name}/tables/{name}/indexes`     | GET | 获取表索引 |
| `/{db_name}/tables/{name}/constraints` | GET | 获取表约束 |

#### 5. 数据查询

| 端点                   | 方法   | 功能        |
|----------------------|------|-----------|
| `/{db_name}/query`   | POST | 查询表数据（分页） |
| `/{db_name}/execute` | POST | 执行SQL     |

#### 6. 数据操作

| 端点                       | 方法   | 功能   |
|--------------------------|------|------|
| `/{db_name}/data/insert` | POST | 插入数据 |
| `/{db_name}/data/update` | POST | 更新数据 |
| `/{db_name}/data/delete` | POST | 删除数据 |

## 🎯 核心特性

### 1. 多数据库支持

- ✅ PostgreSQL - 完整实现
- ✅ MySQL - 完整实现
- ✅ SQL Server - 完整实现
- 🔄 SQLite - 预留接口
- 🔄 Oracle - 预留接口

### 2. 统一接口

- 所有数据库使用相同的API接口
- 前端无需关心数据库类型
- 自动适配不同数据库的SQL语法

### 3. 智能默认值

```python
# 根据数据库类型自动设置默认schema
if handler.db_type == 'postgresql':
    schema_name = 'public'
elif handler.db_type == 'mysql':
    schema_name = handler.connection.settings_dict['NAME']
elif handler.db_type == 'sqlserver':
    schema_name = 'dbo'
```

### 4. 安全性

- ✅ 使用参数化查询防止SQL注入
- ✅ WHERE条件必须提供（更新/删除）
- ✅ 执行时间记录
- ✅ 详细的错误日志

### 5. 性能优化

- ✅ 连接复用（Django连接池）
- ✅ 分页查询
- ✅ 索引信息缓存
- ✅ 批量操作支持

## 🔌 扩展新数据库

### 步骤1：创建处理器类

```python
# oracle_handler.py
from .base_database_handler import BaseDatabaseHandler

class OracleHandler(BaseDatabaseHandler):
    def get_databases(self):
        # 实现Oracle特定的查询
        pass
    
    def get_tables(self, schema_name):
        # 实现Oracle特定的查询
        pass
    
    # ... 实现其他抽象方法
```

### 步骤2：注册到工厂

```python
# database_manager_service.py
def get_handler(db_name: str):
    engine = connection.settings_dict.get('ENGINE', '')
    
    if 'oracle' in engine:
        return OracleHandler(db_name)
    # ... 其他数据库
```

### 步骤3：完成！

无需修改API层和前端代码，新数据库自动支持所有功能。

## 📊 数据流

```
前端请求
    ↓
API路由 (database_manager_api.py)
    ↓
工厂服务 (DatabaseManagerService.get_handler)
    ↓
具体处理器 (PostgreSQLHandler/MySQLHandler/SQLServerHandler)
    ↓
数据库连接 (Django connections)
    ↓
执行SQL
    ↓
返回结果
```

## 🧪 测试建议

### 1. 单元测试

```python
# 测试工厂模式
def test_get_handler():
    handler = DatabaseManagerService.get_handler('default')
    assert isinstance(handler, BaseDatabaseHandler)

# 测试PostgreSQL
def test_postgresql_get_databases():
    handler = PostgreSQLHandler('default')
    databases = handler.get_databases()
    assert isinstance(databases, list)
```

### 2. 集成测试

```bash
# 测试API端点
curl http://localhost:8000/api/core/database_manager/configs
curl http://localhost:8000/api/core/database_manager/default/databases
curl http://localhost:8000/api/core/database_manager/default/tables?schema_name=public
```

### 3. 性能测试

- 大表查询（100万+行）
- 并发查询测试
- 连接池压力测试

## 🔒 安全注意事项

### 1. SQL注入防护

```python
# ✅ 正确：使用参数化查询
cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))

# ❌ 错误：字符串拼接
cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")
```

### 2. 权限控制

- 建议添加权限装饰器
- 限制危险操作（DROP DATABASE等）
- 记录操作日志

### 3. 输入验证

- WHERE条件不能为空（更新/删除）
- SQL语句长度限制
- 特殊字符过滤

## 📝 使用示例

### Python代码

```python
from core.database_manager.database_manager_service import DatabaseManagerService

# 获取处理器
handler = DatabaseManagerService.get_handler('default')

# 获取数据库列表
databases = handler.get_databases()

# 获取表列表
tables = handler.get_tables('public')

# 查询数据
result = handler.query_data(
    table_name='users',
    schema_name='public',
    page=1,
    page_size=20,
    where="status = 'active'",
    order_by='created_at DESC'
)

# 执行SQL
result = handler.execute_sql(
    sql="SELECT count(*) FROM users",
    is_query=True
)
```

### API调用

```bash
# 获取配置
GET /api/core/database_manager/configs

# 获取数据库列表
GET /api/core/database_manager/default/databases

# 获取表列表
GET /api/core/database_manager/default/tables?schema_name=public

# 查询数据
POST /api/core/database_manager/default/query
{
  "table_name": "users",
  "schema_name": "public",
  "page": 1,
  "page_size": 20,
  "where": "status = 'active'",
  "order_by": "created_at DESC"
}

# 执行SQL
POST /api/core/database_manager/default/execute
{
  "sql": "SELECT count(*) FROM users",
  "is_query": true
}
```

## 🎉 完成状态

- ✅ 抽象基类设计
- ✅ PostgreSQL完整实现
- ✅ MySQL完整实现
- ✅ SQL Server完整实现
- ✅ Schema定义
- ✅ 服务层（工厂模式）
- ✅ API路由
- ✅ 路由注册

## 🚀 下一步

1. **前端实现** - 创建数据库管理界面
2. **权限控制** - 添加操作权限验证
3. **操作日志** - 记录所有数据库操作
4. **数据导出** - 支持SQL、CSV、Excel导出
5. **SQL编辑器** - 带语法高亮的SQL编辑器
6. **查询历史** - 保存和管理查询历史
7. **性能分析** - SQL执行计划分析
8. **备份恢复** - 数据库备份和恢复功能

## 📚 参考文档

- PostgreSQL: https://www.postgresql.org/docs/
- MySQL: https://dev.mysql.com/doc/
- SQL Server: https://docs.microsoft.com/en-us/sql/
- Django Database API: https://docs.djangoproject.com/en/stable/ref/databases/
