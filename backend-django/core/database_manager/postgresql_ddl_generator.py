#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: postgresql_ddl_generator.py
@Desc: PostgreSQL DDL生成器 - 由于PostgreSQL没有像MySQL的SHOW CREATE TABLE命令，需要手动构建DDL
"""
"""
PostgreSQL DDL生成器
由于PostgreSQL没有像MySQL的SHOW CREATE TABLE命令，需要手动构建DDL
"""
from typing import List, Dict, Any, Optional


class PostgreSQLDDLGenerator:
    """PostgreSQL DDL生成器"""

    @staticmethod
    def generate_table_ddl(
            table_name: str,
            schema_name: str,
            columns: List[Dict[str, Any]],
            indexes: List[Dict[str, Any]],
            constraints: List[Dict[str, Any]]
    ) -> str:
        """生成完整的表DDL"""
        ddl_parts = []

        # 1. CREATE TABLE语句
        create_table = PostgreSQLDDLGenerator._generate_create_table(
            table_name, schema_name, columns
        )
        ddl_parts.append(create_table)

        # 2. 表注释
        table_comment = PostgreSQLDDLGenerator._generate_table_comment(
            table_name, schema_name, columns
        )
        if table_comment:
            ddl_parts.append(table_comment)

        # 3. 列注释
        column_comments = PostgreSQLDDLGenerator._generate_column_comments(
            table_name, schema_name, columns
        )
        if column_comments:
            ddl_parts.extend(column_comments)

        # 4. 索引
        index_ddls = PostgreSQLDDLGenerator._generate_indexes(
            table_name, schema_name, indexes
        )
        if index_ddls:
            ddl_parts.extend(index_ddls)

        return '\n\n'.join(ddl_parts)

    @staticmethod
    def _generate_create_table(
            table_name: str,
            schema_name: str,
            columns: List[Dict[str, Any]]
    ) -> str:
        """生成CREATE TABLE语句"""
        full_table_name = f'"{schema_name}"."{table_name}"'

        # 字段定义
        column_defs = []
        primary_keys = []

        for col in columns:
            col_name = col['column_name']
            data_type = col['data_type'].upper()

            # 处理字符类型的长度
            if col.get('character_maximum_length'):
                data_type = f"{data_type}({col['character_maximum_length']})"
            # 处理数值类型的精度
            elif col.get('numeric_precision'):
                if col.get('numeric_scale'):
                    data_type = f"{data_type}({col['numeric_precision']},{col['numeric_scale']})"
                else:
                    data_type = f"{data_type}({col['numeric_precision']})"

            # 构建列定义
            col_def = f'  "{col_name}" {data_type}'

            # NOT NULL
            if not col.get('is_nullable', True):
                col_def += ' NOT NULL'

            # DEFAULT
            if col.get('column_default'):
                col_def += f" DEFAULT {col['column_default']}"

            column_defs.append(col_def)

            # 收集主键
            if col.get('is_primary_key'):
                primary_keys.append(f'"{col_name}"')

        # 添加主键约束
        if primary_keys:
            column_defs.append(f'  PRIMARY KEY ({", ".join(primary_keys)})')

        # 组装CREATE TABLE
        ddl = f'CREATE TABLE {full_table_name} (\n'
        ddl += ',\n'.join(column_defs)
        ddl += '\n);'

        return ddl

    @staticmethod
    def _generate_table_comment(
            table_name: str,
            schema_name: str,
            columns: List[Dict[str, Any]]
    ) -> Optional[str]:
        """生成表注释"""
        # 从第一个列的信息中获取表注释（如果有的话）
        # 注意：这里假设表注释存储在某个地方，实际可能需要单独查询
        return None

    @staticmethod
    def _generate_column_comments(
            table_name: str,
            schema_name: str,
            columns: List[Dict[str, Any]]
    ) -> List[str]:
        """生成列注释"""
        comments = []
        full_table_name = f'"{schema_name}"."{table_name}"'

        for col in columns:
            if col.get('description'):
                comment = f"COMMENT ON COLUMN {full_table_name}.\"{col['column_name']}\" IS '{col['description']}';"
                comments.append(comment)

        return comments

    @staticmethod
    def _generate_indexes(
            table_name: str,
            schema_name: str,
            indexes: List[Dict[str, Any]]
    ) -> List[str]:
        """生成索引DDL"""
        index_ddls = []
        full_table_name = f'"{schema_name}"."{table_name}"'

        for idx in indexes:
            # 跳过主键索引
            if idx.get('is_primary'):
                continue

            index_name = idx['index_name']
            columns = idx.get('columns', '')
            is_unique = idx.get('is_unique', False)
            index_type = idx.get('index_type', 'btree').lower()

            # 构建CREATE INDEX语句
            unique_keyword = 'UNIQUE ' if is_unique else ''
            ddl = f'CREATE {unique_keyword}INDEX "{index_name}" ON {full_table_name} USING {index_type} ({columns});'
            index_ddls.append(ddl)

        return index_ddls
