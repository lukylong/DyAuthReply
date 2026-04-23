#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: database_manager_service.py
@Desc: 数据库管理服务 - 使用工厂模式根据数据库类型创建对应的处理器
"""
"""
数据库管理服务
使用工厂模式根据数据库类型创建对应的处理器
"""
import logging

from django.conf import settings
from django.db import connections

from .base_database_handler import BaseDatabaseHandler
from .mysql_handler import MySQLHandler
from .postgresql_handler import PostgreSQLHandler
from .sqlserver_handler import SQLServerHandler

logger = logging.getLogger(__name__)


class DatabaseManagerService:
    """数据库管理服务工厂"""

    @staticmethod
    def _normalize_port(db_type: str, raw_port) -> int:
        """将数据库端口规范化为整数，避免响应校验失败"""
        default_port_map = {
            'postgresql': 5432,
            'mysql': 3306,
            'sqlserver': 1433,
            'sqlite': 0,
            'oracle': 1521,
            'unknown': 0,
        }
        default_port = default_port_map.get(db_type, 0)

        if raw_port is None or raw_port == '':
            return default_port

        try:
            return int(raw_port)
        except (TypeError, ValueError):
            logger.warning(f"Invalid database port value: {raw_port!r}, fallback to {default_port}")
            return default_port

    @staticmethod
    def get_handler(db_name: str = "default") -> BaseDatabaseHandler:
        """
        根据数据库类型获取对应的处理器
        :param db_name: Django配置的数据库名称
        :return: 数据库处理器实例
        """
        try:
            connection = connections[db_name]
            engine = connection.settings_dict.get('ENGINE', '')

            if 'postgresql' in engine:
                return PostgreSQLHandler(db_name)
            elif 'mysql' in engine:
                return MySQLHandler(db_name)
            elif 'sql_server' in engine or 'mssql' in engine:
                return SQLServerHandler(db_name)
            else:
                # 默认使用基类（可能不支持某些特定操作）
                logger.warning(f"Unsupported database engine: {engine}, using base handler")
                raise ValueError(f"Unsupported database type: {engine}")
        except Exception as e:
            logger.error(f"Failed to get database handler for {db_name}: {e}")
            raise

    @staticmethod
    def get_database_configs():
        """获取所有配置的数据库信息"""
        configs = []

        for db_name, db_config in settings.DATABASES.items():
            engine = db_config.get('ENGINE', '')

            # 确定数据库类型
            if 'postgresql' in engine:
                db_type = 'postgresql'
            elif 'mysql' in engine:
                db_type = 'mysql'
            elif 'sql_server' in engine or 'mssql' in engine:
                db_type = 'sqlserver'
            elif 'sqlite' in engine:
                db_type = 'sqlite'
            elif 'oracle' in engine:
                db_type = 'oracle'
            else:
                db_type = 'unknown'

            config = {
                'db_name': db_name,
                'name': db_config.get('NAME', db_name),
                'db_type': db_type,
                'host': db_config.get('HOST') or 'localhost',
                'port': DatabaseManagerService._normalize_port(db_type, db_config.get('PORT')),
                'database': db_config.get('NAME', ''),
                'user': db_config.get('USER', ''),
                'has_password': bool(db_config.get('PASSWORD', ''))
            }
            configs.append(config)

        return configs

    @staticmethod
    def test_connection(db_name: str = "default") -> dict:
        """
        测试数据库连接
        :param db_name: Django配置的数据库名称
        :return: 测试结果
        """
        try:
            connection = connections[db_name]
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()

            return {
                "success": True,
                "message": "数据库连接成功",
                "db_name": db_name,
                "db_type": connection.settings_dict.get('ENGINE', '')
            }
        except Exception as e:
            logger.error(f"Database connection test failed for {db_name}: {e}")
            return {
                "success": False,
                "message": f"数据库连接失败: {str(e)}",
                "db_name": db_name
            }
