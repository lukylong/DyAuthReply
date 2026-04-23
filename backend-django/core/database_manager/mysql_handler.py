#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: mysql_handler.py
@Desc: MySQL 数据库处理器 - 实现MySQL特定的数据库操作
"""
"""
MySQL 数据库处理器
实现MySQL特定的数据库操作
"""
import logging
from typing import Any, Dict, List, Optional

from .base_database_handler import BaseDatabaseHandler

logger = logging.getLogger(__name__)


class MySQLHandler(BaseDatabaseHandler):
    """MySQL 数据库处理器"""

    # ============ 数据库管理 ============
    def get_databases(self) -> List[Dict[str, Any]]:
        """获取所有数据库"""
        query = """
                SELECT SCHEMA_NAME                       as name
                     , DEFAULT_CHARACTER_SET_NAME        as encoding
                     , DEFAULT_COLLATION_NAME            as collation
                     , (SELECT COUNT(*)
                        FROM information_schema.TABLES
                        WHERE TABLE_SCHEMA = SCHEMA_NAME
                          AND TABLE_TYPE = 'BASE TABLE') as tables_count
                FROM information_schema.SCHEMATA
                WHERE SCHEMA_NAME NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
                ORDER BY SCHEMA_NAME; \
                """
        databases = self._execute_query(query)

        # 获取数据库大小
        for db in databases:
            size_query = """
                         SELECT ROUND(SUM(data_length + index_length), 2) as size_bytes
                         FROM information_schema.TABLES
                         WHERE table_schema = %s; \
                         """
            size_result = self._execute_query(size_query, (db['name'],))
            size_bytes = size_result[0]['size_bytes'] if size_result and size_result[0]['size_bytes'] else 0
            db['size_bytes'] = int(size_bytes) if size_bytes else 0

            # 格式化大小
            if db['size_bytes'] >= 1073741824:  # GB
                db['size'] = f"{db['size_bytes'] / 1073741824:.2f} GB"
            elif db['size_bytes'] >= 1048576:  # MB
                db['size'] = f"{db['size_bytes'] / 1048576:.2f} MB"
            elif db['size_bytes'] >= 1024:  # KB
                db['size'] = f"{db['size_bytes'] / 1024:.2f} KB"
            else:
                db['size'] = f"{db['size_bytes']} bytes"

        return databases

    def create_database(self, name: str, charset: str = "utf8mb4",
                        collation: str = "utf8mb4_unicode_ci", **kwargs) -> bool:
        """创建数据库"""
        try:
            query = f"CREATE DATABASE `{name}` CHARACTER SET {charset} COLLATE {collation}"
            self._execute_command(query)
            logger.info(f"Database {name} created successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to create database {name}: {e}")
            raise

    def drop_database(self, name: str) -> bool:
        """删除数据库"""
        try:
            query = f"DROP DATABASE `{name}`"
            self._execute_command(query)
            logger.info(f"Database {name} dropped successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to drop database {name}: {e}")
            raise

    # ============ 表管理 ============
    def get_tables(self, database: Optional[str] = None, schema_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取表列表"""
        # MySQL中database和schema是同义词，优先使用database
        if not schema_name:
            schema_name = database or self.connection.settings_dict['NAME']

        query = """
                SELECT TABLE_SCHEMA                 as schema_name
                     , TABLE_NAME                   as table_name
                     , TABLE_TYPE                   as table_type
                     , TABLE_ROWS                   as row_count
                     , DATA_LENGTH                  as data_length
                     , INDEX_LENGTH                 as index_length
                     , (DATA_LENGTH + INDEX_LENGTH) as total_size_bytes
                     , TABLE_COMMENT                as description
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = %s
                  AND TABLE_TYPE = 'BASE TABLE'
                ORDER BY TABLE_NAME; \
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

    def get_table_columns(self, table_name: str, schema_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取表字段信息"""
        if not schema_name:
            schema_name = self.connection.settings_dict['NAME']

        query = """
                SELECT COLUMN_NAME              as column_name
                     , DATA_TYPE                as data_type
                     , IS_NULLABLE = 'YES'      as is_nullable
                     , COLUMN_DEFAULT           as column_default
                     , CHARACTER_MAXIMUM_LENGTH as character_maximum_length
                     , NUMERIC_PRECISION        as numeric_precision
                     , NUMERIC_SCALE            as numeric_scale
                     , ORDINAL_POSITION         as ordinal_position
                     , COLUMN_KEY = 'PRI'       as is_primary_key
                     , COLUMN_KEY = 'UNI'       as is_unique
                     , COLUMN_COMMENT           as description
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s
                  AND TABLE_NAME = %s
                ORDER BY ORDINAL_POSITION; \
                """
        return self._execute_query(query, (schema_name, table_name))

    def get_table_indexes(self, table_name: str, schema_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取表索引信息"""
        if not schema_name:
            schema_name = self.connection.settings_dict['NAME']

        query = """
                SELECT INDEX_NAME                                      as index_name
                     , INDEX_TYPE                                      as index_type
                     , GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) as columns
                     , NON_UNIQUE = 0                                  as is_unique
                     , INDEX_NAME = 'PRIMARY'                          as is_primary
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = %s
                  AND TABLE_NAME = %s
                GROUP BY INDEX_NAME, INDEX_TYPE, NON_UNIQUE
                ORDER BY INDEX_NAME; \
                """
        indexes = self._execute_query(query, (schema_name, table_name))

        # 添加definition字段（MySQL没有直接的定义，构造一个）
        for idx in indexes:
            idx['definition'] = f"INDEX {idx['index_name']} ({idx['columns']})"

        return indexes

    def get_table_constraints(self, table_name: str, schema_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取表约束信息"""
        if not schema_name:
            schema_name = self.connection.settings_dict['NAME']

        query = """
                SELECT CONSTRAINT_NAME           as constraint_name
                     , CONSTRAINT_TYPE           as constraint_type
                     , GROUP_CONCAT(COLUMN_NAME) as columns
                     , REFERENCED_TABLE_NAME     as referenced_table
                     , NULL                      as referenced_columns
                FROM information_schema.KEY_COLUMN_USAGE
                         JOIN information_schema.TABLE_CONSTRAINTS USING (CONSTRAINT_NAME, TABLE_SCHEMA, TABLE_NAME)
                WHERE TABLE_SCHEMA = %s
                  AND TABLE_NAME = %s
                GROUP BY CONSTRAINT_NAME, CONSTRAINT_TYPE, REFERENCED_TABLE_NAME
                ORDER BY CONSTRAINT_TYPE, CONSTRAINT_NAME; \
                """
        constraints = self._execute_query(query, (schema_name, table_name))

        # 添加definition字段
        for const in constraints:
            const['definition'] = f"{const['constraint_type']} ({const['columns']})"

        return constraints

    def get_table_structure(self, table_name: str, database: Optional[str] = None, schema_name: Optional[str] = None) -> \
    Dict[str, Any]:
        """获取表结构详情"""
        # MySQL中database和schema是同义词，优先使用database
        if not schema_name:
            schema_name = database or self.connection.settings_dict['NAME']

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
        """获取表的DDL语句（MySQL使用SHOW CREATE TABLE）"""
        # MySQL的SHOW CREATE TABLE不需要schema，因为已经在连接时指定了database
        query = f"SHOW CREATE TABLE `{table_name}`"

        try:
            result = self._execute_query(query)
            if result and len(result) > 0:
                # SHOW CREATE TABLE返回两列：Table和Create Table
                # 第二列就是DDL语句
                ddl = result[0].get('Create Table', '')
                return ddl
            return f"-- 无法获取表 {table_name} 的DDL"
        except Exception as e:
            return f"-- 获取DDL失败: {str(e)}"

    # ============ 视图管理 ============
    def get_views(self, database: Optional[str] = None, schema_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取视图列表"""
        # MySQL中database和schema是同义词，优先使用database
        if not schema_name:
            schema_name = database or self.connection.settings_dict['NAME']

        query = """
                SELECT TABLE_NAME      as view_name
                     , TABLE_SCHEMA    as schema_name
                     , VIEW_DEFINITION as view_definition
                     , IS_UPDATABLE    as is_updatable
                     , CHECK_OPTION    as check_option
                FROM information_schema.VIEWS
                WHERE TABLE_SCHEMA = %s
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
            schema_name = self.connection.settings_dict['NAME']

        # 1. 视图基本信息
        view_query = """
                     SELECT TABLE_NAME      as view_name
                          , TABLE_SCHEMA    as schema_name
                          , VIEW_DEFINITION as view_definition
                          , IS_UPDATABLE    as is_updatable
                          , CHECK_OPTION    as check_option
                     FROM information_schema.VIEWS
                     WHERE TABLE_SCHEMA = %s
                       AND TABLE_NAME = %s \
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
                             , COLUMN_COMMENT   as description
                        FROM information_schema.COLUMNS
                        WHERE TABLE_SCHEMA = %s
                          AND TABLE_NAME = %s
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
        try:
            query = f"SHOW CREATE VIEW `{view_name}`"
            result = self._execute_query(query)
            if result and len(result) > 0:
                # SHOW CREATE VIEW返回多列，Create View是我们需要的
                ddl = result[0].get('Create View', '')
                return ddl
            return f"-- 无法获取视图 {view_name} 的定义"
        except Exception as e:
            return f"-- 获取视图定义失败: {str(e)}"

    def get_view_dependencies(self, view_name: str, schema_name: Optional[str] = None) -> List[str]:
        """获取视图依赖的表列表"""
        if not schema_name:
            schema_name = self.connection.settings_dict['NAME']

        query = """
                SELECT DISTINCT REFERENCED_TABLE_NAME as table_name
                FROM information_schema.VIEW_TABLE_USAGE
                WHERE VIEW_SCHEMA = %s
                  AND VIEW_NAME = %s
                  AND REFERENCED_TABLE_NAME IS NOT NULL
                ORDER BY REFERENCED_TABLE_NAME \
                """
        result = self._execute_query(query, (schema_name, view_name))
        return [row['table_name'] for row in result]

    # ============ DDL操作 ============
    def execute_ddl(self, sql: str, database: Optional[str] = None, schema: Optional[str] = None) -> Dict[str, Any]:
        """执行DDL语句"""
        try:
            with self.connection.cursor() as cursor:
                # MySQL中database和schema是同义词，优先使用database
                db_name = database or schema

                # 如果指定了数据库，切换到该数据库
                if db_name:
                    cursor.execute(f"USE `{db_name}`")

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
