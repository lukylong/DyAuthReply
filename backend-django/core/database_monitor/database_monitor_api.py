#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: database_monitor_api.py
@Desc: 从数据库服务器获取所有数据库列表 - databases = []
"""
# time: 2024/12/21
# file: database_monitor_api.py
# author: AI Assistant

import logging
from typing import List

from django.conf import settings
from ninja import Router

logger = logging.getLogger(__name__)

from .database_collector import DatabaseCollector
from .database_monitor_schema import (
    DatabaseOverviewSchema,
    DatabaseRealtimeStatsSchema,
    DatabaseConnectionTestSchema,
    DatabaseConfigSchema
)

router = Router()


def get_all_databases_from_server(db_config: dict, db_type: str) -> List[str]:
    """从数据库服务器获取所有数据库列表"""
    databases = []

    try:
        if db_type == 'POSTGRESQL':
            import psycopg2
            # 连接到postgres数据库（默认数据库）
            conn = psycopg2.connect(
                host=db_config.get('HOST', 'localhost'),
                port=db_config.get('PORT', 5432),
                user=db_config.get('USER', ''),
                password=db_config.get('PASSWORD', ''),
                database='postgres'  # 连接到默认数据库
            )
            cursor = conn.cursor()
            # 查询所有数据库，排除模板数据库
            cursor.execute("""
                           SELECT datname
                           FROM pg_database
                           WHERE datistemplate = FALSE
                             AND datname NOT IN ('postgres')
                           ORDER BY datname
                           """)
            databases = [row[0] for row in cursor.fetchall()]
            cursor.close()
            conn.close()

        elif db_type == 'MYSQL':
            import pymysql
            conn = pymysql.connect(
                host=db_config.get('HOST', 'localhost'),
                port=db_config.get('PORT', 3306),
                user=db_config.get('USER', ''),
                password=db_config.get('PASSWORD', '')
            )
            cursor = conn.cursor()
            # 查询所有数据库，排除系统数据库
            cursor.execute("""
                           SELECT SCHEMA_NAME
                           FROM information_schema.SCHEMATA
                           WHERE SCHEMA_NAME NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
                           ORDER BY SCHEMA_NAME
                           """)
            databases = [row[0] for row in cursor.fetchall()]
            cursor.close()
            conn.close()

        elif db_type == 'SQLSERVER':
            import pyodbc
            conn_str = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={db_config.get('HOST', 'localhost')},{db_config.get('PORT', 1433)};UID={db_config.get('USER', '')};PWD={db_config.get('PASSWORD', '')}"
            conn = pyodbc.connect(conn_str)
            cursor = conn.cursor()
            # 查询所有数据库，排除系统数据库
            cursor.execute("""
                           SELECT name
                           FROM sys.databases
                           WHERE name NOT IN ('master', 'tempdb', 'model', 'msdb')
                           ORDER BY name
                           """)
            databases = [row[0] for row in cursor.fetchall()]
            cursor.close()
            conn.close()

    except Exception as e:
        logger.error(f"Failed to get databases from server: {e}")
        # 如果获取失败，返回空列表
        return []

    return databases


def get_database_configs() -> List[dict]:
    """从settings中获取数据库配置，并获取服务器上的所有数据库"""
    configs = []
    configured_databases = []  # Django配置中的数据库
    other_databases = []  # 服务器上的其他数据库

    # 获取所有数据库配置
    databases = getattr(settings, 'DATABASES', {})

    for db_key, db_config in databases.items():
        engine = db_config.get('ENGINE', '')

        # 检查数据库类型
        if 'postgresql' in engine:
            db_type = 'POSTGRESQL'
            default_port = 5432
        elif 'mysql' in engine:
            db_type = 'MYSQL'
            default_port = 3306
        elif 'mssql' in engine or 'sqlserver' in engine:
            db_type = 'SQLSERVER'
            default_port = 1433
        elif 'sqlite' in engine:
            # SQLite不支持监控，跳过
            continue
        else:
            # 跳过不支持的数据库类型
            continue

        # 获取基本配置
        host = db_config.get('HOST', 'localhost')
        port = db_config.get('PORT', default_port)
        user = db_config.get('USER', '')
        password = db_config.get('PASSWORD', '')
        configured_db_name = db_config.get('NAME', '')  # Django配置中的数据库名

        # 从服务器获取所有数据库
        all_databases = get_all_databases_from_server(db_config, db_type)

        # 如果获取失败，使用配置中的数据库
        if not all_databases:
            if configured_db_name:
                all_databases = [configured_db_name]

        # 为每个数据库创建配置
        for database_name in all_databases:
            # 生成唯一的db_name（配置key + 数据库名）
            unique_db_name = f"{db_key}_{database_name}" if db_key != 'default' else database_name

            config = {
                'name': f"{database_name}",  # 显示名称就是数据库名
                'db_name': unique_db_name,  # 唯一标识
                'db_type': db_type,
                'host': host,
                'port': port,
                'database': database_name,  # 真实数据库名
                'user': user,
                'password': password,
                'has_password': bool(password),
                'config_key': db_key,  # 原始配置key
                'is_configured': database_name == configured_db_name  # 是否是配置中的数据库
            }

            # 区分配置的数据库和其他数据库
            if database_name == configured_db_name:
                configured_databases.append(config)
            else:
                other_databases.append(config)

    # 配置的数据库放在前面，其他数据库按名称排序
    other_databases.sort(key=lambda x: x['name'].lower())
    configs = configured_databases + other_databases

    return configs


@router.get("/database_monitor/configs", response=List[DatabaseConfigSchema])
def get_database_monitor_configs(request):
    """获取数据库监控配置列表"""
    configs = get_database_configs()
    return [DatabaseConfigSchema(
        name=config['name'],
        db_name=config['db_name'],
        db_type=config['db_type'],
        host=config['host'],
        port=config['port'],
        database=config['database'],
        user=config['user'],
        has_password=config['has_password']
    ) for config in configs]


@router.get("/database_monitor/{db_name}/overview", response=DatabaseOverviewSchema)
def get_database_overview(request, db_name: str):
    """获取数据库概览信息"""
    configs = get_database_configs()
    db_config = next((config for config in configs if config['db_name'] == db_name), None)

    if not db_config:
        raise ValueError(f"Database {db_name} not found")

    collector = DatabaseCollector(
        db_type=db_config['db_type'],
        host=db_config['host'],
        port=db_config['port'],
        user=db_config['user'],
        password=db_config['password'],
        database=db_config['database']
    )

    data = collector.get_all_info(db_name, db_config['name'])
    return DatabaseOverviewSchema(**data)


@router.get("/database_monitor/{db_name}/realtime", response=DatabaseRealtimeStatsSchema)
def get_database_realtime_stats(request, db_name: str):
    """获取数据库实时统计信息"""
    configs = get_database_configs()
    db_config = next((config for config in configs if config['db_name'] == db_name), None)

    if not db_config:
        raise ValueError(f"Database {db_name} not found")

    collector = DatabaseCollector(
        db_type=db_config['db_type'],
        host=db_config['host'],
        port=db_config['port'],
        user=db_config['user'],
        password=db_config['password'],
        database=db_config['database']
    )

    data = collector.get_realtime_stats(db_name)
    return DatabaseRealtimeStatsSchema(**data)


@router.post("/database_monitor/{db_name}/test", response=DatabaseConnectionTestSchema)
def test_database_connection(request, db_name: str):
    """测试数据库连接"""
    configs = get_database_configs()
    db_config = next((config for config in configs if config['db_name'] == db_name), None)

    if not db_config:
        raise ValueError(f"Database {db_name} not found")

    collector = DatabaseCollector(
        db_type=db_config['db_type'],
        host=db_config['host'],
        port=db_config['port'],
        user=db_config['user'],
        password=db_config['password'],
        database=db_config['database']
    )

    result = collector.test_connection()
    return DatabaseConnectionTestSchema(**result)
