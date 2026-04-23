#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: redis_manager_service.py
@Desc: Redis管理服务 - 
"""
# time: 2024/12/21
# file: redis_manager_service.py
# author: AI Assistant

import logging
from typing import List, Dict, Any, Optional, Tuple

import redis
from django.conf import settings

logger = logging.getLogger(__name__)


class RedisManagerService:
    """Redis管理服务"""

    def __init__(self, db_index: int = 0):
        """
        初始化Redis连接
        
        Args:
            db_index: Redis数据库索引（0-15）
        """
        # redis_config = getattr(settings, 'REDIS_CONFIG', {})
        redis_host = getattr(settings, 'REDIS_HOST', '127.0.0.1')
        redis_port = getattr(settings, 'REDIS_PORT', 6379)
        redis_password = getattr(settings, 'REDIS_PASSWORD', None)
        redis_db = int(getattr(settings, 'REDIS_DB', '0'))
        # 不自动解码，因为可能有二进制数据
        self.client = redis.Redis(
            host=redis_host,
            port=redis_port,
            db=db_index,
            password=redis_password,
            decode_responses=False  # 不自动解码，手动处理
        )
        self.db_index = db_index

    def _safe_decode(self, value: any) -> str:
        """安全解码Redis值"""
        if value is None:
            return ''
        if isinstance(value, bytes):
            try:
                return value.decode('utf-8')
            except UnicodeDecodeError:
                # 如果无法解码为UTF-8，返回base64编码的字符串
                import base64
                return f"<binary data: {base64.b64encode(value).decode('ascii')}>"
        return str(value)

    def get_all_databases(self) -> List[Dict[str, Any]]:
        """
        获取所有Redis数据库信息
        
        Returns:
            数据库列表
        """
        databases = []
        total_keys = 0

        # Redis默认有16个数据库（0-15）
        for db_idx in range(16):
            try:
                temp_client = redis.Redis(
                    host=self.client.connection_pool.connection_kwargs['host'],
                    port=self.client.connection_pool.connection_kwargs['port'],
                    db=db_idx,
                    password=self.client.connection_pool.connection_kwargs.get('password'),
                    decode_responses=True
                )

                # 获取数据库信息
                info = temp_client.info('keyspace')
                db_key = f'db{db_idx}'

                if db_key in info:
                    db_info = info[db_key]
                    keys_count = db_info.get('keys', 0)
                    expires_count = db_info.get('expires', 0)
                    avg_ttl = db_info.get('avg_ttl', 0)
                else:
                    keys_count = 0
                    expires_count = 0
                    avg_ttl = 0

                databases.append({
                    'db_index': db_idx,
                    'keys_count': keys_count,
                    'expires_count': expires_count,
                    'avg_ttl': avg_ttl
                })

                total_keys += keys_count
                temp_client.close()

            except Exception as e:
                logger.error(f"Failed to get info for db{db_idx}: {e}")
                databases.append({
                    'db_index': db_idx,
                    'keys_count': 0,
                    'expires_count': 0,
                    'avg_ttl': 0
                })

        return databases, total_keys

    def search_keys(
            self,
            pattern: str = "*",
            key_type: Optional[str] = None,
            page: int = 1,
            page_size: int = 20
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        搜索Redis键
        
        Args:
            pattern: 搜索模式
            key_type: 键类型过滤
            page: 页码
            page_size: 每页数量
            
        Returns:
            (键列表, 总数)
        """
        try:
            # 使用SCAN命令遍历所有键
            all_keys = []
            cursor = 0

            while True:
                cursor, keys = self.client.scan(cursor, match=pattern.encode() if isinstance(pattern, str) else pattern,
                                                count=100)
                # 解码键名
                all_keys.extend([self._safe_decode(k) for k in keys])
                if cursor == 0:
                    break

            logger.info(f"Scanned {len(all_keys)} keys with pattern '{pattern}'")

            # 按类型过滤
            if key_type:
                filtered_keys = []
                for key in all_keys:
                    ktype = self.client.type(key)
                    if ktype == key_type:
                        filtered_keys.append(key)
                logger.info(f"Filtered to {len(filtered_keys)} keys of type '{key_type}'")
                all_keys = filtered_keys

            total = len(all_keys)
            logger.info(f"Total keys: {total}, returning page {page} with {page_size} items per page")

            # 分页
            start = (page - 1) * page_size
            end = start + page_size
            page_keys = all_keys[start:end]

            # 获取键的详细信息
            keys_info = []
            for key in page_keys:
                try:
                    key_info = self._get_key_info(key)
                    keys_info.append(key_info)
                except Exception as e:
                    logger.error(f"Failed to get info for key {key}: {e}")
                    # 即使获取详细信息失败，也添加基本信息
                    keys_info.append({
                        'key': key,
                        'type': 'unknown',
                        'ttl': -1,
                        'size': None,
                        'length': None,
                        'encoding': None
                    })

            return keys_info, total

        except Exception as e:
            logger.error(f"Failed to search keys: {e}")
            raise

    def _get_key_info(self, key: str) -> Dict[str, Any]:
        """获取键的基本信息"""
        key_bytes = key.encode() if isinstance(key, str) else key
        key_type = self._safe_decode(self.client.type(key_bytes))
        ttl = self.client.ttl(key_bytes)

        info = {
            'key': key,
            'type': key_type,
            'ttl': ttl,
            'encoding': None
        }

        # 获取编码信息
        try:
            if self.client.exists(key_bytes):
                encoding = self.client.object('encoding', key_bytes)
                info['encoding'] = self._safe_decode(encoding) if encoding else None
        except Exception:
            pass

        # 获取大小和长度
        if key_type == 'string':
            value = self.client.get(key_bytes)
            info['size'] = len(value) if value else 0
        elif key_type == 'list':
            info['length'] = self.client.llen(key_bytes)
        elif key_type == 'set':
            info['length'] = self.client.scard(key_bytes)
        elif key_type == 'zset':
            info['length'] = self.client.zcard(key_bytes)
        elif key_type == 'hash':
            info['length'] = self.client.hlen(key_bytes)

        return info

    def get_key_detail(self, key: str) -> Dict[str, Any]:
        """
        获取键的详细信息
        
        Args:
            key: 键名
            
        Returns:
            键的详细信息
        """
        key_bytes = key.encode() if isinstance(key, str) else key

        if not self.client.exists(key_bytes):
            raise ValueError(f"Key '{key}' does not exist")

        key_type = self._safe_decode(self.client.type(key_bytes))
        ttl = self.client.ttl(key_bytes)

        detail = {
            'key': key,
            'type': key_type,
            'ttl': ttl,
            'encoding': None
        }

        # 获取编码信息
        try:
            encoding = self.client.object('encoding', key_bytes)
            detail['encoding'] = self._safe_decode(encoding) if encoding else None
        except Exception:
            pass

        # 根据类型获取值
        if key_type == 'string':
            value = self.client.get(key_bytes)
            detail['value'] = self._safe_decode(value)
            detail['size'] = len(value) if value else 0
        elif key_type == 'list':
            values = self.client.lrange(key_bytes, 0, -1)
            detail['value'] = [self._safe_decode(v) for v in values]
            detail['length'] = len(values)
        elif key_type == 'set':
            members = self.client.smembers(key_bytes)
            detail['value'] = [self._safe_decode(m) for m in members]
            detail['length'] = len(members)
        elif key_type == 'zset':
            members = self.client.zrange(key_bytes, 0, -1, withscores=True)
            detail['value'] = [{'member': self._safe_decode(m), 'score': s} for m, s in members]
            detail['length'] = len(members)
        elif key_type == 'hash':
            hash_data = self.client.hgetall(key_bytes)
            detail['value'] = {self._safe_decode(k): self._safe_decode(v) for k, v in hash_data.items()}
            detail['length'] = len(hash_data)

        return detail

    def create_key(self, key: str, key_type: str, value: Any, ttl: Optional[int] = None) -> bool:
        """
        创建Redis键
        
        Args:
            key: 键名
            key_type: 数据类型
            value: 值
            ttl: 过期时间（秒）
            
        Returns:
            是否成功
        """
        try:
            # 检查键是否已存在
            if self.client.exists(key):
                raise ValueError(f"Key '{key}' already exists")

            # 根据类型设置值
            if key_type == 'string':
                self.client.set(key, value)
            elif key_type == 'list':
                if isinstance(value, list):
                    self.client.rpush(key, *value)
                else:
                    raise ValueError("List type requires a list value")
            elif key_type == 'set':
                if isinstance(value, list):
                    self.client.sadd(key, *value)
                else:
                    raise ValueError("Set type requires a list value")
            elif key_type == 'zset':
                if isinstance(value, list):
                    # value格式: [{"member": "xxx", "score": 1.0}]
                    mapping = {item['member']: item['score'] for item in value}
                    self.client.zadd(key, mapping)
                else:
                    raise ValueError("ZSet type requires a list of {member, score} dicts")
            elif key_type == 'hash':
                if isinstance(value, dict):
                    self.client.hset(key, mapping=value)
                else:
                    raise ValueError("Hash type requires a dict value")
            else:
                raise ValueError(f"Unsupported type: {key_type}")

            # 设置过期时间
            if ttl and ttl > 0:
                self.client.expire(key, ttl)

            return True

        except Exception as e:
            logger.error(f"Failed to create key {key}: {e}")
            raise

    def update_key(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """
        更新Redis键
        
        Args:
            key: 键名
            value: 新值
            ttl: 过期时间（秒）
            
        Returns:
            是否成功
        """
        try:
            if not self.client.exists(key):
                raise ValueError(f"Key '{key}' does not exist")

            key_type = self.client.type(key)

            # 删除旧值
            self.client.delete(key)

            # 设置新值
            if key_type == 'string':
                self.client.set(key, value)
            elif key_type == 'list':
                if isinstance(value, list):
                    self.client.rpush(key, *value)
                else:
                    raise ValueError("List type requires a list value")
            elif key_type == 'set':
                if isinstance(value, list):
                    self.client.sadd(key, *value)
                else:
                    raise ValueError("Set type requires a list value")
            elif key_type == 'zset':
                if isinstance(value, list):
                    mapping = {item['member']: item['score'] for item in value}
                    self.client.zadd(key, mapping)
                else:
                    raise ValueError("ZSet type requires a list of {member, score} dicts")
            elif key_type == 'hash':
                if isinstance(value, dict):
                    self.client.hset(key, mapping=value)
                else:
                    raise ValueError("Hash type requires a dict value")

            # 设置过期时间
            if ttl is not None:
                if ttl > 0:
                    self.client.expire(key, ttl)
                elif ttl == -1:
                    self.client.persist(key)

            return True

        except Exception as e:
            logger.error(f"Failed to update key {key}: {e}")
            raise

    def delete_key(self, key: str) -> bool:
        """
        删除Redis键
        
        Args:
            key: 键名
            
        Returns:
            是否成功
        """
        try:
            result = self.client.delete(key)
            return result > 0
        except Exception as e:
            logger.error(f"Failed to delete key {key}: {e}")
            raise

    def batch_delete_keys(self, keys: List[str]) -> int:
        """
        批量删除Redis键
        
        Args:
            keys: 键名列表
            
        Returns:
            删除的键数量
        """
        try:
            if not keys:
                return 0
            return self.client.delete(*keys)
        except Exception as e:
            logger.error(f"Failed to batch delete keys: {e}")
            raise

    def rename_key(self, old_key: str, new_key: str) -> bool:
        """
        重命名键
        
        Args:
            old_key: 旧键名
            new_key: 新键名
            
        Returns:
            是否成功
        """
        try:
            if not self.client.exists(old_key):
                raise ValueError(f"Key '{old_key}' does not exist")

            if self.client.exists(new_key):
                raise ValueError(f"Key '{new_key}' already exists")

            self.client.rename(old_key, new_key)
            return True

        except Exception as e:
            logger.error(f"Failed to rename key {old_key} to {new_key}: {e}")
            raise

    def set_expire(self, key: str, ttl: int) -> bool:
        """
        设置键的过期时间
        
        Args:
            key: 键名
            ttl: 过期时间（秒），-1表示永不过期
            
        Returns:
            是否成功
        """
        try:
            if not self.client.exists(key):
                raise ValueError(f"Key '{key}' does not exist")

            if ttl == -1:
                self.client.persist(key)
            else:
                self.client.expire(key, ttl)

            return True

        except Exception as e:
            logger.error(f"Failed to set expire for key {key}: {e}")
            raise

    def flush_db(self, confirm: bool = False) -> bool:
        """
        清空当前数据库
        
        Args:
            confirm: 确认清空
            
        Returns:
            是否成功
        """
        if not confirm:
            raise ValueError("Must confirm to flush database")

        try:
            self.client.flushdb()
            return True
        except Exception as e:
            logger.error(f"Failed to flush database: {e}")
            raise

    def close(self):
        """关闭连接"""
        if self.client:
            self.client.close()
