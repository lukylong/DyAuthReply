#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: postgresql_handler.py
@Desc: PostgreSQL 数据库处理器 - 实现PostgreSQL特定的数据库操作
"""
"""
PostgreSQL 数据库处理器
实现PostgreSQL特定的数据库操作
"""
import logging
from typing import Any, Dict, List, Optional

from .base_database_handler import BaseDatabaseHandler
from .postgresql_ddl_generator import PostgreSQLDDLGenerator

logger = logging.getLogger(__name__)


class PostgreSQLHandler(BaseDatabaseHandler):
    """PostgreSQL 数据库处理器"""

    def _execute_on_database(self, database: str, query_func, *args, **kwargs):
        """在指定数据库上执行查询函数"""
        import psycopg2
        from psycopg2.extras import RealDictCursor

        conn_params = {
            'host': self.connection.settings_dict.get('HOST'),
            'port': self.connection.settings_dict.get('PORT'),
            'user': self.connection.settings_dict.get('USER'),
            'password': self.connection.settings_dict.get('PASSWORD'),
            'database': database,
        }

        try:
            temp_conn = psycopg2.connect(**conn_params)

            # 临时保存当前连接
            original_conn = self.connection

            # 创建一个临时的connection对象用于查询
            class TempConnection:
                def __init__(self, conn):
                    self.real_conn = conn
                    self.settings_dict = original_conn.settings_dict.copy()
                    self.settings_dict['NAME'] = database

                def cursor(self):
                    return self.real_conn.cursor(cursor_factory=RealDictCursor)

            # 替换连接
            self.connection = TempConnection(temp_conn)

            # 执行查询函数
            result = query_func(*args, **kwargs)

            # 恢复原连接
            self.connection = original_conn

            temp_conn.close()

            return result
        except Exception as e:
            logger.error(f"Failed to execute query on database {database}: {e}")
            # 确保恢复原连接
            if 'original_conn' in locals():
                self.connection = original_conn
            raise

    # ============ 数据库管理 ============
    def get_databases(self) -> List[Dict[str, Any]]:
        """获取所有数据库"""
        query = """
                SELECT d.datname                                                         as name
                     , pg_catalog.pg_get_userbyid(d.datdba)                              as owner
                     , pg_catalog.pg_encoding_to_char(d.encoding)                        as encoding
                     , d.datcollate                                                      as collation
                     , pg_catalog.pg_size_pretty(pg_catalog.pg_database_size(d.datname)) as size
                     , pg_catalog.pg_database_size(d.datname)                            as size_bytes
                     , (SELECT count(*)
                        FROM pg_catalog.pg_tables
                        WHERE schemaname NOT IN ('pg_catalog', 'information_schema'))    as tables_count
                     , pg_catalog.shobj_description(d.oid, 'pg_database')                as description
                FROM pg_catalog.pg_database d
                WHERE d.datistemplate = false
                ORDER BY d.datname; \
                """
        return self._execute_query(query)

    def create_database(self, name: str, owner: Optional[str] = None,
                        encoding: str = "UTF8", template: str = "template0", **kwargs) -> bool:
        """创建数据库"""
        try:
            with self.connection.cursor() as cursor:
                old_autocommit = self.connection.autocommit
                try:
                    self.connection.autocommit = True
                    query = f'CREATE DATABASE "{name}" ENCODING \'{encoding}\' TEMPLATE {template}'
                    if owner:
                        query += f' OWNER "{owner}"'
                    cursor.execute(query)
                    logger.info(f"Database {name} created successfully")
                    return True
                finally:
                    self.connection.autocommit = old_autocommit
        except Exception as e:
            logger.error(f"Failed to create database {name}: {e}")
            raise

    def drop_database(self, name: str) -> bool:
        """删除数据库"""
        try:
            with self.connection.cursor() as cursor:
                old_autocommit = self.connection.autocommit
                try:
                    self.connection.autocommit = True
                    # 先断开所有连接
                    cursor.execute(f"""
                        SELECT pg_terminate_backend(pg_stat_activity.pid)
                        FROM pg_stat_activity
                        WHERE pg_stat_activity.datname = '{name}'
                        AND pid <> pg_backend_pid();
                    """)
                    cursor.execute(f'DROP DATABASE "{name}"')
                    logger.info(f"Database {name} dropped successfully")
                    return True
                finally:
                    self.connection.autocommit = old_autocommit
        except Exception as e:
            logger.error(f"Failed to drop database {name}: {e}")
            raise

    # ============ Schema管理 ============
    def get_schemas(self, database: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取所有schema"""
        # 如果指定了数据库，需要切换连接
        if database and database != self.connection.settings_dict.get('NAME'):
            # 创建临时连接到指定数据库
            import psycopg2
            conn_params = {
                'host': self.connection.settings_dict.get('HOST'),
                'port': self.connection.settings_dict.get('PORT'),
                'user': self.connection.settings_dict.get('USER'),
                'password': self.connection.settings_dict.get('PASSWORD'),
                'database': database,
            }

            try:
                temp_conn = psycopg2.connect(**conn_params)
                cursor = temp_conn.cursor()

                query = """
                        SELECT schema_name                        as name
                             , schema_owner                       as owner
                             , (SELECT count(*)
                                FROM information_schema.tables
                                WHERE table_schema = schema_name) as tables_count
                        FROM information_schema.schemata
                        WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
                        ORDER BY schema_name; \
                        """

                cursor.execute(query)
                columns = [desc[0] for desc in cursor.description]
                results = [dict(zip(columns, row)) for row in cursor.fetchall()]

                cursor.close()
                temp_conn.close()

                return results
            except Exception as e:
                logger.error(f"Failed to get schemas from database {database}: {e}")
                raise
        else:
            # 使用当前连接
            query = """
                    SELECT schema_name                        as name
                         , schema_owner                       as owner
                         , (SELECT count(*)
                            FROM information_schema.tables
                            WHERE table_schema = schema_name) as tables_count
                    FROM information_schema.schemata
                    WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
                    ORDER BY schema_name; \
                    """
            return self._execute_query(query)

    # ============ 表管理 ============
    def get_tables(self, database: Optional[str] = None, schema_name: Optional[str] = "public") -> List[Dict[str, Any]]:
        """获取表列表"""
        # 如果指定了数据库，需要切换连接
        if database and database != self.connection.settings_dict.get('NAME'):
            return self._execute_on_database(database, self._get_tables_query, schema_name)
        else:
            return self._get_tables_query(schema_name)

    def _get_tables_query(self, schema_name: Optional[str] = "public") -> List[Dict[str, Any]]:
        """执行获取表列表的查询"""
        query = """
                SELECT schemaname                                                             as schema_name
                     , tablename                                                              as table_name
                     , 'BASE TABLE'                                                           as table_type
                     , pg_size_pretty(pg_total_relation_size(schemaname || '.' || tablename)) as total_size
                     , pg_total_relation_size(schemaname || '.' || tablename)                 as total_size_bytes
                     , pg_size_pretty(pg_relation_size(schemaname || '.' || tablename))       as table_size
                     , pg_relation_size(schemaname || '.' || tablename)                       as table_size_bytes
                     , pg_size_pretty(pg_total_relation_size(schemaname || '.' || tablename) -
                                      pg_relation_size(schemaname || '.' || tablename))       as indexes_size
                     , (pg_total_relation_size(schemaname || '.' || tablename) -
                        pg_relation_size(schemaname || '.' || tablename))                     as indexes_size_bytes
                     , obj_description((schemaname || '.' || tablename)::regclass)            as description
                FROM pg_catalog.pg_tables
                WHERE schemaname = %s
                ORDER BY tablename; \
                """
        tables = self._execute_query(query, (schema_name,))

        # 获取行数
        for table in tables:
            try:
                count_query = f'SELECT count(*) as row_count FROM "{schema_name}"."{table["table_name"]}"'
                result = self._execute_query(count_query)
                table['row_count'] = result[0]['row_count'] if result else 0
            except Exception as e:
                logger.warning(f"Failed to get row count for {table['table_name']}: {e}")
                table['row_count'] = None

        return tables

    def get_table_columns(self, table_name: str, schema_name: Optional[str] = "public") -> List[Dict[str, Any]]:
        """获取表字段信息"""
        query = """
                SELECT c.column_name
                     , c.data_type
                     , c.is_nullable = 'YES'                                                                  as is_nullable
                     , c.column_default
                     , c.character_maximum_length
                     , c.numeric_precision
                     , c.numeric_scale
                     , c.ordinal_position
                     , EXISTS(SELECT 1
                              FROM information_schema.table_constraints tc
                                       JOIN information_schema.key_column_usage kcu
                                            ON tc.constraint_name = kcu.constraint_name
                              WHERE tc.table_schema = c.table_schema
                                AND tc.table_name = c.table_name
                                AND kcu.column_name = c.column_name
                                AND tc.constraint_type = 'PRIMARY KEY')                                       as is_primary_key
                     , EXISTS(SELECT 1
                              FROM information_schema.table_constraints tc
                                       JOIN information_schema.key_column_usage kcu
                                            ON tc.constraint_name = kcu.constraint_name
                              WHERE tc.table_schema = c.table_schema
                                AND tc.table_name = c.table_name
                                AND kcu.column_name = c.column_name
                                AND tc.constraint_type = 'UNIQUE')                                            as is_unique
                     , col_description((c.table_schema || '.' || c.table_name)::regclass,
                                       c.ordinal_position)                                                    as description
                FROM information_schema.columns c
                WHERE c.table_schema = %s
                  AND c.table_name = %s
                ORDER BY c.ordinal_position; \
                """
        return self._execute_query(query, (schema_name, table_name))

    def get_table_indexes(self, table_name: str, schema_name: Optional[str] = "public") -> List[Dict[str, Any]]:
        """获取表索引信息"""
        query = """
                SELECT i.indexname                                                       as index_name
                     , am.amname                                                         as index_type
                     , array_to_string(array_agg(a.attname ORDER BY k.ordinality), ', ') as columns
                     , i.indexdef                                                        as definition
                     , ix.indisunique                                                    as is_unique
                     , ix.indisprimary                                                   as is_primary
                FROM pg_indexes i
                         JOIN pg_class c ON c.relname = i.tablename AND c.relnamespace = (SELECT oid
                                                                                          FROM pg_namespace
                                                                                          WHERE nspname = i.schemaname)
                         JOIN pg_index ix ON ix.indexrelid = (SELECT oid
                                                              FROM pg_class
                                                              WHERE relname = i.indexname
                                                                AND relnamespace = (SELECT oid
                                                                                    FROM pg_namespace
                                                                                    WHERE nspname = i.schemaname))
                         JOIN pg_am am ON am.oid = (SELECT relam
                                                    FROM pg_class
                                                    WHERE relname = i.indexname
                                                      AND relnamespace = (SELECT oid
                                                                          FROM pg_namespace
                                                                          WHERE nspname = i.schemaname))
                         CROSS JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ordinality)
                         JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
                WHERE i.schemaname = %s
                  AND i.tablename = %s
                GROUP BY i.indexname, am.amname, i.indexdef, ix.indisunique, ix.indisprimary
                ORDER BY i.indexname; \
                """
        return self._execute_query(query, (schema_name, table_name))

    def get_table_constraints(self, table_name: str, schema_name: Optional[str] = "public") -> List[Dict[str, Any]]:
        """获取表约束信息"""
        query = """
                SELECT tc.constraint_name
                     , tc.constraint_type
                     , array_to_string(array_agg(DISTINCT kcu.column_name), ', ') as columns
                     , pg_get_constraintdef(
                        (SELECT oid
                         FROM pg_constraint
                         WHERE conname = tc.constraint_name
                           AND connamespace = (SELECT oid FROM pg_namespace WHERE nspname = %s))
                       )                                                          as definition
                     , ccu.table_name                                             as referenced_table
                     , array_to_string(array_agg(DISTINCT ccu.column_name), ', ') as referenced_columns
                FROM information_schema.table_constraints tc
                         LEFT JOIN information_schema.key_column_usage kcu
                                   ON tc.constraint_name = kcu.constraint_name
                                       AND tc.table_schema = kcu.table_schema
                         LEFT JOIN information_schema.constraint_column_usage ccu
                                   ON tc.constraint_name = ccu.constraint_name
                                       AND tc.table_schema = ccu.constraint_schema
                WHERE tc.table_schema = %s
                  AND tc.table_name = %s
                GROUP BY tc.constraint_name, tc.constraint_type, tc.table_schema, ccu.table_name
                ORDER BY tc.constraint_type, tc.constraint_name; \
                """
        return self._execute_query(query, (schema_name, schema_name, table_name))

    def get_table_structure(self, table_name: str, database: Optional[str] = None,
                            schema_name: Optional[str] = "public") -> Dict[str, Any]:
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

    # ============ 数据库统计 ============
    def get_database_size(self, database_name: Optional[str] = None) -> Dict[str, Any]:
        """获取数据库大小"""
        if not database_name:
            database_name = self.connection.settings_dict['NAME']

        query = """
        SELECT 
            pg_database_size(%s) as size_bytes,
            pg_size_pretty(pg_database_size(%s)) as size
        """
        result = self._execute_query(query, (database_name, database_name))
        return result[0] if result else {}

    def get_table_stats(self, schema_name: Optional[str] = "public") -> Dict[str, Any]:
        """获取表统计信息"""
        query = """
                SELECT count(*)                                                                    as total_tables
                     , pg_size_pretty(sum(pg_total_relation_size(schemaname || '.' || tablename))) as total_size
                     , sum(pg_total_relation_size(schemaname || '.' || tablename))                 as total_size_bytes
                FROM pg_catalog.pg_tables
                WHERE schemaname = %s; \
                """
        result = self._execute_query(query, (schema_name,))
        return result[0] if result else {}

    def get_table_ddl(self, table_name: str, schema_name: Optional[str] = None) -> str:
        """获取表的DDL语句（PostgreSQL需要手动构建）"""
        if not schema_name:
            schema_name = "public"

        try:
            # 获取表结构信息
            columns = self.get_table_columns(table_name, schema_name)
            indexes = self.get_table_indexes(table_name, schema_name)
            constraints = self.get_table_constraints(table_name, schema_name)

            # 使用DDL生成器生成DDL
            ddl = PostgreSQLDDLGenerator.generate_table_ddl(
                table_name=table_name,
                schema_name=schema_name,
                columns=columns,
                indexes=indexes,
                constraints=constraints
            )

            return ddl
        except Exception as e:
            logger.error(f"Failed to generate DDL for table {schema_name}.{table_name}: {e}")
            return f"-- 获取DDL失败: {str(e)}"

    # ============ 视图管理 ============
    def get_views(self, database: Optional[str] = None, schema_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取视图列表（包括普通视图和物化视图）"""
        if not schema_name:
            schema_name = "public"

        # 如果指定了数据库，需要切换连接
        if database and database != self.connection.settings_dict.get('NAME'):
            return self._execute_on_database(database, self._get_views_query, schema_name)
        else:
            return self._get_views_query(schema_name)

    def _get_views_query(self, schema_name: Optional[str] = "public") -> List[Dict[str, Any]]:
        """执行获取视图列表的查询"""
        # 查询普通视图
        query = """
                SELECT table_name   as view_name
                     , table_schema as schema_name
                     , view_definition
                     , is_updatable
                     , check_option
                     , 'VIEW'       as view_type
                FROM information_schema.views
                WHERE table_schema = %s

                UNION ALL

                -- 查询物化视图
                SELECT matviewname         as view_name
                     , schemaname          as schema_name
                     , definition          as view_definition
                     , 'NO'                as is_updatable
                     , NULL                as check_option
                     , 'MATERIALIZED VIEW' as view_type
                FROM pg_matviews
                WHERE schemaname = %s

                ORDER BY view_name \
                """
        result = self._execute_query(query, (schema_name, schema_name))

        # 转换is_updatable为布尔值
        for row in result:
            row['is_updatable'] = row.get('is_updatable') == 'YES'

        return result

    def get_view_structure(self, view_name: str, schema_name: Optional[str] = None) -> Dict[str, Any]:
        """获取视图结构详情（包括物化视图）"""
        if not schema_name:
            schema_name = "public"

        # 1. 先尝试查询普通视图
        view_query = """
                     SELECT table_name   as view_name
                          , table_schema as schema_name
                          , view_definition
                          , is_updatable
                          , check_option
                          , 'VIEW'       as view_type
                     FROM information_schema.views
                     WHERE table_schema = %s
                       AND table_name = %s \
                     """
        view_info = self._execute_query(view_query, (schema_name, view_name))

        # 如果不是普通视图，尝试查询物化视图
        if not view_info:
            matview_query = """
                            SELECT matviewname         as view_name
                                 , schemaname          as schema_name
                                 , definition          as view_definition
                                 , 'NO'                as is_updatable
                                 , NULL                as check_option
                                 , 'MATERIALIZED VIEW' as view_type
                            FROM pg_matviews
                            WHERE schemaname = %s
                              AND matviewname = %s \
                            """
            view_info = self._execute_query(matview_query, (schema_name, view_name))

        if not view_info:
            raise ValueError(f"View or Materialized View {schema_name}.{view_name} not found")

        view_info = view_info[0]
        view_info['is_updatable'] = view_info.get('is_updatable') == 'YES'
        view_type = view_info.get('view_type', 'VIEW')

        # 2. 视图列信息
        # 先尝试从information_schema.columns查询
        columns_query = """
                        SELECT column_name
                             , data_type
                             , is_nullable
                             , ordinal_position
                             , col_description((table_schema || '.' || table_name)::regclass::oid,
                                               ordinal_position) as description
                        FROM information_schema.columns
                        WHERE table_schema = %s
                          AND table_name = %s
                        ORDER BY ordinal_position \
                        """
        columns = self._execute_query(columns_query, (schema_name, view_name))

        # 如果查询不到（可能是物化视图），尝试从pg_attribute查询
        if not columns and view_type == "MATERIALIZED VIEW":
            pg_columns_query = """
                               SELECT a.attname                                       as column_name
                                    , pg_catalog.format_type(a.atttypid, a.atttypmod) as data_type
                                    , CASE WHEN a.attnotnull THEN 'NO' ELSE 'YES' END as is_nullable
                                    , a.attnum                                        as ordinal_position
                                    , pg_catalog.col_description(c.oid, a.attnum)     as description
                               FROM pg_catalog.pg_attribute a
                                        JOIN pg_catalog.pg_class c ON a.attrelid = c.oid
                                        JOIN pg_catalog.pg_namespace n ON c.relnamespace = n.oid
                               WHERE c.relname = %s
                                 AND n.nspname = %s
                                 AND a.attnum > 0
                                 AND NOT a.attisdropped
                               ORDER BY a.attnum \
                               """
            columns = self._execute_query(pg_columns_query, (view_name, schema_name))

        # 转换is_nullable为布尔值
        for col in columns:
            col['is_nullable'] = col.get('is_nullable') == 'YES'

        # 3. 获取视图定义SQL（传递视图类型）
        definition_sql = self.get_view_definition(view_name, schema_name, view_type)

        # 4. 获取依赖的表
        dependencies = self.get_view_dependencies(view_name, schema_name)

        return {
            "view_info": view_info,
            "columns": columns,
            "dependencies": dependencies,
            "definition_sql": definition_sql
        }

    def get_view_definition(self, view_name: str, schema_name: Optional[str] = None, view_type: str = "VIEW") -> str:
        """获取视图定义SQL（包括物化视图）"""
        if not schema_name:
            schema_name = "public"

        try:
            if view_type == "MATERIALIZED VIEW":
                # 物化视图：从pg_matviews获取
                matview_query = """
                                SELECT definition
                                FROM pg_matviews
                                WHERE schemaname = %s
                                  AND matviewname = %s \
                                """
                result = self._execute_query(matview_query, (schema_name, view_name))
                if result and len(result) > 0:
                    definition = result[0].get('definition', '')
                    # 构建完整的CREATE MATERIALIZED VIEW语句
                    ddl = f"CREATE MATERIALIZED VIEW \"{schema_name}\".\"{view_name}\" AS\n{definition}"
                    return ddl
            else:
                # 普通视图：使用pg_get_viewdef
                query = """
                SELECT pg_get_viewdef(%s::regclass, true) as definition
                """
                result = self._execute_query(query, (f"{schema_name}.{view_name}",))
                if result and len(result) > 0:
                    definition = result[0].get('definition', '')
                    # 构建完整的CREATE VIEW语句
                    ddl = f"CREATE OR REPLACE VIEW \"{schema_name}\".\"{view_name}\" AS\n{definition}"
                    return ddl
        except Exception as e:
            logger.error(f"Failed to get view definition for {schema_name}.{view_name}: {e}")

        return f"-- 无法获取视图 {view_name} 的定义"

    def get_view_dependencies(self, view_name: str, schema_name: Optional[str] = None) -> List[str]:
        """获取视图依赖的表列表（包括物化视图）"""
        if not schema_name:
            schema_name = "public"

        query = """
                SELECT DISTINCT ref_nsp.nspname || '.' || ref_cl.relname as table_name
                FROM pg_depend d
                         JOIN pg_rewrite r ON r.oid = d.objid
                         JOIN pg_class v ON v.oid = r.ev_class
                         JOIN pg_namespace v_nsp ON v_nsp.oid = v.relnamespace
                         JOIN pg_class ref_cl ON ref_cl.oid = d.refobjid
                         JOIN pg_namespace ref_nsp ON ref_nsp.oid = ref_cl.relnamespace
                WHERE v.relkind IN ('v', 'm')           -- 'v' = 普通视图, 'm' = 物化视图
                  AND v_nsp.nspname = %s
                  AND v.relname = %s
                  AND ref_cl.relkind IN ('r', 'v', 'm') -- 表、视图或物化视图
                  AND d.deptype = 'n'                   -- normal dependency
                ORDER BY table_name \
                """
        result = self._execute_query(query, (schema_name, view_name))
        return [row['table_name'] for row in result]

    # ============ DDL操作 ============
    def execute_ddl(self, sql: str, database: Optional[str] = None, schema: Optional[str] = None) -> Dict[str, Any]:
        """执行DDL语句"""
        try:
            with self.connection.cursor() as cursor:
                # 如果指定了schema，设置search_path
                if schema:
                    cursor.execute(f"SET search_path TO {schema}, public")

                # 执行DDL语句
                cursor.execute(sql)

                # 获取影响的行数（对于DDL通常为0或-1）
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
