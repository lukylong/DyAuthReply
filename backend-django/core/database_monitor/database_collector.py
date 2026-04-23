#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: database_collector.py
@Desc: 递归地序列化数据，处理datetime、decimal等类型
"""
# time: 2024/12/21
# file: database_collector.py
# author: AI Assistant

import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any

logger = logging.getLogger(__name__)

# 数据库驱动导入
try:
    import psycopg2
    import psycopg2.extras

    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

try:
    import pymysql

    PYMYSQL_AVAILABLE = True
except ImportError:
    PYMYSQL_AVAILABLE = False

try:
    import pyodbc

    PYODBC_AVAILABLE = True
except ImportError:
    PYODBC_AVAILABLE = False


def serialize_data(data: Any) -> Any:
    """
    递归地序列化数据，处理datetime、decimal等类型
    """
    import decimal

    if isinstance(data, (datetime,)):
        return data.isoformat()
    elif isinstance(data, decimal.Decimal):
        return float(data)
    elif isinstance(data, (bytes,)):
        try:
            return data.decode('utf-8')
        except UnicodeDecodeError:
            return str(data)
    elif isinstance(data, dict):
        return {serialize_data(k): serialize_data(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [serialize_data(item) for item in data]
    elif isinstance(data, tuple):
        return tuple(serialize_data(item) for item in data)
    else:
        return data


class DatabaseCollector:
    """数据库信息收集器基类"""

    def __init__(self, db_type: str, host: str, port: int,
                 user: str, password: str, database: str, **kwargs):
        self.db_type = db_type.upper()
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.kwargs = kwargs
        self.connection = None

    def connect(self) -> bool:
        """连接数据库"""
        try:
            if self.db_type == 'POSTGRESQL':
                return self._connect_postgresql()
            elif self.db_type == 'MYSQL':
                return self._connect_mysql()
            elif self.db_type == 'SQLSERVER':
                return self._connect_sqlserver()
            else:
                logger.error(f"Unsupported database type: {self.db_type}")
                return False
        except Exception as e:
            logger.error(f"Failed to connect to {self.db_type} database: {e}")
            return False

    def _connect_postgresql(self) -> bool:
        """连接PostgreSQL"""
        if not PSYCOPG2_AVAILABLE:
            logger.error("psycopg2 not available")
            return False

        self.connection = psycopg2.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            connect_timeout=5
        )
        return True

    def _connect_mysql(self) -> bool:
        """连接MySQL"""
        if not PYMYSQL_AVAILABLE:
            logger.error("pymysql not available")
            return False

        self.connection = pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            charset='utf8mb4',
            connect_timeout=5
        )
        return True

    def _connect_sqlserver(self) -> bool:
        """连接SQL Server"""
        if not PYODBC_AVAILABLE:
            logger.error("pyodbc not available")
            return False

        connection_string = (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={self.host},{self.port};"
            f"DATABASE={self.database};"
            f"UID={self.user};"
            f"PWD={self.password};"
            f"Timeout=5;"
        )
        self.connection = pyodbc.connect(connection_string)
        return True

    def disconnect(self):
        """断开连接"""
        if self.connection:
            try:
                self.connection.close()
            except Exception as e:
                logger.error(f"Error disconnecting from database: {e}")
            finally:
                self.connection = None

    def test_connection(self) -> Dict[str, Any]:
        """测试数据库连接"""
        start_time = time.time()
        try:
            if self.connect():
                response_time = (time.time() - start_time) * 1000
                version = self._get_version()
                self.disconnect()
                return {
                    'success': True,
                    'message': '连接成功',
                    'response_time': round(response_time, 2),
                    'version': version,
                    'db_type': self.db_type
                }
            else:
                return {
                    'success': False,
                    'message': '连接失败',
                    'response_time': None,
                    'version': None,
                    'db_type': self.db_type
                }
        except Exception as e:
            return {
                'success': False,
                'message': f'连接错误: {str(e)}',
                'response_time': None,
                'version': None,
                'db_type': self.db_type
            }

    def _get_version(self) -> str:
        """获取数据库版本"""
        try:
            cursor = self.connection.cursor()
            if self.db_type == 'POSTGRESQL':
                cursor.execute("SELECT version()")
                return cursor.fetchone()[0]
            elif self.db_type == 'MYSQL':
                cursor.execute("SELECT VERSION()")
                return cursor.fetchone()[0]
            elif self.db_type == 'SQLSERVER':
                cursor.execute("SELECT @@VERSION")
                return cursor.fetchone()[0]
            return 'Unknown'
        except Exception as e:
            logger.error(f"Error getting database version: {e}")
            return 'Unknown'

    def get_basic_info(self) -> Dict[str, Any]:
        """获取数据库基本信息"""
        if not self.connection:
            if not self.connect():
                return {}

        try:
            info = {
                'db_type': self.db_type,
                'host': self.host,
                'port': self.port,
                'database': self.database,
                'version': self._get_version(),
                'uptime': self._get_uptime(),
                'timezone': self._get_timezone(),
                'charset': self._get_charset(),
            }
            return serialize_data(info)
        except Exception as e:
            logger.error(f"Error getting basic info: {e}")
            return {}

    def _get_uptime(self) -> str:
        """获取数据库运行时间"""
        try:
            cursor = self.connection.cursor()
            if self.db_type == 'POSTGRESQL':
                cursor.execute("SELECT pg_postmaster_start_time()")
                start_time = cursor.fetchone()[0]
                # 确保两个datetime都是offset-aware或都是offset-naive
                from django.utils import timezone
                current_time = timezone.now()
                if start_time.tzinfo is None:
                    start_time = timezone.make_aware(start_time)
                elif current_time.tzinfo is None:
                    current_time = timezone.make_aware(current_time)
                uptime = current_time - start_time
                return str(uptime)
            elif self.db_type == 'MYSQL':
                cursor.execute("SHOW GLOBAL STATUS LIKE 'Uptime'")
                result = cursor.fetchone()
                if result:
                    uptime_seconds = int(result[1])
                    uptime = timedelta(seconds=uptime_seconds)
                    return str(uptime)
            elif self.db_type == 'SQLSERVER':
                cursor.execute("SELECT sqlserver_start_time FROM sys.dm_os_sys_info")
                start_time = cursor.fetchone()[0]
                # 确保时区一致性
                from django.utils import timezone
                current_time = timezone.now()
                if start_time.tzinfo is None:
                    start_time = timezone.make_aware(start_time)
                elif current_time.tzinfo is None:
                    current_time = timezone.make_aware(current_time)
                uptime = current_time - start_time
                return str(uptime)
            return 'Unknown'
        except Exception as e:
            logger.error(f"Error getting uptime: {e}")
            return 'Unknown'

    def _get_timezone(self) -> str:
        """获取数据库时区"""
        try:
            cursor = self.connection.cursor()
            if self.db_type == 'POSTGRESQL':
                cursor.execute("SHOW timezone")
                return cursor.fetchone()[0]
            elif self.db_type == 'MYSQL':
                cursor.execute("SELECT @@global.time_zone")
                return cursor.fetchone()[0]
            elif self.db_type == 'SQLSERVER':
                cursor.execute("SELECT CURRENT_TIMEZONE()")
                return cursor.fetchone()[0]
            return 'Unknown'
        except Exception as e:
            logger.error(f"Error getting timezone: {e}")
            return 'Unknown'

    def _get_charset(self) -> str:
        """获取数据库字符集"""
        try:
            cursor = self.connection.cursor()
            if self.db_type == 'POSTGRESQL':
                cursor.execute("SELECT pg_encoding_to_char(encoding) FROM pg_database WHERE datname = %s",
                               (self.database,))
                return cursor.fetchone()[0]
            elif self.db_type == 'MYSQL':
                cursor.execute("SELECT @@character_set_database")
                return cursor.fetchone()[0]
            elif self.db_type == 'SQLSERVER':
                cursor.execute("SELECT DATABASEPROPERTYEX(DB_NAME(), 'Collation')")
                return cursor.fetchone()[0]
            return 'Unknown'
        except Exception as e:
            logger.error(f"Error getting charset: {e}")
            return 'Unknown'

    def get_connection_info(self) -> Dict[str, Any]:
        """获取连接信息"""
        if not self.connection:
            if not self.connect():
                return {}

        try:
            if self.db_type == 'POSTGRESQL':
                return self._get_postgresql_connections()
            elif self.db_type == 'MYSQL':
                return self._get_mysql_connections()
            elif self.db_type == 'SQLSERVER':
                return self._get_sqlserver_connections()
            return {}
        except Exception as e:
            logger.error(f"Error getting connection info: {e}")
            return {}

    def _get_postgresql_connections(self) -> Dict[str, Any]:
        """获取PostgreSQL连接信息"""
        cursor = self.connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # 总连接数
        cursor.execute("SELECT COUNT(*) AS total_connections FROM pg_stat_activity")
        total_connections = cursor.fetchone()['total_connections']

        # 最大连接数
        cursor.execute("SHOW max_connections")
        max_conn_result = cursor.fetchone()
        if max_conn_result:
            # RealDictRow对象，通过字段名访问
            max_connections = int(max_conn_result['max_connections'])
        else:
            max_connections = 0

        # 活跃连接数
        cursor.execute("SELECT COUNT(*) AS active_connections FROM pg_stat_activity WHERE state = 'active'")
        active_connections = cursor.fetchone()['active_connections']

        # 空闲连接数
        cursor.execute("SELECT COUNT(*) AS idle_connections FROM pg_stat_activity WHERE state = 'idle'")
        idle_connections = cursor.fetchone()['idle_connections']

        return {
            'total_connections': total_connections,
            'max_connections': max_connections,
            'active_connections': active_connections,
            'idle_connections': idle_connections,
            'connection_usage_percent': round((total_connections / max_connections) * 100,
                                              2) if max_connections > 0 else 0.0
        }

    def _get_mysql_connections(self) -> Dict[str, Any]:
        """获取MySQL连接信息"""
        cursor = self.connection.cursor()

        # 总连接数
        cursor.execute("SHOW STATUS LIKE 'Threads_connected'")
        total_connections = int(cursor.fetchone()[1])

        # 最大连接数
        cursor.execute("SHOW VARIABLES LIKE 'max_connections'")
        max_connections = int(cursor.fetchone()[1])

        # 运行中的连接数
        cursor.execute("SHOW STATUS LIKE 'Threads_running'")
        active_connections = int(cursor.fetchone()[1])

        idle_connections = total_connections - active_connections

        return {
            'total_connections': total_connections,
            'max_connections': max_connections,
            'active_connections': active_connections,
            'idle_connections': idle_connections,
            'connection_usage_percent': round((total_connections / max_connections) * 100,
                                              2) if max_connections > 0 else 0.0
        }

    def _get_sqlserver_connections(self) -> Dict[str, Any]:
        """获取SQL Server连接信息"""
        cursor = self.connection.cursor()

        # 总连接数
        cursor.execute("SELECT COUNT(*) FROM sys.dm_exec_sessions WHERE is_user_process = 1")
        total_connections = cursor.fetchone()[0]

        # 最大连接数（SQL Server默认为32767，实际受内存限制）
        cursor.execute("SELECT @@MAX_CONNECTIONS")
        max_connections = cursor.fetchone()[0]

        # 活跃连接数
        cursor.execute("SELECT COUNT(*) FROM sys.dm_exec_requests")
        active_connections = cursor.fetchone()[0]

        idle_connections = total_connections - active_connections

        return {
            'total_connections': total_connections,
            'max_connections': max_connections,
            'active_connections': active_connections,
            'idle_connections': idle_connections,
            'connection_usage_percent': round((total_connections / max_connections) * 100,
                                              2) if max_connections > 0 else 0
        }

    def get_database_size(self) -> Dict[str, Any]:
        """获取数据库大小信息"""
        if not self.connection:
            if not self.connect():
                return {}

        try:
            if self.db_type == 'POSTGRESQL':
                return self._get_postgresql_size()
            elif self.db_type == 'MYSQL':
                return self._get_mysql_size()
            elif self.db_type == 'SQLSERVER':
                return self._get_sqlserver_size()
            return {}
        except Exception as e:
            logger.error(f"Error getting database size: {e}")
            return {}

    def _get_postgresql_size(self) -> Dict[str, Any]:
        """获取PostgreSQL数据库大小"""
        cursor = self.connection.cursor()
        cursor.execute("SELECT pg_database_size(%s)", (self.database,))
        size_bytes = cursor.fetchone()[0]

        return {
            'database_size_bytes': size_bytes,
            'database_size_mb': round(size_bytes / 1024 / 1024, 2),
            'database_size_gb': round(size_bytes / 1024 / 1024 / 1024, 2)
        }

    def _get_mysql_size(self) -> Dict[str, Any]:
        """获取MySQL数据库大小"""
        cursor = self.connection.cursor()
        cursor.execute("""
                       SELECT SUM(data_length + index_length) AS size_bytes
                       FROM information_schema.tables
                       WHERE table_schema = %s
                       """, (self.database,))
        result = cursor.fetchone()
        size_bytes = result[0] if result[0] else 0

        return {
            'database_size_bytes': size_bytes,
            'database_size_mb': round(size_bytes / 1024 / 1024, 2),
            'database_size_gb': round(size_bytes / 1024 / 1024 / 1024, 2)
        }

    def _get_sqlserver_size(self) -> Dict[str, Any]:
        """获取SQL Server数据库大小"""
        cursor = self.connection.cursor()
        cursor.execute("""
                       SELECT SUM(CAST(FILEPROPERTY(name, 'SpaceUsed') AS bigint) * 8192) AS size_bytes
                       FROM sys.database_files
                       """)
        result = cursor.fetchone()
        size_bytes = result[0] if result[0] else 0

        return {
            'database_size_bytes': size_bytes,
            'database_size_mb': round(size_bytes / 1024 / 1024, 2),
            'database_size_gb': round(size_bytes / 1024 / 1024 / 1024, 2)
        }

    def get_performance_stats(self) -> Dict[str, Any]:
        """获取性能统计信息"""
        if not self.connection:
            if not self.connect():
                return {}

        try:
            if self.db_type == 'POSTGRESQL':
                return self._get_postgresql_performance()
            elif self.db_type == 'MYSQL':
                return self._get_mysql_performance()
            elif self.db_type == 'SQLSERVER':
                return self._get_sqlserver_performance()
            return {}
        except Exception as e:
            logger.error(f"Error getting performance stats: {e}")
            return {}

    def _get_postgresql_performance(self) -> Dict[str, Any]:
        """获取PostgreSQL性能统计"""
        cursor = self.connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # 查询数据库级别性能统计
        cursor.execute("""
                       SELECT SUM(numbackends)              AS total_backends
                            , SUM(xact_commit)              AS transactions_commit
                            , SUM(xact_rollback)            AS transactions_rollback
                            , SUM(blks_read)                AS blocks_read
                            , SUM(blks_hit)                 AS blocks_hit
                            , SUM(tup_returned)             AS tuples_returned
                            , SUM(tup_fetched)              AS tuples_fetched
                            , SUM(tup_inserted)             AS tuples_inserted
                            , SUM(tup_updated)              AS tuples_updated
                            , SUM(tup_deleted)              AS tuples_deleted
                            , SUM(temp_files)               AS temp_files
                            , SUM(temp_bytes)               AS temp_bytes
                            , SUM(deadlocks)                AS deadlocks
                            , SUM(checksum_failures)        AS checksum_failures
                            , SUM(blk_read_time)            AS block_read_time
                            , SUM(blk_write_time)           AS block_write_time
                            , SUM(session_time)             AS session_time
                            , SUM(active_time)              AS active_time
                            , SUM(idle_in_transaction_time) AS idle_in_transaction_time
                       FROM pg_stat_database
                       WHERE datname = %s
                       """, (self.database,))

        stats = cursor.fetchone()

        # 获取锁统计
        cursor.execute("""
                       SELECT mode
                            , COUNT(*) AS lock_count
                       FROM pg_locks
                       WHERE database = (SELECT oid FROM pg_database WHERE datname = %s)
                       GROUP BY mode
                       """, (self.database,))

        locks = cursor.fetchall()
        lock_stats = {lock['mode']: lock['lock_count'] for lock in locks}

        # 获取索引使用统计
        cursor.execute("""
                       SELECT SUM(idx_scan)      AS total_index_scans
                            , SUM(idx_tup_read)  AS total_index_tuples_read
                            , SUM(idx_tup_fetch) AS total_index_tuples_fetched
                       FROM pg_stat_user_indexes
                       """)

        index_stats = cursor.fetchone()

        # 获取表空间使用情况
        cursor.execute("""
                       SELECT spcname                                     AS tablespace_name
                            , PG_SIZE_PRETTY(PG_TABLESPACE_SIZE(spcname)) AS tablespace_size
                       FROM pg_tablespace
                       """)

        tablespaces = cursor.fetchall()

        # 计算各种比率
        total_reads = stats['blocks_read'] + stats['blocks_hit']
        cache_hit_ratio = (stats['blocks_hit'] / total_reads * 100) if total_reads > 0 else 0

        total_transactions = stats['transactions_commit'] + stats['transactions_rollback']
        rollback_ratio = (stats['transactions_rollback'] / total_transactions * 100) if total_transactions > 0 else 0

        return {
            # 基础统计
            'total_backends': stats['total_backends'],
            'transactions_commit': stats['transactions_commit'],
            'transactions_rollback': stats['transactions_rollback'],
            'total_transactions': total_transactions,
            'rollback_ratio': round(rollback_ratio, 2),

            # 缓存统计
            'blocks_read': stats['blocks_read'],
            'blocks_hit': stats['blocks_hit'],
            'cache_hit_ratio': round(cache_hit_ratio, 2),

            # 元组操作统计
            'tuples_returned': stats['tuples_returned'],
            'tuples_fetched': stats['tuples_fetched'],
            'tuples_inserted': stats['tuples_inserted'],
            'tuples_updated': stats['tuples_updated'],
            'tuples_deleted': stats['tuples_deleted'],

            # 临时文件统计
            'temp_files': stats['temp_files'] or 0,
            'temp_bytes': stats['temp_bytes'] or 0,
            'temp_size_mb': round((stats['temp_bytes'] or 0) / 1024 / 1024, 2),

            # 错误统计
            'deadlocks': stats['deadlocks'] or 0,
            'checksum_failures': stats['checksum_failures'] or 0,

            # 时间统计（毫秒）
            'block_read_time': round((stats['block_read_time'] or 0), 2),
            'block_write_time': round((stats['block_write_time'] or 0), 2),
            'session_time': round((stats['session_time'] or 0) / 1000, 2),  # 转换为秒
            'active_time': round((stats['active_time'] or 0) / 1000, 2),
            'idle_in_transaction_time': round((stats['idle_in_transaction_time'] or 0) / 1000, 2),

            # 锁统计
            'lock_stats': lock_stats,
            'total_locks': sum(lock_stats.values()),

            # 索引统计
            'total_index_scans': index_stats['total_index_scans'] or 0,
            'total_index_tuples_read': index_stats['total_index_tuples_read'] or 0,
            'total_index_tuples_fetched': index_stats['total_index_tuples_fetched'] or 0,

            # 表空间信息
            'tablespaces': [dict(ts) for ts in tablespaces]
        }

    def _get_mysql_performance(self) -> Dict[str, Any]:
        """获取MySQL性能统计"""
        cursor = self.connection.cursor()

        # 获取各种统计信息
        stats = {}
        status_queries = [
            ('queries', 'Queries'),
            ('connections', 'Connections'),
            ('slow_queries', 'Slow_queries'),
            ('bytes_received', 'Bytes_received'),
            ('bytes_sent', 'Bytes_sent'),
            ('innodb_buffer_pool_reads', 'Innodb_buffer_pool_reads'),
            ('innodb_buffer_pool_read_requests', 'Innodb_buffer_pool_read_requests')
        ]

        for stat_name, mysql_var in status_queries:
            cursor.execute(f"SHOW GLOBAL STATUS LIKE '{mysql_var}'")
            result = cursor.fetchone()
            stats[stat_name] = int(result[1]) if result else 0

        # 计算缓存命中率
        total_reads = stats['innodb_buffer_pool_reads'] + stats['innodb_buffer_pool_read_requests']
        cache_hit_ratio = ((stats['innodb_buffer_pool_read_requests'] - stats['innodb_buffer_pool_reads']) / stats[
            'innodb_buffer_pool_read_requests'] * 100) if stats['innodb_buffer_pool_read_requests'] > 0 else 0

        return {
            'total_queries': stats['queries'],
            'total_connections': stats['connections'],
            'slow_queries': stats['slow_queries'],
            'bytes_received': stats['bytes_received'],
            'bytes_sent': stats['bytes_sent'],
            'cache_hit_ratio': round(cache_hit_ratio, 2)
        }

    def _get_sqlserver_performance(self) -> Dict[str, Any]:
        """获取SQL Server性能统计"""
        cursor = self.connection.cursor()

        # 获取批处理请求/秒
        cursor.execute("""
                       SELECT cntr_value
                       FROM sys.dm_os_performance_counters
                       WHERE counter_name = 'Batch Requests/sec'
                       """)
        batch_requests = cursor.fetchone()[0] if cursor.rowcount > 0 else 0

        # 获取页生存期
        cursor.execute("""
                       SELECT cntr_value
                       FROM sys.dm_os_performance_counters
                       WHERE counter_name = 'Page life expectancy'
                       """)
        page_life_expectancy = cursor.fetchone()[0] if cursor.rowcount > 0 else 0

        # 获取缓冲区命中率
        cursor.execute("""
                       SELECT cntr_value
                       FROM sys.dm_os_performance_counters
                       WHERE counter_name = 'Buffer cache hit ratio'
                       """)
        buffer_hit_ratio = cursor.fetchone()[0] if cursor.rowcount > 0 else 0

        return {
            'batch_requests_per_sec': batch_requests,
            'page_life_expectancy': page_life_expectancy,
            'buffer_cache_hit_ratio': buffer_hit_ratio / 100 if buffer_hit_ratio else 0
        }

    def get_table_stats(self) -> List[Dict[str, Any]]:
        """获取表统计信息"""
        if not self.connection:
            if not self.connect():
                return []

        try:
            if self.db_type == 'POSTGRESQL':
                return self._get_postgresql_tables()
            elif self.db_type == 'MYSQL':
                return self._get_mysql_tables()
            elif self.db_type == 'SQLSERVER':
                return self._get_sqlserver_tables()
            return []
        except Exception as e:
            logger.error(f"Error getting table stats: {e}")
            return []

    def _get_postgresql_tables(self) -> List[Dict[str, Any]]:
        """获取PostgreSQL表统计"""
        cursor = self.connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # 获取所有表的详细统计信息
        cursor.execute("""
                       SELECT st.schemaname
                            , st.relname                                                         AS tablename
                            , st.n_tup_ins                                                       AS inserts
                            , st.n_tup_upd                                                       AS updates
                            , st.n_tup_del                                                       AS deletes
                            , st.n_live_tup                                                      AS live_tuples
                            , st.n_dead_tup                                                      AS dead_tuples
                            , st.seq_scan                                                        AS sequential_scans
                            , st.seq_tup_read                                                    AS sequential_tuples_read
                            , st.idx_scan                                                        AS index_scans
                            , st.idx_tup_fetch                                                   AS index_tuples_fetched
                            , st.n_tup_hot_upd                                                   AS hot_updates
                            , st.vacuum_count
                            , st.autovacuum_count
                            , st.analyze_count
                            , st.autoanalyze_count
                            , st.last_vacuum
                            , st.last_autovacuum
                            , st.last_analyze
                            , st.last_autoanalyze
                            , COALESCE(PG_SIZE_PRETTY(PG_RELATION_SIZE(c.oid)), '0 bytes')       AS size
                            , COALESCE(PG_RELATION_SIZE(c.oid), 0)                               AS size_bytes
                            , COALESCE(PG_SIZE_PRETTY(PG_TOTAL_RELATION_SIZE(c.oid)), '0 bytes') AS total_size
                            , COALESCE(PG_TOTAL_RELATION_SIZE(c.oid), 0)                         AS total_size_bytes
                            , CASE
                                  WHEN st.n_live_tup > 0
                                      THEN ROUND((st.n_dead_tup::numeric / st.n_live_tup::numeric) * 100, 2)
                                  ELSE 0
                           END                                                                   AS bloat_ratio
                            , c.reltuples::bigint                                                AS estimated_rows
                            , c.relpages                                                         AS pages
                       FROM pg_stat_user_tables st
                                JOIN pg_class c ON c.relname = st.relname AND c.relnamespace = (SELECT oid
                                                                                                FROM pg_namespace
                                                                                                WHERE nspname = st.schemaname)
                       WHERE c.relkind = 'r' -- 只包含普通表
                       ORDER BY COALESCE(PG_TOTAL_RELATION_SIZE(c.oid), 0) DESC
                       """)

        return [dict(row) for row in cursor.fetchall()]

    def _get_mysql_tables(self) -> List[Dict[str, Any]]:
        """获取MySQL表统计"""
        cursor = self.connection.cursor()
        cursor.execute("""
                       SELECT table_name
                            , table_rows
                            , data_length
                            , index_length
                            , (data_length + index_length) AS total_size
                            , auto_increment
                       FROM information_schema.tables
                       WHERE table_schema = %s
                       ORDER BY (data_length + index_length) DESC
                       LIMIT 10
                       """, (self.database,))

        tables = []
        for row in cursor.fetchall():
            tables.append({
                'table_name': row[0],
                'table_rows': row[1] or 0,
                'data_length': row[2] or 0,
                'index_length': row[3] or 0,
                'total_size': row[4] or 0,
                'auto_increment': row[5] or 0
            })
        return tables

    def _get_sqlserver_tables(self) -> List[Dict[str, Any]]:
        """获取SQL Server表统计"""
        cursor = self.connection.cursor()
        cursor.execute("""
                       SELECT TOP 10
                t.name AS table_name, i.rows AS table_rows
                            , SUM(a.total_pages) * 8 AS total_size_kb
                            , SUM(a.used_pages) * 8  AS used_size_kb
                            , SUM(a.data_pages) * 8  AS data_size_kb
                       FROM sys.tables t
                                INNER JOIN sys.indexes i ON t.object_id = i.object_id
                                INNER JOIN sys.partitions p ON i.object_id = p.object_id AND i.index_id = p.index_id
                                INNER JOIN sys.allocation_units a ON p.partition_id = a.container_id
                       WHERE t.name NOT LIKE 'dt%'
                         AND i.object_id > 255
                       GROUP BY t.name, i.rows
                       ORDER BY SUM(a.total_pages) DESC
                       """)

        tables = []
        for row in cursor.fetchall():
            tables.append({
                'table_name': row[0],
                'table_rows': row[1] or 0,
                'total_size_kb': row[2] or 0,
                'used_size_kb': row[3] or 0,
                'data_size_kb': row[4] or 0
            })
        return tables

    def get_all_info(self, connection_id: str, connection_name: str) -> Dict[str, Any]:
        """获取所有数据库监控信息"""
        timestamp = datetime.now().isoformat()

        try:
            if not self.connect():
                return {
                    'connection_id': connection_id,
                    'connection_name': connection_name,
                    'status': 'disconnected',
                    'basic_info': {},
                    'connection_info': {},
                    'database_size': {},
                    'performance_stats': {},
                    'table_stats': [],
                    'timestamp': timestamp
                }

            data = {
                'connection_id': connection_id,
                'connection_name': connection_name,
                'status': 'connected',
                'basic_info': self.get_basic_info(),
                'connection_info': self.get_connection_info(),
                'database_size': self.get_database_size(),
                'performance_stats': self.get_performance_stats(),
                'table_stats': self.get_table_stats(),
                'timestamp': timestamp
            }
            return serialize_data(data)
        except Exception as e:
            logger.error(f"Error getting database all info: {e}")
            return {
                'connection_id': connection_id,
                'connection_name': connection_name,
                'status': 'error',
                'basic_info': {},
                'connection_info': {},
                'database_size': {},
                'performance_stats': {},
                'table_stats': [],
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
                    'connections_used': 0,
                    'connection_usage_percent': 0.0,
                    'database_size_mb': 0.0,
                    'cache_hit_ratio': 0.0,
                    'active_connections': 0,
                    'timestamp': timestamp
                }

            connection_info = self.get_connection_info()
            database_size = self.get_database_size()
            performance_stats = self.get_performance_stats()

            data = {
                'connection_id': connection_id,
                'connections_used': connection_info.get('total_connections', 0),
                'connection_usage_percent': connection_info.get('connection_usage_percent', 0.0),
                'database_size_mb': database_size.get('database_size_mb', 0.0),
                'cache_hit_ratio': performance_stats.get('cache_hit_ratio', 0.0),
                'active_connections': connection_info.get('active_connections', 0),
                'timestamp': timestamp
            }
            return serialize_data(data)
        except Exception as e:
            logger.error(f"Error getting database realtime stats: {e}")
            return {
                'connection_id': connection_id,
                'connections_used': 0,
                'connection_usage_percent': 0.0,
                'database_size_mb': 0.0,
                'cache_hit_ratio': 0.0,
                'active_connections': 0,
                'timestamp': timestamp
            }
        finally:
            self.disconnect()
