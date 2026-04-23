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
