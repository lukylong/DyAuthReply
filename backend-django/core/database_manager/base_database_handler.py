#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: base_database_handler.py
@Desc: 数据库处理器基类 - 定义所有数据库操作的抽象接口，支持PostgreSQL、MySQL、SQL Server等
"""
"""
数据库处理器基类
定义所有数据库操作的抽象接口，支持PostgreSQL、MySQL、SQL Server等
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from django.db import connections


class BaseDatabaseHandler(ABC):
    """数据库处理器抽象基类"""

    def __init__(self, db_name: str = "default"):
        """
        初始化数据库处理器
        :param db_name: Django配置的数据库名称
        """
        self.db_name = db_name
        self.connection = connections[db_name]
        self.db_type = self._get_db_type()

    def _get_db_type(self) -> str:
        """获取数据库类型"""
        engine = self.connection.settings_dict.get('ENGINE', '')
        if 'postgresql' in engine:
            return 'postgresql'
        elif 'mysql' in engine:
            return 'mysql'
        elif 'sql_server' in engine or 'mssql' in engine:
            return 'sqlserver'
        elif 'sqlite' in engine:
            return 'sqlite'
        elif 'oracle' in engine:
            return 'oracle'
        else:
            return 'unknown'

    def _execute_query(self, query: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """执行查询并返回字典列表"""
        with self.connection.cursor() as cursor:
            cursor.execute(query, params or ())
            columns = [col[0] for col in cursor.description] if cursor.description else []
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def _execute_command(self, command: str, params: Optional[tuple] = None) -> int:
        """执行命令并返回影响行数"""
        with self.connection.cursor() as cursor:
            cursor.execute(command, params or ())
            return cursor.rowcount

    # ============ 数据库管理 ============
    @abstractmethod
    def get_databases(self) -> List[Dict[str, Any]]:
        """获取所有数据库"""
        pass

    @abstractmethod
    def create_database(self, name: str, **kwargs) -> bool:
        """创建数据库"""
        pass

    @abstractmethod
    def drop_database(self, name: str) -> bool:
        """删除数据库"""
        pass

    # ============ 表管理 ============
    @abstractmethod
    def get_tables(self, database: Optional[str] = None, schema_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取表列表"""
        pass

    @abstractmethod
    def get_table_structure(self, table_name: str, database: Optional[str] = None, schema_name: Optional[str] = None) -> \
    Dict[str, Any]:
        """获取表结构详情"""
        pass

    @abstractmethod
    def get_table_columns(self, table_name: str, schema_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取表字段信息"""
        pass

    @abstractmethod
    def get_table_indexes(self, table_name: str, schema_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取表索引信息"""
        pass

    @abstractmethod
    def get_table_constraints(self, table_name: str, schema_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取表约束信息"""
        pass

    @abstractmethod
    def get_table_ddl(self, table_name: str, schema_name: Optional[str] = None) -> str:
        """获取表的DDL语句"""
        pass

    # ============ 视图管理 ============
    @abstractmethod
    def get_views(self, database: Optional[str] = None, schema_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取视图列表"""
        pass

    @abstractmethod
    def get_view_structure(self, view_name: str, schema_name: Optional[str] = None) -> Dict[str, Any]:
        """获取视图结构详情"""
        pass

    @abstractmethod
    def get_view_definition(self, view_name: str, schema_name: Optional[str] = None) -> str:
        """获取视图定义SQL"""
        pass

    @abstractmethod
    def get_view_dependencies(self, view_name: str, schema_name: Optional[str] = None) -> List[str]:
        """获取视图依赖的表列表"""
        pass

    # ============ DDL操作 ============
    @abstractmethod
    def execute_ddl(self, sql: str, database: Optional[str] = None, schema: Optional[str] = None) -> Dict[str, Any]:
        """
        执行DDL语句（CREATE, ALTER, DROP等）
        
        Args:
            sql: DDL SQL语句
            database: 数据库名（可选）
            schema: Schema名（可选）
            
        Returns:
            {
                'success': bool,
                'message': str,
                'affected_rows': int
            }
        """
        pass

    # ============ 数据查询 ============
    def query_data(self, table_name: str, schema_name: Optional[str] = None,
                   page: int = 1, page_size: int = 20,
                   where: Optional[str] = None, order_by: Optional[str] = None) -> Dict[str, Any]:
        """查询表数据（分页）"""
        # 构建完整表名
        full_table_name = self._build_table_name(table_name, schema_name)

        # 1. 获取总数
        count_query = f"SELECT COUNT(*) as total FROM {full_table_name}"
        if where:
            count_query += f" WHERE {where}"
        total_result = self._execute_query(count_query)
        total = total_result[0]['total'] if total_result else 0

        # 2. 查询数据
        offset = (page - 1) * page_size
        data_query = f"SELECT * FROM {full_table_name}"
        if where:
            data_query += f" WHERE {where}"
        if order_by:
            data_query += f" ORDER BY {order_by}"
        data_query += f" LIMIT {page_size} OFFSET {offset}"

        rows = self._execute_query(data_query)
        columns = list(rows[0].keys()) if rows else []

        return {
            "columns": columns,
            "rows": rows,
            "total": total,
            "page": page,
            "page_size": page_size
        }

    def _build_table_name(self, table_name: str, schema_name: Optional[str] = None) -> str:
        """构建完整的表名"""
        if schema_name:
            return f"{schema_name}.{table_name}"
        return table_name

    # ============ SQL执行 ============
    def execute_sql(self, sql: str, is_query: bool = True) -> Dict[str, Any]:
        """执行自定义SQL"""
        import time
        start_time = time.time()

        try:
            if is_query:
                # SELECT查询
                rows = self._execute_query(sql)
                columns = list(rows[0].keys()) if rows else []
                execution_time = time.time() - start_time

                return {
                    "success": True,
                    "message": f"查询成功，返回 {len(rows)} 行",
                    "columns": columns,
                    "rows": rows,
                    "affected_rows": None,
                    "execution_time": round(execution_time, 3)
                }
            else:
                # INSERT/UPDATE/DELETE
                affected_rows = self._execute_command(sql)
                execution_time = time.time() - start_time

                return {
                    "success": True,
                    "message": f"执行成功，影响 {affected_rows} 行",
                    "columns": None,
                    "rows": None,
                    "affected_rows": affected_rows,
                    "execution_time": round(execution_time, 3)
                }
        except Exception as e:
            execution_time = time.time() - start_time
            return {
                "success": False,
                "message": str(e),
                "columns": None,
                "rows": None,
                "affected_rows": None,
                "execution_time": round(execution_time, 3)
            }

    # ============ 数据操作 ============
    def insert_data(self, table_name: str, data: Dict[str, Any],
                    schema_name: Optional[str] = None) -> Dict[str, Any]:
        """插入数据"""
        full_table_name = self._build_table_name(table_name, schema_name)
        columns = list(data.keys())
        values = list(data.values())
        placeholders = ', '.join(['%s'] * len(values))

        query = f"""
        INSERT INTO {full_table_name} ({', '.join(columns)})
        VALUES ({placeholders})
        """

        try:
            affected_rows = self._execute_command(query, tuple(values))
            return {
                "success": True,
                "message": "插入成功",
                "affected_rows": affected_rows
            }
        except Exception as e:
            return {
                "success": False,
                "message": str(e),
                "affected_rows": 0
            }

    def update_data(self, table_name: str, data: Dict[str, Any], where: str,
                    schema_name: Optional[str] = None) -> Dict[str, Any]:
        """更新数据"""
        full_table_name = self._build_table_name(table_name, schema_name)
        set_clause = ', '.join([f"{k} = %s" for k in data.keys()])
        query = f"""
        UPDATE {full_table_name}
        SET {set_clause}
        WHERE {where}
        """

        try:
            affected_rows = self._execute_command(query, tuple(data.values()))
            return {
                "success": True,
                "message": f"更新成功，影响 {affected_rows} 行",
                "affected_rows": affected_rows
            }
        except Exception as e:
            return {
                "success": False,
                "message": str(e),
                "affected_rows": 0
            }

    def delete_data(self, table_name: str, where: str,
                    schema_name: Optional[str] = None) -> Dict[str, Any]:
        """删除数据"""
        full_table_name = self._build_table_name(table_name, schema_name)
        query = f"DELETE FROM {full_table_name} WHERE {where}"

        try:
            affected_rows = self._execute_command(query)
            return {
                "success": True,
                "message": f"删除成功，影响 {affected_rows} 行",
                "affected_rows": affected_rows
            }
        except Exception as e:
            return {
                "success": False,
                "message": str(e),
                "affected_rows": 0
            }
