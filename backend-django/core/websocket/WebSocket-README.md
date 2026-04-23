# WebSocket测试页面

## 功能概述

这是一个WebSocket连接测试页面，用于验证前后端WebSocket通信功能。支持实时消息收发、连接状态监控和多种消息类型测试。

## 技术架构

### 后端技术栈

- **Django Channels** - WebSocket支持
- **Redis** - 消息队列和频道层
- **AsyncWebsocketConsumer** - 异步WebSocket消费者
- **JWT认证** - 用户身份验证

### 前端技术栈

- **Vue 3** + **TypeScript**
- **Ant Design Vue** - UI组件库
- **WebSocket API** - 原生WebSocket连接
- **Vben Admin V5** - 管理后台框架

## 功能特性

### 1. 连接管理

- **自动重连** - 连接断开时自动尝试重连（最多5次）
- **心跳检测** - 定期发送ping消息保持连接活跃
- **连接状态监控** - 实时显示连接状态和统计信息
- **JWT认证** - 基于用户登录状态的连接认证

### 2. 消息类型

- **Echo（回显）** - 服务器原样返回发送的消息
- **Chat（聊天）** - 模拟聊天消息，服务器会回复
- **Ping/Pong** - 心跳检测消息
- **System Info（系统信息）** - 获取服务器系统信息
- **Custom（自定义）** - 发送自定义类型消息

### 3. 用户界面

- **连接统计** - 显示发送/接收消息数、重连次数、连接时间
- **消息历史** - 实时显示所有收发消息的详细记录
- **快捷操作** - 预设常用消息的快捷发送按钮
- **连接信息** - 显示WebSocket配置和状态详情

## API 接口

### WebSocket 端点

#### 测试连接

```
ws://localhost:8000/ws/test/
```

- **用途**: WebSocket连接测试
- **认证**: 需要JWT Token
- **消费者**: `TestWebSocketConsumer`

#### 通知推送

```
ws://localhost:8000/ws/notifications/
```

- **用途**: 系统通知推送
- **认证**: 需要JWT Token
- **消费者**: `NotificationConsumer`

### 消息格式

#### 客户端发送消息

```json
{
  "type": "echo|chat|ping|system_info|custom",
  "content": "消息内容",
  "timestamp": "2024-01-01T00:00:00Z"
}
```

#### 服务器响应消息

```json
{
  "type": "echo_response|chat_response|pong|system_info_response|error",
  "message": "响应消息",
  "user": "用户名",
  "timestamp": "2024-01-01T00:00:00Z",
  "data": {...}
}
```

## 文件结构

```
web-v5/apps/web-antd/src/views/system/websocket-test/
├── index.vue           # 主页面组件
└── README.md          # 说明文档

web-v5/apps/web-antd/src/api/system/
└── websocket.ts       # WebSocket API和管理类

backend/system/websocket/
├── __init__.py        # 模块初始化
├── consumers.py       # WebSocket消费者
└── routing.py         # WebSocket路由配置

backend/application/
├── asgi.py           # ASGI配置（更新支持WebSocket）
└── settings.py       # Django设置（添加Channels配置）
```

## 配置说明

### 后端配置

#### 1. Django设置 (settings.py)

```python
INSTALLED_APPS = [
    # ...
    'channels',  # WebSocket支持
    # ...
]

# ASGI应用程序
ASGI_APPLICATION = 'application.asgi.application'

# Channels层配置
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:6379/{REDIS_DB}"],
            "symmetric_encryption_keys": [SECRET_KEY],
        },
    },
}
```

#### 2. ASGI配置 (asgi.py)

```python
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from core.websocket import websocket_urlpatterns

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(
        URLRouter(websocket_urlpatterns)
    ),
})
```

### 前端配置

#### 1. WebSocket管理类

```typescript
// 创建WebSocket连接
const wsManager = createTestWebSocket({
  onOpen: (event) => { /* 连接建立 */ },
  onMessage: (message) => { /* 接收消息 */ },
  onClose: (event) => { /* 连接关闭 */ },
  onError: (event) => { /* 连接错误 */ },
});

// 连接WebSocket
await wsManager.connect();

// 发送消息
wsManager.send({
  type: 'echo',
  content: 'Hello WebSocket!'
});

// 关闭连接
wsManager.close();
```

## 使用方法

### 1. 启动后端服务

```bash
cd backend
python manage.py runserver 0.0.0.0:8000
```

### 2. 启动前端服务

```bash
cd web-v5
pnpm dev
```

### 3. 访问测试页面

登录系统后，在系统管理菜单中找到"WebSocket测试"页面。

### 4. 测试连接

1. 点击"连接"按钮建立WebSocket连接
2. 选择消息类型并输入内容
3. 点击"发送"按钮发送消息
4. 查看消息历史区域的收发记录
5. 使用快捷操作按钮快速测试

## 故障排除

### 1. 连接失败

- **检查后端服务** - 确保Django服务正在运行
- **检查Redis服务** - 确保Redis服务可用
- **检查用户认证** - 确保已登录并有有效Token

### 2. 消息发送失败

- **检查连接状态** - 确保WebSocket连接正常
- **检查消息格式** - 确保消息符合JSON格式
- **查看控制台错误** - 检查浏览器开发者工具

### 3. 自动重连问题

- **检查网络连接** - 确保网络稳定
- **检查服务器状态** - 确保后端服务未重启
- **调整重连配置** - 可在代码中修改重连参数

## 扩展功能

### 1. 添加新的消息类型

在后端 `consumers.py` 中添加新的消息处理逻辑：

```python
elif message_type == 'new_type':
    # 处理新类型消息
    await self.send(text_data=json.dumps({
        'type': 'new_type_response',
        'message': '处理结果',
        'timestamp': self._get_timestamp()
    }))
```

### 2. 集成业务功能

可以将WebSocket用于：

- **实时通知推送**
- **在线用户状态**
- **实时数据更新**
- **聊天功能**
- **系统监控数据推送**

## 安全考虑

1. **身份认证** - 使用JWT Token验证用户身份
2. **消息验证** - 验证消息格式和内容
3. **频率限制** - 防止消息发送过于频繁
4. **错误处理** - 优雅处理各种异常情况
5. **日志记录** - 记录连接和消息日志

## 性能优化

1. **连接池管理** - 合理管理WebSocket连接
2. **消息压缩** - 对大消息进行压缩
3. **心跳优化** - 调整心跳间隔减少网络开销
4. **内存管理** - 及时清理断开的连接
5. **负载均衡** - 支持多实例部署

这个WebSocket测试功能为项目提供了完整的实时通信能力，可以作为后续业务功能的基础。 