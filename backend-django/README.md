## 运行教程

### 在 `config/env.py` 中配置数据库信息

```bash
# 默认是Postgres SQL
# 数据库类型 MYSQL/SQLSERVER/SQLITE3/POSTGRESQL
DATABASE_TYPE = "POSTGRESQL"
# 数据库地址
DATABASE_HOST = "127.0.0.1"
# 数据库端口
DATABASE_PORT = 3306
# 数据库用户名
DATABASE_USER = ""
# 数据库密码
DATABASE_PASSWORD = ""
# 数据库名
DATABASE_NAME = ""
```

### 安装依赖环境

```bash
pip install -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple -r requirements.txt
```

### 安装 Playwright 浏览器内核（抖音私信自动回复模块需要）

`core.douyin` 模块依赖 Playwright 驱动 Chromium 接管创作者中心，首次部署需要执行：

```bash
# 安装 Chromium 内核（约 170MB）
playwright install chromium

# 如果在 Linux 服务器上运行且缺少系统依赖，再执行：
playwright install-deps chromium
```

独立 worker 进程（M2 里程碑）需要额外在 `env/*.py` 中配置：

```python
DOUYIN_DATA_DIR = "/var/lib/zq-platform/douyin"   # storage_state 与 user_data_dir 根目录
DOUYIN_STORAGE_ENCRYPTION_KEY = "<Fernet key>"    # 登录态加密密钥
DOUYIN_WORKER_HEADLESS = False                    # 首次扫码登录需有头模式
DOUYIN_REDIS_CHANNEL_PREFIX = "douyin"            # 指令频道前缀
```

### 执行迁移命令

```bash
python manage.py makemigrations core scheduler
```

```bash
python manage.py migrate
```

### 初始化数据

```bash
python manage.py loaddata db_init.json
```

### 启动项目

```bash
python manage.py runserver 0.0.0.0:8000
```
```bash
python -m uvicorn application.asgi:application --host 0.0.0.0 --port 8000 --reload --log-level info
```

## 数据导出
 python manage.py dumpdata > db_init.json