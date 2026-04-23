#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: database_manager_schema.py
@Desc: 数据库管理 Schema 定义
"""
"""
数据库管理 Schema 定义
"""
from typing import Any, Dict, List, Optional

from ninja import Schema, Field


# ============ 数据库相关 ============
class DatabaseInfo(Schema):
    """数据库信息"""
    name: str
    owner: Optional[str] = None
    encoding: Optional[str] = None
    collation: Optional[str] = None
    size: Optional[str] = None
    size_bytes: Optional[int] = None
    tables_count: Optional[int] = None
    description: Optional[str] = None


class DatabaseCreateIn(Schema):
    """创建数据库输入"""
    name: str
    owner: Optional[str] = None
    encoding: Optional[str] = "UTF8"
    charset: Optional[str] = "utf8mb4"  # MySQL
    collation: Optional[str] = None
    template: Optional[str] = "template0"  # PostgreSQL


class DatabaseOperationOut(Schema):
    """数据库操作输出"""
    success: bool
    message: str


# ============ Schema相关（PostgreSQL） ============
class SchemaInfo(Schema):
    """Schema信息"""
    name: str
    owner: Optional[str] = None
    tables_count: Optional[int] = None


# ============ 表相关 ============
class TableInfo(Schema):
    """表信息"""
    schema_name: Optional[str] = None
    table_name: str
    table_type: Optional[str] = "BASE TABLE"
    row_count: Optional[int] = None
    total_size: Optional[str] = None
    total_size_bytes: Optional[int] = None
    table_size: Optional[str] = None
    table_size_bytes: Optional[int] = None
    indexes_size: Optional[str] = None
    indexes_size_bytes: Optional[int] = None
    data_length: Optional[int] = None  # MySQL
    index_length: Optional[int] = None  # MySQL
    description: Optional[str] = None


# ============ 视图相关 ============
class ViewInfo(Schema):
    """视图信息"""
    schema_name: Optional[str] = None
    view_name: str
    view_definition: Optional[str] = None  # 视图定义SQL（简短版）
    is_updatable: Optional[bool] = None  # 是否可更新
    check_option: Optional[str] = None  # CHECK OPTION
    view_type: Optional[str] = "VIEW"  # 视图类型: VIEW 或 MATERIALIZED VIEW
    description: Optional[str] = None  # 视图描述/注释


class ViewColumn(Schema):
    """视图列信息"""
    column_name: str
    data_type: str
    is_nullable: Optional[bool] = None
    ordinal_position: Optional[int] = None
    description: Optional[str] = None


class ViewStructure(Schema):
    """视图结构详情"""
    view_info: ViewInfo
    columns: List[ViewColumn]
    dependencies: List[str]  # 依赖的表列表
    definition_sql: str  # 完整的CREATE VIEW语句


# ============ 列信息 ============
class ColumnInfo(Schema):
    """字段信息"""
    column_name: str
    data_type: str
    is_nullable: bool
    column_default: Optional[str] = None
    character_maximum_length: Optional[int] = None
    numeric_precision: Optional[int] = None
    numeric_scale: Optional[int] = None
    ordinal_position: Optional[int] = None
    is_primary_key: bool = False
    is_unique: bool = False
    description: Optional[str] = None


class IndexInfo(Schema):
    """索引信息"""
    index_name: str
    index_type: Optional[str] = None
    columns: str  # 逗号分隔的列名
    is_unique: bool = False
    is_primary: bool = False
    definition: Optional[str] = None


class ConstraintInfo(Schema):
    """约束信息"""
    constraint_name: str
    constraint_type: str  # PRIMARY KEY, FOREIGN KEY, UNIQUE, CHECK
    columns: Optional[str] = None  # 逗号分隔的列名
    definition: Optional[str] = None
    referenced_table: Optional[str] = None
    referenced_columns: Optional[str] = None


class TableStructure(Schema):
    """表结构详情"""
    table_info: TableInfo
    columns: List[ColumnInfo]
    indexes: List[IndexInfo]
    constraints: List[ConstraintInfo]


# ============ 数据查询相关 ============
class QueryDataIn(Schema):
    """查询数据输入"""
    table_name: str
    schema_name: Optional[str] = None
    page: int = 1
    page_size: int = 20
    where: Optional[str] = None  # WHERE条件
    order_by: Optional[str] = None  # 排序字段


class QueryDataOut(Schema):
    """查询数据输出"""
    columns: List[str]
    rows: List[Dict[str, Any]]
    total: int
    page: int
    page_size: int


# ============ SQL执行相关 ============
class ExecuteSQLIn(Schema):
    """执行SQL输入"""
    sql: str
    is_query: bool = True  # True=SELECT, False=INSERT/UPDATE/DELETE


class ExecuteSQLOut(Schema):
    """执行SQL输出"""
    success: bool
    message: str
    columns: Optional[List[str]] = None
    rows: Optional[List[Dict[str, Any]]] = None
    affected_rows: Optional[int] = None
    execution_time: float


# ============ 数据操作相关 ============
class InsertDataIn(Schema):
    """插入数据"""
    table_name: str
    schema_name: Optional[str] = None
    data: Dict[str, Any]


class UpdateDataIn(Schema):
    """更新数据"""
    table_name: str
    schema_name: Optional[str] = None
    data: Dict[str, Any]
    where: str  # WHERE条件（必须）


class DeleteDataIn(Schema):
    """删除数据"""
    table_name: str
    schema_name: Optional[str] = None
    where: str  # WHERE条件（必须）


class DataOperationOut(Schema):
    """数据操作输出"""
    success: bool
    message: str
    affected_rows: int


# ============ DDL操作相关 ============
class ExecuteDDLIn(Schema):
    """执行DDL输入"""
    sql: str = Field(..., description="DDL SQL语句")
    database: Optional[str] = Field(None, description="数据库名")
    schema_name: Optional[str] = Field(None, description="Schema名")


class ExecuteDDLOut(Schema):
    """执行DDL输出"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(..., description="执行消息")
    affected_rows: int = Field(default=0, description="影响行数")


# ============ 数据库配置相关 ============
class DatabaseConfig(Schema):
    """数据库配置信息"""
    db_name: str  # Django配置的key
    name: str  # 显示名称
    db_type: str  # postgresql, mysql, sqlserver等
    host: str
    port: int
    database: str  # 实际数据库名
    user: str
    has_password: bool
