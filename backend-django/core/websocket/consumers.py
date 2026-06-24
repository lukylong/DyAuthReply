#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: consumers.py
@Desc: 基于Token认证的WebSocket消费者基类 - 
"""
import asyncio
import json
import logging
from contextlib import suppress
from datetime import datetime
from typing import Optional, Dict, Any
from urllib.parse import parse_qs

import jwt
from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

logger = logging.getLogger(__name__)


class TokenAuthWebSocketConsumer(AsyncWebsocketConsumer):
    """基于Token认证的WebSocket消费者基类"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = None

    async def connect(self):
        """连接时进行Token认证"""
        # 获取查询参数中的token
        query_string = self.scope.get('query_string', b'').decode('utf-8')
        token = None

        if query_string:
            # 解析查询参数
            query_params = parse_qs(query_string)
            token_list = query_params.get('token', [])
            if token_list:
                token = token_list[0]

        if not token:
            logger.warning(f"WebSocket connection rejected: No token provided")
            await self.close(code=4001)
            return

        # 验证token
        try:
            # 使用与项目一致的JWT验证方式
            payload = jwt.decode(
                token,
                settings.JWT_ACCESS_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
                options={"verify_exp": True}
            )

            user_id = payload.get('id')
            if not user_id:
                logger.warning(f"WebSocket connection rejected: Invalid token payload")
                await self.close(code=4001)
                return

            # 将user_id存储到scope中
            self.scope['user_id'] = user_id
            self.user_id = user_id

            logger.info(f"WebSocket connection accepted for user {user_id}")
            await self.accept()

        except jwt.ExpiredSignatureError:
            logger.warning(f"WebSocket connection rejected: Token expired")
            await self.close(code=4001)
        except jwt.InvalidTokenError:
            logger.warning(f"WebSocket connection rejected: Invalid token")
            await self.close(code=4001)
        except Exception as e:
            logger.error(f"WebSocket authentication failed: {str(e)}")
            await self.close(code=4001)

    async def disconnect(self, close_code):
        """断开连接"""
        logger.info(f"WebSocket disconnected with code {close_code}")

    async def receive(self, text_data):
        """接收消息的基础处理"""
        try:
            data = json.loads(text_data)
            message_type = data.get('type', 'unknown')

            # 根据消息类型处理
            if message_type == 'ping':
                await self.send_message('pong', '心跳响应')
            else:
                await self.handle_message(data)

        except json.JSONDecodeError:
            await self.send_error('Invalid JSON format')
        except Exception as e:
            logger.error(f"Error receiving message: {str(e)}")
            await self.send_error(f'处理消息时出错: {str(e)}')

    async def handle_message(self, data: Dict[str, Any]):
        """子类需要实现的消息处理方法"""
        await self.send_error('Message type not supported')

    async def send_message(self, message_type: str, message: str, data: Optional[Dict] = None):
        """发送消息"""
        response = {
            'type': message_type,
            'message': message,
            'timestamp': datetime.now().isoformat()
        }
        if data:
            response['data'] = data

        await self.send(text_data=json.dumps(response))

    async def send_error(self, error_message: str):
        """发送错误消息"""
        await self.send_message('error', error_message)


class TestWebSocketConsumer(TokenAuthWebSocketConsumer):
    """测试WebSocket消费者"""

    async def handle_message(self, data: Dict[str, Any]):
        """处理测试消息"""
        message_type = data.get('type', 'unknown')
        content = data.get('content', '')

        if message_type == 'echo':
            await self.send_message('echo_response', f'回声: {content}')
        elif message_type == 'chat':
            await self.send_message('chat_response', f'收到聊天消息: {content}', {
                'user': f'user_{self.user_id}',
                'original_message': content
            })
        elif message_type == 'system_info':
            # 获取系统信息
            import platform
            system_info = {
                'hostname': platform.node(),
                'system': platform.system(),
                'python_version': platform.python_version(),
                'timestamp': datetime.now().isoformat()
            }
            await self.send_message('system_info_response', '系统信息', system_info)
        else:
            await self.send_message('unknown_response', f'未知消息类型: {message_type}')


class NotificationConsumer(TokenAuthWebSocketConsumer):
    """通知WebSocket消费者"""

    async def connect(self):
        """连接并加入通知组"""
        await super().connect()
        if hasattr(self, 'user_id'):
            # 加入用户通知组
            await self.channel_layer.group_add(
                f"notifications_user_{self.user_id}",
                self.channel_name
            )

    async def disconnect(self, close_code):
        """断开连接并离开通知组"""
        if hasattr(self, 'user_id'):
            await self.channel_layer.group_discard(
                f"notifications_user_{self.user_id}",
                self.channel_name
            )
        await super().disconnect(close_code)

    async def handle_message(self, data: Dict[str, Any]):
        """处理通知相关消息"""
        message_type = data.get('type', 'unknown')

        if message_type == 'subscribe':
            await self.send_message('subscribe_response', '已订阅通知')
        else:
            await self.send_message('notification_response', f'通知消息处理: {message_type}')

    # 处理从组广播的消息
    async def notification_message(self, event):
        """处理组广播的通知消息"""
        await self.send_message('notification', event['message'], event.get('data'))


class ServerMonitorConsumer(TokenAuthWebSocketConsumer):
    """服务器监控WebSocket消费者"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.monitor_task = None
        self.is_monitoring = False
        self.monitor_interval = 2  # 固定2秒更新一次
        # 创建持久的收集器实例以保持缓存数据
        from core.server_monitor.server_info import ServerInfoCollector
        self.server_collector = ServerInfoCollector()

    async def connect(self):
        """连接并开始监控"""
        await super().connect()
        if hasattr(self, 'user_id'):
            # 加入服务器监控组
            await self.channel_layer.group_add(
                "server_monitor",
                self.channel_name
            )

    async def disconnect(self, close_code):
        """断开连接并停止监控"""
        self.is_monitoring = False
        if self.monitor_task:
            self.monitor_task.cancel()

        if hasattr(self, 'user_id'):
            await self.channel_layer.group_discard(
                "server_monitor",
                self.channel_name
            )
        await super().disconnect(close_code)

    async def handle_message(self, data: Dict[str, Any]):
        """处理服务器监控消息"""
        message_type = data.get('type', 'unknown')

        if message_type == 'start_monitor':
            await self.start_monitoring()
        elif message_type == 'stop_monitor':
            await self.stop_monitoring()
        elif message_type == 'get_overview':
            await self.send_server_overview()
        elif message_type == 'get_realtime':
            await self.send_realtime_stats()
        else:
            await self.send_error(f'未知的监控命令: {message_type}')

    async def start_monitoring(self):
        """开始监控"""
        if self.is_monitoring:
            await self.send_message('monitor_status', '监控已在运行')
            return

        self.is_monitoring = True
        self.monitor_task = asyncio.create_task(self.monitor_loop())
        await self.send_message('monitor_started', f'开始监控，间隔{self.monitor_interval}秒')

    async def stop_monitoring(self):
        """停止监控"""
        self.is_monitoring = False
        if self.monitor_task:
            self.monitor_task.cancel()
            self.monitor_task = None
        await self.send_message('monitor_stopped', '监控已停止')

    async def restart_monitoring(self):
        """重启监控"""
        await self.stop_monitoring()
        await asyncio.sleep(0.1)  # 短暂延迟
        await self.start_monitoring()

    async def monitor_loop(self):
        """监控循环"""
        try:
            while self.is_monitoring:
                try:
                    await self.send_realtime_stats()
                except Exception as e:
                    logger.error(f"发送实时数据失败: {str(e)}")
                    # 发送错误消息但不停止监控循环
                    try:
                        await self.send_error(f'获取监控数据失败: {str(e)}')
                    except:
                        pass

                # 等待下一次监控间隔
                await asyncio.sleep(self.monitor_interval)
        except asyncio.CancelledError:
            logger.info("监控循环被取消")
        except Exception as e:
            logger.error(f"监控循环严重错误: {str(e)}")
            self.is_monitoring = False

    async def send_server_overview(self):
        """发送服务器概览信息"""
        try:
            overview_data = await sync_to_async(self.server_collector.get_all_info)()

            await self.send_message('server_overview', '服务器概览信息', overview_data)
        except Exception as e:
            logger.error(f"获取服务器概览失败: {str(e)}")
            await self.send_error(f'获取服务器概览失败: {str(e)}')

    async def send_realtime_stats(self):
        """发送实时统计信息"""
        try:
            realtime_data = await sync_to_async(self.server_collector.get_realtime_stats)()

            await self.send_message('realtime_stats', '实时统计信息', realtime_data)
        except Exception as e:
            logger.error(f"获取实时统计失败: {str(e)}")
            await self.send_error(f'获取实时统计失败: {str(e)}')


class RedisMonitorConsumer(TokenAuthWebSocketConsumer):
    """Redis监控WebSocket消费者"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.monitor_task = None
        self.is_monitoring = False
        self.monitor_interval = 2  # 固定2秒更新一次

    async def connect(self):
        """连接并开始监控"""
        await super().connect()
        if hasattr(self, 'user_id'):
            # 加入Redis监控组
            await self.channel_layer.group_add(
                "redis_monitor",
                self.channel_name
            )

    async def disconnect(self, close_code):
        """断开连接并停止监控"""
        self.is_monitoring = False
        if self.monitor_task:
            self.monitor_task.cancel()

        if hasattr(self, 'user_id'):
            await self.channel_layer.group_discard(
                "redis_monitor",
                self.channel_name
            )
        await super().disconnect(close_code)

    async def handle_message(self, data: Dict[str, Any]):
        """处理Redis监控消息"""
        message_type = data.get('type', 'unknown')

        if message_type == 'start_monitor':
            await self.start_monitoring()
        elif message_type == 'stop_monitor':
            await self.stop_monitoring()
        elif message_type == 'get_overview':
            await self.send_redis_overview()
        elif message_type == 'get_realtime':
            await self.send_realtime_stats()
        elif message_type == 'test_connection':
            await self.test_redis_connection()
        else:
            await self.send_error(f'未知的Redis监控命令: {message_type}')

    async def start_monitoring(self):
        """开始监控"""
        if self.is_monitoring:
            await self.send_message('monitor_status', 'Redis监控已在运行')
            return

        self.is_monitoring = True
        self.monitor_task = asyncio.create_task(self.monitor_loop())
        await self.send_message('monitor_started', f'开始Redis监控，间隔{self.monitor_interval}秒')

    async def stop_monitoring(self):
        """停止监控"""
        self.is_monitoring = False
        if self.monitor_task:
            self.monitor_task.cancel()
            self.monitor_task = None
        await self.send_message('monitor_stopped', 'Redis监控已停止')

    async def restart_monitoring(self):
        """重启监控"""
        await self.stop_monitoring()
        await asyncio.sleep(0.1)  # 短暂延迟
        await self.start_monitoring()

    async def monitor_loop(self):
        """监控循环"""
        try:
            while self.is_monitoring:
                try:
                    await self.send_realtime_stats()
                except Exception as e:
                    logger.error(f"发送Redis实时数据失败: {str(e)}")
                    # 发送错误消息但不停止监控循环
                    try:
                        await self.send_error(f'获取Redis监控数据失败: {str(e)}')
                    except:
                        pass

                # 等待下一次监控间隔
                await asyncio.sleep(self.monitor_interval)
        except asyncio.CancelledError:
            logger.info("Redis监控循环被取消")
        except Exception as e:
            logger.error(f"Redis监控循环严重错误: {str(e)}")
            self.is_monitoring = False

    async def send_redis_overview(self):
        """发送Redis概览信息"""
        try:
            from core.redis_monitor import RedisInfoCollector
            from django.conf import settings

            # 从Django设置中获取Redis配置
            redis_host = getattr(settings, 'REDIS_HOST', '127.0.0.1')
            redis_port = 6379
            redis_password = getattr(settings, 'REDIS_PASSWORD', None)
            redis_db = int(getattr(settings, 'REDIS_DB', '0'))

            if redis_password == '':
                redis_password = None

            collector = RedisInfoCollector(
                host=redis_host,
                port=redis_port,
                password=redis_password,
                db=redis_db
            )

            overview_data = await sync_to_async(collector.get_all_info)('project_redis', '项目Redis')

            await self.send_message('redis_overview', 'Redis概览信息', overview_data)
        except Exception as e:
            logger.error(f"获取Redis概览失败: {str(e)}")
            await self.send_error(f'获取Redis概览失败: {str(e)}')

    async def send_realtime_stats(self):
        """发送Redis实时统计信息"""
        try:
            from core.redis_monitor import RedisInfoCollector
            from django.conf import settings

            # 从Django设置中获取Redis配置
            redis_host = getattr(settings, 'REDIS_HOST', '127.0.0.1')
            redis_port = 6379
            redis_password = getattr(settings, 'REDIS_PASSWORD', None)
            redis_db = int(getattr(settings, 'REDIS_DB', '0'))

            if redis_password == '':
                redis_password = None

            collector = RedisInfoCollector(
                host=redis_host,
                port=redis_port,
                password=redis_password,
                db=redis_db
            )

            realtime_data = await sync_to_async(collector.get_realtime_stats)('project_redis')

            await self.send_message('redis_realtime', 'Redis实时统计', realtime_data)
        except Exception as e:
            logger.error(f"获取Redis实时统计失败: {str(e)}")
            await self.send_error(f'获取Redis实时统计失败: {str(e)}')

    async def test_redis_connection(self):
        """测试Redis连接"""
        try:
            from core.redis_monitor import RedisInfoCollector
            from django.conf import settings

            # 从Django设置中获取Redis配置
            redis_host = getattr(settings, 'REDIS_HOST', '127.0.0.1')
            redis_port = 6379
            redis_password = getattr(settings, 'REDIS_PASSWORD', None)
            redis_db = int(getattr(settings, 'REDIS_DB', '0'))

            if redis_password == '':
                redis_password = None

            collector = RedisInfoCollector(
                host=redis_host,
                port=redis_port,
                password=redis_password,
                db=redis_db
            )

            test_result = await sync_to_async(collector.test_connection)()

            await self.send_message('connection_test', 'Redis连接测试结果', test_result)
        except Exception as e:
            logger.error(f"Redis连接测试失败: {str(e)}")
            await self.send_error(f'Redis连接测试失败: {str(e)}')


def _read_data_version() -> int:
    """读取 SQLite PRAGMA data_version：任意其它连接（含 Worker 进程）提交写入后会变化，
    自身连接提交不变。用作极廉价的"库是否被改过"探针，避免空转全表查询。

    非 SQLite（如 Postgres 服务端）无此 pragma：返回 -1，调用方据此退化为每轮都查增量。
    """
    from django.db import connection
    try:
        with connection.cursor() as c:
            c.execute('PRAGMA data_version')
            row = c.fetchone()
            return int(row[0]) if row else -1
    except Exception:  # noqa: BLE001
        return -1


def _account_new_messages(account_id: str, after_ts) -> dict:
    """查询某账号自 after_ts 之后新增的消息（入向+出向），返回新游标与受影响会话集合。

    用 sys_create_datetime（本地落库时间，随 Worker 插入单调递增）做游标，覆盖
    收到的新私信与本方已发出的回复，让 UI 据此增量刷新。
    """
    from django.db.models import Max
    from core.douyin.douyin_message_model import DouyinMessage

    qs = DouyinMessage.objects.filter(conversation__account_id=account_id)
    if after_ts is not None:
        qs = qs.filter(sys_create_datetime__gt=after_ts)
    rows = list(
        qs.order_by('-sys_create_datetime')
        .values('conversation_id', 'sys_create_datetime')[:50]
    )
    if not rows:
        return {'new': False, 'cursor': after_ts, 'conversation_ids': []}
    new_max = rows[0]['sys_create_datetime']
    conv_ids = list({str(r['conversation_id']) for r in rows})
    return {'new': True, 'cursor': new_max, 'conversation_ids': conv_ids}


def _account_max_message_ts(account_id: str):
    from django.db.models import Max
    from core.douyin.douyin_message_model import DouyinMessage
    return (
        DouyinMessage.objects.filter(conversation__account_id=account_id)
        .aggregate(m=Max('sys_create_datetime'))['m']
    )


class DouyinConsumer(TokenAuthWebSocketConsumer):
    """抖音托管事件 WebSocket 消费者

    前端连接 `ws://host/ws/douyin/?token=xxx` 后自动加入分组
    `douyin_user_{user_id}`，由 worker 通过 channel_layer.group_send
    推送如下事件：
      - qr_image         扫码登录二维码（base64 图片）
      - login_success    登录成功
      - login_failed     登录失败 / 二维码过期
      - new_message      收到入向消息
      - reply_sent       已发送自动回复
      - reply_failed     回复失败
      - event            通用运行时事件（对应 DouyinEvent）

    """

    async def connect(self):
        await super().connect()
        if hasattr(self, 'user_id'):
            await self.channel_layer.group_add(
                f"douyin_user_{self.user_id}",
                self.channel_name,
            )
            logger.info(f"[DouyinConsumer] 用户 {self.user_id} 已加入抖音事件推送组")

    async def disconnect(self, close_code):
        if hasattr(self, 'user_id'):
            await self.channel_layer.group_discard(
                f"douyin_user_{self.user_id}",
                self.channel_name,
            )
        await super().disconnect(close_code)

    async def handle_message(self, data: Dict[str, Any]):
        """前端→后端的主动消息。目前仅保留 ping，其它预留给未来的"手动介入"指令"""
        message_type = data.get('type', 'unknown')
        if message_type == 'subscribe':
            await self.send_message('subscribe_response', '已订阅抖音事件推送')
        else:
            await self.send_message('unknown', f'暂不支持的消息类型: {message_type}')

    async def douyin_event(self, event: Dict[str, Any]):
        """处理 group_send({'type': 'douyin.event', 'event': ..., 'payload': ...})"""
        await self.send_message(
            event.get('event', 'douyin_event'),
            event.get('event', ''),
            {
                'event': event.get('event'),
                'payload': event.get('payload', {}),
                'timestamp': event.get('timestamp'),
            },
        )


class DouyinClientRealtimeConsumer(AsyncWebsocketConsumer):
    """桌面客户端实时私信消费者（方案 D）。

    客户端模式 Worker 与 API 跨进程、InMemoryChannelLayer 无法跨进程 group_send，
    故在 API 进程内用 PRAGMA data_version 廉价感知 Worker 落库，再查订阅账号的增量
    消息，推送 `new_message` 信号；UI 收到后走 REST 拉增量（复用解密/富媒体渲染），
    替代原 3 秒轮询。

    鉴权与客户端 REST 一致：仅允许本机回环连接（LocalDesktopAuth 同款），无 JWT。
    端点：`ws://127.0.0.1:8765/ws/client/douyin/`。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sub_account_id = None
        self._sub_conversation_id = None
        self._dbpoll_task = None
        self._last_data_version = None
        self._cursor_ts = None

    def _is_loopback(self) -> bool:
        client = self.scope.get('client') or []
        host = client[0] if client else ''
        return host in ('127.0.0.1', '::1', 'localhost', '')

    async def connect(self):
        if not self._is_loopback():
            await self.close(code=4003)
            return
        await self.accept()

    async def disconnect(self, close_code):
        if self._dbpoll_task is not None:
            self._dbpoll_task.cancel()
            self._dbpoll_task = None

    async def receive(self, text_data=None, bytes_data=None):
        try:
            data = json.loads(text_data or '{}')
        except json.JSONDecodeError:
            return
        mtype = data.get('type', '')
        if mtype == 'ping':
            await self._send('pong', {})
            return
        if mtype == 'subscribe':
            self._sub_account_id = (data.get('account_id') or '').strip() or None
            self._sub_conversation_id = (data.get('conversation_id') or '').strip() or None
            # 订阅时把游标对齐到当前最新，避免把已存在的历史消息当作"新消息"重复推送。
            if self._sub_account_id:
                with suppress(Exception):
                    self._cursor_ts = await sync_to_async(
                        _account_max_message_ts, thread_sensitive=True
                    )(self._sub_account_id)
            await self._ensure_dbpoll_started()
            await self._send('subscribe_response', {
                'account_id': self._sub_account_id,
                'conversation_id': self._sub_conversation_id,
            })

    async def _send(self, mtype: str, data: dict):
        await self.send(text_data=json.dumps({
            'type': mtype,
            'data': data,
            'timestamp': datetime.now().isoformat(),
        }))

    async def _ensure_dbpoll_started(self):
        if not bool(getattr(settings, 'DOUYIN_WS_REALTIME_ENABLED', True)):
            return
        if self._dbpoll_task is None or self._dbpoll_task.done():
            self._dbpoll_task = asyncio.create_task(self._dbpoll_loop())

    async def _dbpoll_loop(self):
        """每 interval 读 data_version；变化时查订阅账号增量消息并推送 new_message。"""
        interval = max(
            0.1,
            float(getattr(settings, 'DOUYIN_WS_DBPOLL_INTERVAL_MS', 400)) / 1000.0,
        )
        try:
            while True:
                await asyncio.sleep(interval)
                if not self._sub_account_id:
                    continue
                try:
                    dv = await sync_to_async(_read_data_version, thread_sensitive=True)()
                    # SQLite：dv 未变说明无任何写入，直接跳过；非 SQLite(dv=-1) 每轮都查。
                    if dv != -1 and dv == self._last_data_version:
                        continue
                    self._last_data_version = dv
                    result = await sync_to_async(
                        _account_new_messages, thread_sensitive=True
                    )(self._sub_account_id, self._cursor_ts)
                    if result.get('new'):
                        self._cursor_ts = result['cursor']
                        await self._send('new_message', {
                            'account_id': self._sub_account_id,
                            'conversation_ids': result.get('conversation_ids', []),
                        })
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"[DouyinClientRealtime] dbpoll 异常: {e}")
        except asyncio.CancelledError:
            pass


class DatabaseMonitorConsumer(TokenAuthWebSocketConsumer):
    """数据库监控WebSocket消费者"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.monitor_task = None
        self.is_monitoring = False
        self.monitor_interval = 2  # 固定2秒更新一次
        self.current_db_name = None

    async def connect(self):
        """连接并开始监控"""
        await super().connect()
        if hasattr(self, 'user_id'):
            # 加入数据库监控组
            await self.channel_layer.group_add(
                "database_monitor",
                self.channel_name
            )

    async def disconnect(self, close_code):
        """断开连接并停止监控"""
        self.is_monitoring = False
        if self.monitor_task:
            self.monitor_task.cancel()

        if hasattr(self, 'user_id'):
            await self.channel_layer.group_discard(
                "database_monitor",
                self.channel_name
            )
        await super().disconnect(close_code)

    async def handle_message(self, data: Dict[str, Any]):
        """处理数据库监控消息"""
        message_type = data.get('type', 'unknown')

        if message_type == 'start_monitor':
            db_name = data.get('db_name')
            if not db_name:
                await self.send_error('缺少数据库名称参数')
                return
            await self.start_monitoring(db_name)
        elif message_type == 'stop_monitor':
            await self.stop_monitoring()
        elif message_type == 'get_overview':
            db_name = data.get('db_name')
            if not db_name:
                await self.send_error('缺少数据库名称参数')
                return
            await self.send_database_overview(db_name)
        elif message_type == 'get_realtime':
            db_name = data.get('db_name')
            if not db_name:
                await self.send_error('缺少数据库名称参数')
                return
            await self.send_realtime_stats(db_name)
        elif message_type == 'test_connection':
            db_name = data.get('db_name')
            if not db_name:
                await self.send_error('缺少数据库名称参数')
                return
            await self.test_database_connection(db_name)
        elif message_type == 'get_configs':
            await self.send_database_configs()
        else:
            await self.send_error(f'未知的数据库监控命令: {message_type}')

    async def start_monitoring(self, db_name: str):
        """开始监控"""
        if self.is_monitoring:
            await self.send_message('monitor_status', '数据库监控已在运行')
            return

        self.current_db_name = db_name
        self.is_monitoring = True
        self.monitor_task = asyncio.create_task(self.monitor_loop())
        await self.send_message('monitor_started', f'开始数据库监控({db_name})，间隔{self.monitor_interval}秒')

    async def stop_monitoring(self):
        """停止监控"""
        self.is_monitoring = False
        if self.monitor_task:
            self.monitor_task.cancel()
            self.monitor_task = None
        self.current_db_name = None
        await self.send_message('monitor_stopped', '数据库监控已停止')

    async def restart_monitoring(self):
        """重启监控"""
        if self.current_db_name:
            await self.stop_monitoring()
            await asyncio.sleep(0.1)  # 短暂延迟
            await self.start_monitoring(self.current_db_name)

    async def monitor_loop(self):
        """监控循环"""
        try:
            while self.is_monitoring and self.current_db_name:
                try:
                    await self.send_realtime_stats(self.current_db_name)
                except Exception as e:
                    logger.error(f"发送数据库实时数据失败: {str(e)}")
                    # 发送错误消息但不停止监控循环
                    try:
                        await self.send_error(f'获取数据库监控数据失败: {str(e)}')
                    except:
                        pass

                # 等待下一次监控间隔
                await asyncio.sleep(self.monitor_interval)
        except asyncio.CancelledError:
            logger.info("数据库监控循环被取消")
        except Exception as e:
            logger.error(f"数据库监控循环严重错误: {str(e)}")
            self.is_monitoring = False

    async def send_database_configs(self):
        """发送数据库配置列表"""
        try:
            from core.database_monitor import get_database_configs

            configs = await sync_to_async(get_database_configs)()

            await self.send_message('database_configs', '数据库配置列表', configs)
        except Exception as e:
            logger.error(f"获取数据库配置失败: {str(e)}")
            await self.send_error(f'获取数据库配置失败: {str(e)}')

    async def send_database_overview(self, db_name: str):
        """发送数据库概览信息"""
        try:
            from core.database_monitor import get_database_configs
            from core.database_monitor import DatabaseCollector

            configs = await sync_to_async(get_database_configs)()
            db_config = next((config for config in configs if config['db_name'] == db_name), None)

            if not db_config:
                await self.send_error(f'数据库 {db_name} 未找到')
                return

            collector = DatabaseCollector(
                db_type=db_config['db_type'],
                host=db_config['host'],
                port=db_config['port'],
                user=db_config['user'],
                password=db_config['password'],
                database=db_config['database']
            )

            overview_data = await sync_to_async(collector.get_all_info)(db_name, db_config['name'])

            await self.send_message('database_overview', '数据库概览信息', overview_data)
        except Exception as e:
            logger.error(f"获取数据库概览失败: {str(e)}")
            await self.send_error(f'获取数据库概览失败: {str(e)}')

    async def send_realtime_stats(self, db_name: str):
        """发送数据库实时统计信息"""
        try:
            from core.database_monitor import get_database_configs
            from core.database_monitor import DatabaseCollector

            configs = await sync_to_async(get_database_configs)()
            db_config = next((config for config in configs if config['db_name'] == db_name), None)

            if not db_config:
                await self.send_error(f'数据库 {db_name} 未找到')
                return

            collector = DatabaseCollector(
                db_type=db_config['db_type'],
                host=db_config['host'],
                port=db_config['port'],
                user=db_config['user'],
                password=db_config['password'],
                database=db_config['database']
            )

            realtime_data = await sync_to_async(collector.get_realtime_stats)(db_name)

            await self.send_message('database_realtime', '数据库实时统计', realtime_data)
        except Exception as e:
            logger.error(f"获取数据库实时统计失败: {str(e)}")
            await self.send_error(f'获取数据库实时统计失败: {str(e)}')

    async def test_database_connection(self, db_name: str):
        """测试数据库连接"""
        try:
            from core.database_monitor import get_database_configs
            from core.database_monitor import DatabaseCollector

            configs = await sync_to_async(get_database_configs)()
            db_config = next((config for config in configs if config['db_name'] == db_name), None)

            if not db_config:
                await self.send_error(f'数据库 {db_name} 未找到')
                return

            collector = DatabaseCollector(
                db_type=db_config['db_type'],
                host=db_config['host'],
                port=db_config['port'],
                user=db_config['user'],
                password=db_config['password'],
                database=db_config['database']
            )

            test_result = await sync_to_async(collector.test_connection)()

            await self.send_message('connection_test', '数据库连接测试结果', test_result)
        except Exception as e:
            logger.error(f"数据库连接测试失败: {str(e)}")
            await self.send_error(f'数据库连接测试失败: {str(e)}')
