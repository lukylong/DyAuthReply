#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: sqlserver_handler.py
@Desc: SQL Server 数据库处理器 - 实现SQL Server特定的数据库操作
"""
"""
SQL Server 数据库处理器
实现SQL Server特定的数据库操作
"""
import logging
from typing import Any, Dict, List, Optional

from .base_database_handler import BaseDatabaseHandler

logger = logging.getLogger(__name__)


class SQLServerHandler(BaseDatabaseHandler):
    """SQL Server 数据库处理器"""

    # ============ 数据库管理 ============
    def get_databases(self) -> List[Dict[str, Any]]:
        """获取所有数据库"""
        query = """
                SELECT name
                     , SUSER_SNAME(owner_sid)                                                as owner
                     , collation_name                                                        as collation
                     , (SELECT COUNT(*)
                        FROM sys.tables t
                        WHERE t.type = 'U'
                          AND SCHEMA_NAME(t.schema_id) NOT IN ('sys', 'INFORMATION_SCHEMA')) as tables_count
                FROM sys.databases
                WHERE name NOT IN ('master', 'tempdb', 'model', 'msdb')
                ORDER BY name; \
                """
        databases = self._execute_query(query)

        # 获取数据库大小
        for db in databases:
            size_query = """
                         SELECT SUM(size) * 8 / 1024 as size_mb
                         FROM sys.master_files
                         WHERE database_id = DB_ID(?) \
                         """
            size_result = self._execute_query(size_query, (db['name'],))
            size_mb = size_result[0]['size_mb'] if size_result and size_result[0]['size_mb'] else 0
            db['size_bytes'] = int(size_mb * 1024 * 1024) if size_mb else 0

            # 格式化大小
            if db['size_bytes'] >= 1073741824:
                db['size'] = f"{db['size_bytes'] / 1073741824:.2f} GB"
            elif db['size_bytes'] >= 1048576:
                db['size'] = f"{db['size_bytes'] / 1048576:.2f} MB"
            else:
                db['size'] = f"{db['size_bytes'] / 1024:.2f} KB"

        return databases

    def create_database(self, name: str, collation: str = "SQL_Latin1_General_CP1_CI_AS", **kwargs) -> bool:
        """创建数据库"""
        try:
            query = f"CREATE DATABASE [{name}] COLLATE {collation}"
            self._execute_command(query)
            logger.info(f"Database {name} created successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to create database {name}: {e}")
            raise

    def drop_database(self, name: str) -> bool:
        """删除数据库"""
        try:
            # SQL Server需要先设置为单用户模式
            query = f"""
            ALTER DATABASE [{name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
            DROP DATABASE [{name}];
            """
            self._execute_command(query)
            logger.info(f"Database {name} dropped successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to drop database {name}: {e}")
            raise

    # ============ 表管理 ============
    def get_tables(self, database: Optional[str] = None, schema_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取表列表"""
        query = """
                SELECT SCHEMA_NAME(t.schema_id)                            as schema_name
                     , t.name                                              as table_name
                     , 'BASE TABLE'                                        as table_type
                     , p.rows                                              as row_count
                     , SUM(a.total_pages) * 8 * 1024                       as total_size_bytes
                     , SUM(a.used_pages) * 8 * 1024                        as data_length
                     , (SUM(a.total_pages) - SUM(a.used_pages)) * 8 * 1024 as index_length
                     , CAST(ep.value AS NVARCHAR(MAX))                     as description
                FROM sys.tables t
                         INNER JOIN sys.indexes i ON t.object_id = i.object_id
                         INNER JOIN sys.partitions p ON i.object_id = p.object_id AND i.index_id = p.index_id
                         INNER JOIN sys.allocation_units a ON p.partition_id = a.container_id
                         LEFT JOIN sys.extended_properties ep ON t.object_id = ep.major_id
                    AND ep.minor_id = 0
                    AND ep.name = 'MS_Description'
                WHERE t.type = 'U'
                  AND SCHEMA_NAME(t.schema_id) = ?
                GROUP BY t.schema_id, t.name, p.rows, ep.value
                ORDER BY t.name; \
                """
        tables = self._execute_query(query, (schema_name,))

        # 格式化大小
        for table in tables:
            total_bytes = table.get('total_size_bytes', 0) or 0
            if total_bytes >= 1073741824:
                table['total_size'] = f"{total_bytes / 1073741824:.2f} GB"
            elif total_bytes >= 1048576:
                table['total_size'] = f"{total_bytes / 1048576:.2f} MB"
            elif total_bytes >= 1024:
                table['total_size'] = f"{total_bytes / 1024:.2f} KB"
            else:
                table['total_size'] = f"{total_bytes} bytes"

        return tables

    def get_table_columns(self, table_name: str, schema_name: Optional[str] = "dbo") -> List[Dict[str, Any]]:
        """获取表字段信息"""
        query = """
                SELECT c.name                                               as column_name
                     , TYPE_NAME(c.user_type_id)                            as data_type
                     , c.is_nullable
                     , OBJECT_DEFINITION(c.default_object_id)               as column_default
                     , c.max_length                                         as character_maximum_length
                     , c.precision                                          as numeric_precision
                     , c.scale                                              as numeric_scale
                     , c.column_id                                          as ordinal_position
                     , CASE WHEN pk.column_id IS NOT NULL THEN 1 ELSE 0 END as is_primary_key
                     , CASE WHEN uq.column_id IS NOT NULL THEN 1 ELSE 0 END as is_unique
                     , CAST(ep.value AS NVARCHAR(MAX))                      as description
                FROM sys.columns c
                         INNER JOIN sys.tables t ON c.object_id = t.object_id
                         LEFT JOIN (SELECT ic.object_id, ic.column_id
                                    FROM sys.index_columns ic
                                             INNER JOIN sys.indexes i
                                                        ON ic.object_id = i.object_id AND ic.index_id = i.index_id
                                    WHERE i.is_primary_key = 1) pk
                                   ON c.object_id = pk.object_id AND c.column_id = pk.column_id
                         LEFT JOIN (SELECT ic.object_id, ic.column_id
                                    FROM sys.index_columns ic
                                             INNER JOIN sys.indexes i
                                                        ON ic.object_id = i.object_id AND ic.index_id = i.index_id
                                    WHERE i.is_unique = 1
                                      AND i.is_primary_key = 0) uq
                                   ON c.object_id = uq.object_id AND c.column_id = uq.column_id
                         LEFT JOIN sys.extended_properties ep ON c.object_id = ep.major_id
                    AND c.column_id = ep.minor_id
                    AND ep.name = 'MS_Description'
                WHERE SCHEMA_NAME(t.schema_id) = ?
                  AND t.name = ?
                ORDER BY c.column_id; \
                """
        return self._execute_query(query, (schema_name, table_name))

    def get_table_indexes(self, table_name: str, schema_name: Optional[str] = "dbo") -> List[Dict[str, Any]]:
        """获取表索引信息"""
        query = """
                SELECT i.name                                                               as index_name
                     , i.type_desc                                                          as index_type
                     , STRING_AGG(c.name, ', ') WITHIN GROUP (ORDER BY ic.key_ordinal)      as columns
                     , i.is_unique
                     , i.is_primary_key                                                     as is_primary
                     , 'INDEX ' + i.name + ' ON ' + SCHEMA_NAME(t.schema_id) + '.' + t.name as definition
                FROM sys.indexes i
                         INNER JOIN sys.tables t ON i.object_id = t.object_id
                         INNER JOIN sys.index_columns ic ON i.object_id = ic.object_id AND i.index_id = ic.index_id
                         INNER JOIN sys.columns c ON ic.object_id = c.object_id AND ic.column_id = c.column_id
                WHERE SCHEMA_NAME(t.schema_id) = ?
                  AND t.name = ?
                GROUP BY i.name, i.type_desc, i.is_unique, i.is_primary_key, t.schema_id, t.name
                ORDER BY i.name; \
                """
        return self._execute_query(query, (schema_name, table_name))

    def get_table_constraints(self, table_name: str, schema_name: Optional[str] = "dbo") -> List[Dict[str, Any]]:
        """获取表约束信息"""
        query = """
                SELECT kc.name                              as constraint_name
                     , kc.type_desc                         as constraint_type
                     , STRING_AGG(c.name, ', ')             as columns
                     , OBJECT_DEFINITION(kc.object_id)      as definition
                     , OBJECT_NAME(fk.referenced_object_id) as referenced_table
                     , NULL                                 as referenced_columns
                FROM sys.key_constraints kc
                         INNER JOIN sys.tables t ON kc.parent_object_id = t.object_id
                         INNER JOIN sys.index_columns ic
                                    ON kc.parent_object_id = ic.object_id AND kc.unique_index_id = ic.index_id
                         INNER JOIN sys.columns c ON ic.object_id = c.object_id AND ic.column_id = c.column_id
                         LEFT JOIN sys.foreign_keys fk ON kc.object_id = fk.object_id
                WHERE SCHEMA_NAME(t.schema_id) = ?
                  AND t.name = ?
                GROUP BY kc.name, kc.type_desc, kc.object_id, fk.referenced_object_id
                ORDER BY kc.type_desc, kc.name; \
                """
        return self._execute_query(query, (schema_name, table_name))

    def get_table_structure(self, table_name: str, database: Optional[str] = None,
                            schema_name: Optional[str] = "dbo") -> Dict[str, Any]:
        """获取表结构详情"""
        # 1. 表基本信息
        tables = self.get_tables(database=database, schema_name=schema_name)
        table_info = next((t for t in tables if t['table_name'] == table_name), None)

        if not table_info:
            raise ValueError(f"Table {schema_name}.{table_name} not found")

        # 2. 字段信息
        columns = self.get_table_columns(table_name, schema_name)

        # 3. 索引信息
        indexes = self.get_table_indexes(table_name, schema_name)

        # 4. 约束信息
        constraints = self.get_table_constraints(table_name, schema_name)

        return {
            "table_info": table_info,
            "columns": columns,
            "indexes": indexes,
            "constraints": constraints
        }

    def get_table_ddl(self, table_name: str, schema_name: Optional[str] = None) -> str:
        """获取表的DDL语句（SQL Server简化版）"""
        if not schema_name:
            schema_name = "dbo"

        try:
            # 获取表结构信息
            columns = self.get_table_columns(table_name, schema_name)

            # 构建简化的CREATE TABLE语句
            full_table_name = f"[{schema_name}].[{table_name}]"
            ddl = f"CREATE TABLE {full_table_name} (\n"

            column_defs = []
            for col in columns:
                col_name = col['column_name']
                data_type = col['data_type'].upper()

                # 处理长度
                if col.get('character_maximum_length') and col['character_maximum_length'] > 0:
                    if col['character_maximum_length'] == -1:
                        data_type = f"{data_type}(MAX)"
                    else:
                        data_type = f"{data_type}({col['character_maximum_length']})"
                elif col.get('numeric_precision'):
                    if col.get('numeric_scale'):
                        data_type = f"{data_type}({col['numeric_precision']},{col['numeric_scale']})"
                    else:
                        data_type = f"{data_type}({col['numeric_precision']})"

                col_def = f"  [{col_name}] {data_type}"

                # NOT NULL
                if not col.get('is_nullable', True):
                    col_def += " NOT NULL"

                # DEFAULT
                if col.get('column_default'):
                    col_def += f" DEFAULT {col['column_default']}"

                # IDENTITY
                if col.get('is_identity'):
                    col_def += " IDENTITY(1,1)"

                column_defs.append(col_def)

            ddl += ",\n".join(column_defs)
            ddl += "\n);"

            # 添加注释说明
            ddl = "-- SQL Server DDL (简化版)\n" + ddl

            return ddl
        except Exception as e:
            logger.error(f"Failed to generate DDL for table {schema_name}.{table_name}: {e}")
            return f"-- 获取DDL失败: {str(e)}"

    # ============ 视图管理 ============
    def get_views(self, database: Optional[str] = None, schema_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取视图列表"""
        if not schema_name:
            schema_name = "dbo"

        query = """
                SELECT TABLE_NAME      as view_name
                     , TABLE_SCHEMA    as schema_name
                     , VIEW_DEFINITION as view_definition
                     , IS_UPDATABLE    as is_updatable
                     , CHECK_OPTION    as check_option
                FROM INFORMATION_SCHEMA.VIEWS
                WHERE TABLE_SCHEMA = ?
                ORDER BY TABLE_NAME \
                """
        result = self._execute_query(query, (schema_name,))

        # 转换is_updatable为布尔值
        for row in result:
            row['is_updatable'] = row.get('is_updatable') == 'YES'

        return result

    def get_view_structure(self, view_name: str, schema_name: Optional[str] = None) -> Dict[str, Any]:
        """获取视图结构详情"""
        if not schema_name:
            schema_name = "dbo"

        # 1. 视图基本信息
        view_query = """
                     SELECT TABLE_NAME      as view_name
                          , TABLE_SCHEMA    as schema_name
                          , VIEW_DEFINITION as view_definition
                          , IS_UPDATABLE    as is_updatable
                          , CHECK_OPTION    as check_option
                     FROM INFORMATION_SCHEMA.VIEWS
                     WHERE TABLE_SCHEMA = ?
                       AND TABLE_NAME = ? \
                     """
        view_info = self._execute_query(view_query, (schema_name, view_name))
        if not view_info:
            raise ValueError(f"View {schema_name}.{view_name} not found")

        view_info = view_info[0]
        view_info['is_updatable'] = view_info.get('is_updatable') == 'YES'

        # 2. 视图列信息
        columns_query = """
                        SELECT COLUMN_NAME      as column_name
                             , DATA_TYPE        as data_type
                             , IS_NULLABLE      as is_nullable
                             , ORDINAL_POSITION as ordinal_position
                        FROM INFORMATION_SCHEMA.COLUMNS
                        WHERE TABLE_SCHEMA = ?
                          AND TABLE_NAME = ?
                        ORDER BY ORDINAL_POSITION \
                        """
        columns = self._execute_query(columns_query, (schema_name, view_name))

        # 转换is_nullable为布尔值
        for col in columns:
            col['is_nullable'] = col.get('is_nullable') == 'YES'

        # 3. 获取视图定义SQL
        definition_sql = self.get_view_definition(view_name, schema_name)

        # 4. 获取依赖的表
        dependencies = self.get_view_dependencies(view_name, schema_name)

        return {
            "view_info": view_info,
            "columns": columns,
            "dependencies": dependencies,
            "definition_sql": definition_sql
        }

    def get_view_definition(self, view_name: str, schema_name: Optional[str] = None) -> str:
        """获取视图定义SQL"""
        if not schema_name:
            schema_name = "dbo"

        try:
            query = """
                    SELECT m.definition
                    FROM sys.sql_modules m
                             INNER JOIN sys.views v ON m.object_id = v.object_id
                             INNER JOIN sys.schemas s ON v.schema_id = s.schema_id
                    WHERE s.name = ?
                      AND v.name = ? \
                    """
            result = self._execute_query(query, (schema_name, view_name))
            if result and len(result) > 0:
                return result[0].get('definition', '')
            return f"-- 无法获取视图 {view_name} 的定义"
        except Exception as e:
            logger.error(f"Failed to get view definition for {schema_name}.{view_name}: {e}")
            return f"-- 获取视图定义失败: {str(e)}"

    def get_view_dependencies(self, view_name: str, schema_name: Optional[str] = None) -> List[str]:
        """获取视图依赖的表列表"""
        if not schema_name:
            schema_name = "dbo"

        query = """
                SELECT DISTINCT SCHEMA_NAME(o.schema_id) + '.' + o.name as table_name
                FROM sys.sql_expression_dependencies d
                         INNER JOIN sys.views v ON d.referencing_id = v.object_id
                         INNER JOIN sys.schemas s ON v.schema_id = s.schema_id
                         INNER JOIN sys.objects o ON d.referenced_id = o.object_id
                WHERE s.name = ?
                  AND v.name = ?
                  AND o.type IN ('U', 'V') -- 表或视图
                ORDER BY table_name \
                """
        result = self._execute_query(query, (schema_name, view_name))
        return [row['table_name'] for row in result]

    # ============ DDL操作 ============
    def execute_ddl(self, sql: str, database: Optional[str] = None, schema: Optional[str] = None) -> Dict[str, Any]:
        """执行DDL语句"""
        try:
            with self.connection.cursor() as cursor:
                # 如果指定了数据库，切换到该数据库
                if database:
                    cursor.execute(f"USE [{database}]")

                # 执行DDL语句
                cursor.execute(sql)

                # 获取影响的行数
                affected_rows = cursor.rowcount if cursor.rowcount >= 0 else 0

                return {
                    'success': True,
                    'message': 'DDL执行成功',
                    'affected_rows': affected_rows
                }
        except Exception as e:
            logger.error(f"Failed to execute DDL: {e}")
            return {
                'success': False,
                'message': f'DDL执行失败: {str(e)}',
                'affected_rows': 0
            }
