#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: redis_collector.py
@Desc: 递归地将Redis数据中的bytes转换为字符串，使其可以JSON序列化
"""
# time: 2024/12/20
# file: redis_collector.py
# author: AI Assistant

import logging
import time
from datetime import datetime
from typing import Dict, List, Optional, Any

import redis

logger = logging.getLogger(__name__)


def serialize_redis_data(data: Any) -> Any:
    """
    递归地将Redis数据中的bytes转换为字符串，使其可以JSON序列化
    """
    if isinstance(data, bytes):
        try:
            return data.decode('utf-8')
        except UnicodeDecodeError:
            return str(data)
    elif isinstance(data, dict):
        return {serialize_redis_data(k): serialize_redis_data(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [serialize_redis_data(item) for item in data]
    elif isinstance(data, tuple):
        return tuple(serialize_redis_data(item) for item in data)
    else:
        return data


class RedisInfoCollector:
    """Redis信息收集器"""

    def __init__(self, host: str = 'localhost', port: int = 6379,
                 password: Optional[str] = None, db: int = 0):
        self.host = host
        self.port = port
        self.password = password
        self.db = db
        self.client = None

    def connect(self) -> bool:
        """连接Redis"""
        try:
            self.client = redis.Redis(
                host=self.host,
                port=self.port,
                password=self.password,
                db=self.db,
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5
            )
            # 测试连接
            self.client.ping()
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Redis {self.host}:{self.port}: {e}")
            return False

    def disconnect(self):
        """断开连接"""
        if self.client:
            try:
                self.client.close()
            except Exception as e:
                logger.error(f"Error disconnecting from Redis: {e}")
            finally:
                self.client = None

    def test_connection(self) -> Dict[str, Any]:
        """测试Redis连接"""
        start_time = time.time()
        try:
            if self.connect():
                response_time = (time.time() - start_time) * 1000
                info = self.client.info('server')
                redis_version = info.get('redis_version', 'unknown')
                self.disconnect()
                data = {
                    'success': True,
                    'message': '连接成功',
                    'response_time': round(response_time, 2),
                    'redis_version': redis_version
                }
                return serialize_redis_data(data)
            else:
                return {
                    'success': False,
                    'message': '连接失败',
                    'response_time': None,
                    'redis_version': None
                }
        except Exception as e:
            return {
                'success': False,
                'message': f'连接错误: {str(e)}',
                'response_time': None,
                'redis_version': None
            }

    def get_server_info(self) -> Dict[str, Any]:
        """获取Redis服务器信息"""
        if not self.client:
            if not self.connect():
                return {}

        try:
            info = self.client.info('server')
            return {
                'redis_version': info.get('redis_version', ''),
                'redis_mode': info.get('redis_mode', 'standalone'),
                'role': info.get('role', 'master'),
                'os': info.get('os', ''),
                'arch_bits': info.get('arch_bits', 64),
                'uptime_in_seconds': info.get('uptime_in_seconds', 0),
                'uptime_in_days': info.get('uptime_in_days', 0),
                'tcp_port': info.get('tcp_port', self.port),
                'connected_clients': info.get('connected_clients', 0),
                'blocked_clients': info.get('blocked_clients', 0)
            }
        except Exception as e:
            logger.error(f"Error getting Redis server info: {e}")
            return {}

    def get_memory_info(self) -> Dict[str, Any]:
        """获取Redis内存信息"""
        if not self.client:
            if not self.connect():
                return {}

        try:
            info = self.client.info('memory')
            return {
                'used_memory': info.get('used_memory', 0),
                'used_memory_human': info.get('used_memory_human', '0B'),
                'used_memory_rss': info.get('used_memory_rss', 0),
                'used_memory_peak': info.get('used_memory_peak', 0),
                'used_memory_peak_human': info.get('used_memory_peak_human', '0B'),
                'total_system_memory': info.get('total_system_memory', 0),
                'total_system_memory_human': info.get('total_system_memory_human', '0B'),
                'used_memory_dataset': info.get('used_memory_dataset', 0),
                'used_memory_dataset_perc': info.get('used_memory_dataset_perc', '0%'),
                'allocator_allocated': info.get('allocator_allocated', 0),
                'allocator_active': info.get('allocator_active', 0),
                'maxmemory': info.get('maxmemory', 0),
                'maxmemory_human': info.get('maxmemory_human', '0B'),
                'maxmemory_policy': info.get('maxmemory_policy', 'noeviction'),
                'mem_fragmentation_ratio': info.get('mem_fragmentation_ratio', 1.0)
            }
        except Exception as e:
            logger.error(f"Error getting Redis memory info: {e}")
            return {}

    def get_stats_info(self) -> Dict[str, Any]:
        """获取Redis统计信息"""
        if not self.client:
            if not self.connect():
                return {}

        try:
            info = self.client.info('stats')
            return {
                'total_connections_received': info.get('total_connections_received', 0),
                'total_commands_processed': info.get('total_commands_processed', 0),
                'instantaneous_ops_per_sec': info.get('instantaneous_ops_per_sec', 0),
                'total_net_input_bytes': info.get('total_net_input_bytes', 0),
                'total_net_output_bytes': info.get('total_net_output_bytes', 0),
                'instantaneous_input_kbps': info.get('instantaneous_input_kbps', 0.0),
                'instantaneous_output_kbps': info.get('instantaneous_output_kbps', 0.0),
                'rejected_connections': info.get('rejected_connections', 0),
                'sync_full': info.get('sync_full', 0),
                'sync_partial_ok': info.get('sync_partial_ok', 0),
                'sync_partial_err': info.get('sync_partial_err', 0),
                'expired_keys': info.get('expired_keys', 0),
                'evicted_keys': info.get('evicted_keys', 0),
                'keyspace_hits': info.get('keyspace_hits', 0),
                'keyspace_misses': info.get('keyspace_misses', 0),
                'pubsub_channels': info.get('pubsub_channels', 0),
                'pubsub_patterns': info.get('pubsub_patterns', 0),
                'latest_fork_usec': info.get('latest_fork_usec', 0),
                'migrate_cached_sockets': info.get('migrate_cached_sockets', 0)
            }
        except Exception as e:
            logger.error(f"Error getting Redis stats info: {e}")
            return {}

    def get_keyspace_info(self) -> List[Dict[str, Any]]:
        """获取Redis键空间信息"""
        if not self.client:
            if not self.connect():
                return []

        try:
            info = self.client.info('keyspace')
            keyspaces = []

            for key, value in info.items():
                if key.startswith('db'):
                    db_id = int(key[2:])  # 移除'db'前缀

                    # 处理不同类型的value
                    if isinstance(value, dict):
                        # 如果value已经是字典，直接使用
                        stats = value
                    elif isinstance(value, str):
                        # 如果value是字符串，解析格式如: keys=3,expires=0,avg_ttl=0
                        stats = {}
                        for stat in value.split(','):
                            if '=' in stat:
                                stat_key, stat_value = stat.split('=', 1)
                                try:
                                    stats[stat_key] = int(stat_value)
                                except ValueError:
                                    stats[stat_key] = stat_value
                    else:
                        # 其他类型，跳过
                        continue

                    keyspaces.append({
                        'db_id': db_id,
                        'keys': stats.get('keys', 0),
                        'expires': stats.get('expires', 0),
                        'avg_ttl': stats.get('avg_ttl', 0)
                    })

            return keyspaces
        except Exception as e:
            logger.error(f"Error getting Redis keyspace info: {e}")
            return []

    def get_clients_info(self, limit: int = 100) -> List[Dict[str, Any]]:
        """获取Redis客户端信息"""
        if not self.client:
            if not self.connect():
                logger.warning("Failed to connect to Redis when getting clients info")
                return []

        try:
            clients_raw = self.client.client_list()
            logger.info(f"Got {len(clients_raw)} clients from Redis")
            clients = []

            for i, client in enumerate(clients_raw[:limit]):
                clients.append({
                    'id': str(client.get('id', '')),
                    'addr': client.get('addr', ''),
                    'fd': client.get('fd', 0),
                    'name': client.get('name', ''),
                    'age': client.get('age', 0),
                    'idle': client.get('idle', 0),
                    'flags': client.get('flags', ''),
                    'db': client.get('db', 0),
                    'sub': client.get('sub', 0),
                    'psub': client.get('psub', 0),
                    'multi': client.get('multi', 0),
                    'qbuf': client.get('qbuf', 0),
                    'qbuf_free': client.get('qbuf-free', 0),
                    'obl': client.get('obl', 0),
                    'oll': client.get('oll', 0),
                    'omem': client.get('omem', 0),
                    'events': client.get('events', ''),
                    'cmd': client.get('cmd', '')
                })

            logger.info(f"Returning {len(clients)} clients (limit: {limit})")
            return clients
        except Exception as e:
            logger.error(f"Error getting Redis clients info: {e}", exc_info=True)
            return []

    def get_slowlog(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取Redis慢日志"""
        if not self.client:
            if not self.connect():
                return []

        try:
            slowlog_raw = self.client.slowlog_get(limit)
            slowlog = []

            for entry in slowlog_raw:
                slowlog.append({
                    'id': entry['id'],
                    'timestamp': entry['start_time'],
                    'duration': entry['duration'],
                    'command': ' '.join(str(arg) for arg in entry['command']),
                    'client_ip': entry.get('client_address', 'unknown'),
                    'client_name': entry.get('client_name', '')
                })

            return slowlog
        except Exception as e:
            logger.error(f"Error getting Redis slowlog: {e}")
            return []

    def get_all_info(self, connection_id: str, connection_name: str) -> Dict[str, Any]:
        """获取所有Redis监控信息"""
        timestamp = datetime.now().isoformat()

        try:
            if not self.connect():
                return {
                    'connection_id': connection_id,
                    'connection_name': connection_name,
                    'status': 'disconnected',
                    'info': {},
                    'memory': {},
                    'stats': {},
                    'keyspace': [],
                    'clients': [],
                    'slow_log': [],
                    'timestamp': timestamp
                }

            data = {
                'connection_id': connection_id,
                'connection_name': connection_name,
                'status': 'connected',
                'info': self.get_server_info(),
                'memory': self.get_memory_info(),
                'stats': self.get_stats_info(),
                'keyspace': self.get_keyspace_info(),
                'clients': self.get_clients_info(),
                'slow_log': self.get_slowlog(),
                'timestamp': timestamp
            }
            return serialize_redis_data(data)
        except Exception as e:
            logger.error(f"Error getting Redis all info: {e}")
            return {
                'connection_id': connection_id,
                'connection_name': connection_name,
                'status': 'error',
                'info': {},
                'memory': {},
                'stats': {},
                'keyspace': [],
                'clients': [],
                'slow_log': [],
                'timestamp': timestamp
            }
        finally:
            self.disconnect()

    def get_realtime_stats(self, connection_id: str) -> Dict[str, Any]:
        """获取实时统计信息"""
        timestamp = datetime.now().isoformat()

        try:
            if not self.connect():
                return {
                    'connection_id': connection_id,
                    'used_memory': 0,
                    'memory_usage_percent': 0.0,
                    'connected_clients': 0,
                    'ops_per_sec': 0,
                    'hit_rate': 0.0,
                    'keyspace_hits': 0,
                    'keyspace_misses': 0,
                    'timestamp': timestamp
                }

            memory_info = self.get_memory_info()
            stats_info = self.get_stats_info()
            server_info = self.get_server_info()

            # 计算内存使用率
            used_memory = memory_info.get('used_memory', 0)
            total_memory = memory_info.get('total_system_memory', 0)
            memory_usage_percent = (used_memory / total_memory * 100) if total_memory > 0 else 0.0

            # 计算命中率
            hits = stats_info.get('keyspace_hits', 0)
            misses = stats_info.get('keyspace_misses', 0)
            hit_rate = (hits / (hits + misses) * 100) if (hits + misses) > 0 else 0.0

            data = {
                'connection_id': connection_id,
                'used_memory': used_memory,
                'memory_usage_percent': round(memory_usage_percent, 2),
                'connected_clients': server_info.get('connected_clients', 0),
                'ops_per_sec': stats_info.get('instantaneous_ops_per_sec', 0),
                'hit_rate': round(hit_rate, 2),
                'keyspace_hits': hits,
                'keyspace_misses': misses,
                'timestamp': timestamp
            }
            return serialize_redis_data(data)
        except Exception as e:
            logger.error(f"Error getting Redis realtime stats: {e}")
            return {
                'connection_id': connection_id,
                'used_memory': 0,
                'memory_usage_percent': 0.0,
                'connected_clients': 0,
                'ops_per_sec': 0,
                'hit_rate': 0.0,
                'keyspace_hits': 0,
                'keyspace_misses': 0,
                'timestamp': timestamp
            }
        finally:
            self.disconnect()
