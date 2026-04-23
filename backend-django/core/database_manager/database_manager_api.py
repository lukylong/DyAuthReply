#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: database_manager_api.py
@Desc: 数据库管理 API - 提供数据库、表、数据的管理接口
"""
"""
数据库管理 API
提供数据库、表、数据的管理接口
"""
import logging

from ninja import Router

from .database_manager_schema import *
from .database_manager_service import DatabaseManagerService

logger = logging.getLogger(__name__)
router = Router(tags=["Core-DatabaseManager"])


# ============ 数据库配置 ============
@router.get("/database_manager/configs", response=List[DatabaseConfig], summary="获取数据库配置列表")
def get_database_configs(request):
    """获取所有配置的数据库信息"""
    return DatabaseManagerService.get_database_configs()


@router.post("/database_manager/{db_name}/test", response=dict, summary="测试数据库连接")
def test_database_connection(request, db_name: str):
    """测试指定数据库的连接"""
    return DatabaseManagerService.test_connection(db_name)


# ============ 数据库管理 ============
@router.get("/database_manager/{db_name}/databases", response=List[DatabaseInfo], summary="获取数据库列表")
def get_databases(request, db_name: str):
    """获取所有数据库"""
    handler = DatabaseManagerService.get_handler(db_name)
    return handler.get_databases()


@router.post("/database_manager/{db_name}/databases", response=DatabaseOperationOut, summary="创建数据库")
def create_database(request, db_name: str, data: DatabaseCreateIn):
    """创建新数据库"""
    try:
        handler = DatabaseManagerService.get_handler(db_name)

        # 根据数据库类型传递不同的参数
        if handler.db_type == 'postgresql':
            success = handler.create_database(
                name=data.name,
                owner=data.owner,
                encoding=data.encoding,
                template=data.template
            )
        elif handler.db_type == 'mysql':
            success = handler.create_database(
                name=data.name,
                charset=data.charset or 'utf8mb4',
                collation=data.collation or 'utf8mb4_unicode_ci'
            )
        elif handler.db_type == 'sqlserver':
            success = handler.create_database(
                name=data.name,
                collation=data.collation or 'SQL_Latin1_General_CP1_CI_AS'
            )
        else:
            success = handler.create_database(name=data.name)

        return DatabaseOperationOut(
            success=success,
            message="数据库创建成功" if success else "数据库创建失败"
        )
    except Exception as e:
        logger.error(f"Failed to create database: {e}")
        return DatabaseOperationOut(success=False, message=str(e))


@router.delete("/database_manager/{db_name}/databases/{database_name}", response=DatabaseOperationOut,
               summary="删除数据库")
def drop_database(request, db_name: str, database_name: str):
    """删除数据库"""
    try:
        handler = DatabaseManagerService.get_handler(db_name)
        success = handler.drop_database(database_name)
        return DatabaseOperationOut(
            success=success,
            message="数据库删除成功" if success else "数据库删除失败"
        )
    except Exception as e:
        logger.error(f"Failed to drop database: {e}")
        return DatabaseOperationOut(success=False, message=str(e))


# ============ Schema管理（PostgreSQL） ============
@router.get("/database_manager/{db_name}/schemas", response=List[SchemaInfo], summary="获取Schema列表")
def get_schemas(request, db_name: str, database: str = None):
    """获取所有Schema（仅PostgreSQL）"""
    handler = DatabaseManagerService.get_handler(db_name)
    if handler.db_type == 'postgresql':
        return handler.get_schemas(database)
    return []


# ============ 表管理 ============
@router.get("/database_manager/{db_name}/tables", response=List[TableInfo], summary="获取表列表")
def get_tables(request, db_name: str, database: str = None, schema_name: str = None):
    """获取指定schema/database的所有表"""
    handler = DatabaseManagerService.get_handler(db_name)

    # 根据数据库类型设置默认schema
    if not schema_name:
        if handler.db_type == 'postgresql':
            schema_name = 'public'
        elif handler.db_type == 'mysql':
            schema_name = database  # MySQL中database和schema是同义词
        elif handler.db_type == 'sqlserver':
            schema_name = 'dbo'

    return handler.get_tables(database=database, schema_name=schema_name)


@router.get("/database_manager/{db_name}/tables/{table_name}/structure", response=TableStructure, summary="获取表结构")
def get_table_structure(request, db_name: str, table_name: str, database: str = None, schema_name: str = None):
    """获取表的详细结构"""
    handler = DatabaseManagerService.get_handler(db_name)

    # 根据数据库类型设置默认schema
    if not schema_name:
        if handler.db_type == 'postgresql':
            schema_name = 'public'
        elif handler.db_type == 'mysql':
            schema_name = database or handler.connection.settings_dict['NAME']
        elif handler.db_type == 'sqlserver':
            schema_name = 'dbo'

    return handler.get_table_structure(table_name, database=database, schema_name=schema_name)


@router.get("/database_manager/{db_name}/tables/{table_name}/ddl", response=dict, summary="获取表DDL")
def get_table_ddl(request, db_name: str, table_name: str, database: str = None, schema_name: str = None):
    """获取表的DDL语句"""
    handler = DatabaseManagerService.get_handler(db_name)

    # 根据数据库类型设置默认schema
    if not schema_name:
        if handler.db_type == 'postgresql':
            schema_name = 'public'
        elif handler.db_type == 'mysql':
            schema_name = handler.connection.settings_dict['NAME']
        elif handler.db_type == 'sqlserver':
            schema_name = 'dbo'

    try:
        ddl = handler.get_table_ddl(table_name, schema_name)
        return {"ddl": ddl}
    except Exception as e:
        logger.error(f"Failed to get DDL for table {table_name}: {e}")
        return {"ddl": f"-- 获取DDL失败: {str(e)}"}


@router.get("/database_manager/{db_name}/tables/{table_name}/columns", response=List[ColumnInfo], summary="获取表字段")
def get_table_columns(request, db_name: str, table_name: str, schema_name: str = None):
    """获取表的字段信息"""
    handler = DatabaseManagerService.get_handler(db_name)

    if not schema_name:
        if handler.db_type == 'postgresql':
            schema_name = 'public'
        elif handler.db_type == 'mysql':
            schema_name = handler.connection.settings_dict['NAME']
        elif handler.db_type == 'sqlserver':
            schema_name = 'dbo'

    return handler.get_table_columns(table_name, schema_name)


@router.get("/database_manager/{db_name}/tables/{table_name}/indexes", response=List[IndexInfo], summary="获取表索引")
def get_table_indexes(request, db_name: str, table_name: str, schema_name: str = None):
    """获取表的索引信息"""
    handler = DatabaseManagerService.get_handler(db_name)

    if not schema_name:
        if handler.db_type == 'postgresql':
            schema_name = 'public'
        elif handler.db_type == 'mysql':
            schema_name = handler.connection.settings_dict['NAME']
        elif handler.db_type == 'sqlserver':
            schema_name = 'dbo'

    return handler.get_table_indexes(table_name, schema_name)


@router.get("/database_manager/{db_name}/tables/{table_name}/constraints", response=List[ConstraintInfo],
            summary="获取表约束")
def get_table_constraints(request, db_name: str, table_name: str, schema_name: str = None):
    """获取表的约束信息"""
    handler = DatabaseManagerService.get_handler(db_name)

    if not schema_name:
        if handler.db_type == 'postgresql':
            schema_name = 'public'
        elif handler.db_type == 'mysql':
            schema_name = handler.connection.settings_dict['NAME']
        elif handler.db_type == 'sqlserver':
            schema_name = 'dbo'

    return handler.get_table_constraints(table_name, schema_name)


# ============ 视图管理 ============
@router.get("/database_manager/{db_name}/views", response=List[ViewInfo], summary="获取视图列表")
def get_views(request, db_name: str, database: str = None, schema_name: str = None):
    """获取指定schema/database的所有视图"""
    handler = DatabaseManagerService.get_handler(db_name)

    # 根据数据库类型设置默认schema
    if not schema_name:
        if handler.db_type == 'postgresql':
            schema_name = 'public'
        elif handler.db_type == 'mysql':
            schema_name = database  # MySQL中database和schema是同义词
        elif handler.db_type == 'sqlserver':
            schema_name = 'dbo'

    return handler.get_views(database=database, schema_name=schema_name)


@router.get("/database_manager/{db_name}/views/{view_name}/structure", response=ViewStructure, summary="获取视图结构")
def get_view_structure(request, db_name: str, view_name: str, schema_name: str = None):
    """获取视图的详细结构"""
    handler = DatabaseManagerService.get_handler(db_name)

    # 根据数据库类型设置默认schema
    if not schema_name:
        if handler.db_type == 'postgresql':
            schema_name = 'public'
        elif handler.db_type == 'mysql':
            schema_name = handler.connection.settings_dict['NAME']
        elif handler.db_type == 'sqlserver':
            schema_name = 'dbo'

    return handler.get_view_structure(view_name, schema_name)


@router.get("/database_manager/{db_name}/views/{view_name}/definition", response=dict, summary="获取视图定义")
def get_view_definition(request, db_name: str, view_name: str, schema_name: str = None):
    """获取视图的定义SQL"""
    handler = DatabaseManagerService.get_handler(db_name)

    # 根据数据库类型设置默认schema
    if not schema_name:
        if handler.db_type == 'postgresql':
            schema_name = 'public'
        elif handler.db_type == 'mysql':
            schema_name = handler.connection.settings_dict['NAME']
        elif handler.db_type == 'sqlserver':
            schema_name = 'dbo'

    try:
        definition = handler.get_view_definition(view_name, schema_name)
        return {"definition": definition}
    except Exception as e:
        logger.error(f"Failed to get view definition for {view_name}: {e}")
        return {"definition": f"-- 获取视图定义失败: {str(e)}"}


@router.get("/database_manager/{db_name}/views/{view_name}/dependencies", response=List[str], summary="获取视图依赖")
def get_view_dependencies(request, db_name: str, view_name: str, schema_name: str = None):
    """获取视图依赖的表列表"""
    handler = DatabaseManagerService.get_handler(db_name)

    # 根据数据库类型设置默认schema
    if not schema_name:
        if handler.db_type == 'postgresql':
            schema_name = 'public'
        elif handler.db_type == 'mysql':
            schema_name = handler.connection.settings_dict['NAME']
        elif handler.db_type == 'sqlserver':
            schema_name = 'dbo'

    return handler.get_view_dependencies(view_name, schema_name)


# ============ 数据查询 ============
@router.post("/database_manager/{db_name}/query", response=QueryDataOut, summary="查询表数据")
def query_data(request, db_name: str, data: QueryDataIn):
    """分页查询表数据"""
    handler = DatabaseManagerService.get_handler(db_name)
    return handler.query_data(
        table_name=data.table_name,
        schema_name=data.schema_name,
        page=data.page,
        page_size=data.page_size,
        where=data.where,
        order_by=data.order_by
    )


# ============ SQL执行 ============
@router.post("/database_manager/{db_name}/execute", response=ExecuteSQLOut, summary="执行SQL")
def execute_sql(request, db_name: str, data: ExecuteSQLIn):
    """执行自定义SQL语句"""
    handler = DatabaseManagerService.get_handler(db_name)
    return handler.execute_sql(data.sql, data.is_query)


# ============ 数据操作 ============
@router.post("/database_manager/{db_name}/data/insert", response=DataOperationOut, summary="插入数据")
def insert_data(request, db_name: str, data: InsertDataIn):
    """向表中插入数据"""
    handler = DatabaseManagerService.get_handler(db_name)
    return handler.insert_data(data.table_name, data.data, data.schema_name)


@router.post("/database_manager/{db_name}/data/update", response=DataOperationOut, summary="更新数据")
def update_data(request, db_name: str, data: UpdateDataIn):
    """更新表中的数据"""
    handler = DatabaseManagerService.get_handler(db_name)
    return handler.update_data(data.table_name, data.data, data.where, data.schema_name)


@router.post("/database_manager/{db_name}/data/delete", response=DataOperationOut, summary="删除数据")
def delete_data(request, db_name: str, data: DeleteDataIn):
    """删除表中的数据"""
    handler = DatabaseManagerService.get_handler(db_name)
    return handler.delete_data(data.table_name, data.where, data.schema_name)


# ============ DDL操作 ============
@router.post("/database_manager/{db_name}/execute/ddl", response=ExecuteDDLOut, summary="执行DDL语句")
def execute_ddl(request, db_name: str, data: ExecuteDDLIn):
    """
    执行DDL语句（CREATE TABLE, ALTER TABLE, DROP TABLE等）
    
    Args:
        db_name: 数据库配置名称
        data: DDL执行参数
            - sql: DDL SQL语句
            - database: 数据库名（可选）
            - schema_name: Schema名（可选）
    
    Returns:
        ExecuteDDLOut: 执行结果
            - success: 是否成功
            - message: 执行消息
            - affected_rows: 影响行数
    """
    handler = DatabaseManagerService.get_handler(db_name)
    result = handler.execute_ddl(data.sql, data.database, data.schema_name)
    return ExecuteDDLOut(**result)
