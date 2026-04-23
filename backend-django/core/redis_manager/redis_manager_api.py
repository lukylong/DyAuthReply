#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: redis_manager_api.py
@Desc: 获取所有Redis数据库信息 - try:
"""
# time: 2024/12/21
# file: redis_manager_api.py
# author: AI Assistant

import logging

from ninja import Router

from .redis_manager_schema import (
    RedisKeyDetailSchema,
    RedisKeyCreateSchema,
    RedisKeyUpdateSchema,
    RedisKeySearchSchema,
    RedisKeyListResponse,
    RedisDatabaseListResponse,
    RedisKeyRenameSchema,
    RedisKeyExpireSchema,
    RedisBatchDeleteSchema,
    RedisFlushDBSchema,
    RedisOperationResponse
)
from .redis_manager_service import RedisManagerService

logger = logging.getLogger(__name__)
router = Router()


@router.get("/redis_manager/databases", response=RedisDatabaseListResponse)
def get_redis_databases(request):
    """获取所有Redis数据库信息"""
    try:
        service = RedisManagerService(db_index=0)
        databases, total_keys = service.get_all_databases()
        service.close()

        return {
            'databases': databases,
            'total_keys': total_keys
        }
    except Exception as e:
        logger.error(f"Failed to get redis databases: {e}")
        raise


@router.post("/redis_manager/{db_index}/keys/search", response=RedisKeyListResponse)
def search_redis_keys(request, db_index: int, search: RedisKeySearchSchema):
    """搜索Redis键"""
    try:
        service = RedisManagerService(db_index=db_index)
        keys, total = service.search_keys(
            pattern=search.pattern,
            key_type=search.key_type,
            page=search.page,
            page_size=search.page_size
        )
        service.close()

        return {
            'total': total,
            'keys': keys,
            'page': search.page,
            'page_size': search.page_size
        }
    except Exception as e:
        logger.error(f"Failed to search redis keys: {e}")
        raise


@router.get("/redis_manager/{db_index}/keys/{key}", response=RedisKeyDetailSchema)
def get_redis_key_detail(request, db_index: int, key: str):
    """获取Redis键详情"""
    try:
        service = RedisManagerService(db_index=db_index)
        detail = service.get_key_detail(key)
        service.close()

        return detail
    except Exception as e:
        logger.error(f"Failed to get redis key detail: {e}")
        raise


@router.post("/redis_manager/{db_index}/keys", response=RedisOperationResponse)
def create_redis_key(request, db_index: int, data: RedisKeyCreateSchema):
    """创建Redis键"""
    try:
        service = RedisManagerService(db_index=db_index)
        success = service.create_key(
            key=data.key,
            key_type=data.type,
            value=data.value,
            ttl=data.ttl
        )
        service.close()

        return {
            'success': success,
            'message': f"Key '{data.key}' created successfully"
        }
    except ValueError as e:
        return {
            'success': False,
            'message': str(e)
        }
    except Exception as e:
        logger.error(f"Failed to create redis key: {e}")
        raise


@router.put("/redis_manager/{db_index}/keys/{key}", response=RedisOperationResponse)
def update_redis_key(request, db_index: int, key: str, data: RedisKeyUpdateSchema):
    """更新Redis键"""
    try:
        service = RedisManagerService(db_index=db_index)
        success = service.update_key(
            key=key,
            value=data.value,
            ttl=data.ttl
        )
        service.close()

        return {
            'success': success,
            'message': f"Key '{key}' updated successfully"
        }
    except ValueError as e:
        return {
            'success': False,
            'message': str(e)
        }
    except Exception as e:
        logger.error(f"Failed to update redis key: {e}")
        raise


@router.delete("/redis_manager/{db_index}/keys/{key}", response=RedisOperationResponse)
def delete_redis_key(request, db_index: int, key: str):
    """删除Redis键"""
    try:
        service = RedisManagerService(db_index=db_index)
        success = service.delete_key(key)
        service.close()

        return {
            'success': success,
            'message': f"Key '{key}' deleted successfully" if success else f"Key '{key}' not found"
        }
    except Exception as e:
        logger.error(f"Failed to delete redis key: {e}")
        raise


@router.post("/redis_manager/{db_index}/keys/batch-delete", response=RedisOperationResponse)
def batch_delete_redis_keys(request, db_index: int, data: RedisBatchDeleteSchema):
    """批量删除Redis键"""
    try:
        service = RedisManagerService(db_index=db_index)
        count = service.batch_delete_keys(data.keys)
        service.close()

        return {
            'success': True,
            'message': f"Deleted {count} keys",
            'data': {'deleted_count': count}
        }
    except Exception as e:
        logger.error(f"Failed to batch delete redis keys: {e}")
        raise


@router.post("/redis_manager/{db_index}/keys/rename", response=RedisOperationResponse)
def rename_redis_key(request, db_index: int, data: RedisKeyRenameSchema):
    """重命名Redis键"""
    try:
        service = RedisManagerService(db_index=db_index)
        success = service.rename_key(data.old_key, data.new_key)
        service.close()

        return {
            'success': success,
            'message': f"Key renamed from '{data.old_key}' to '{data.new_key}'"
        }
    except ValueError as e:
        return {
            'success': False,
            'message': str(e)
        }
    except Exception as e:
        logger.error(f"Failed to rename redis key: {e}")
        raise


@router.post("/redis_manager/{db_index}/keys/expire", response=RedisOperationResponse)
def set_redis_key_expire(request, db_index: int, data: RedisKeyExpireSchema):
    """设置Redis键过期时间"""
    try:
        service = RedisManagerService(db_index=db_index)
        success = service.set_expire(data.key, data.ttl)
        service.close()

        ttl_msg = "永不过期" if data.ttl == -1 else f"{data.ttl}秒后过期"
        return {
            'success': success,
            'message': f"Key '{data.key}' set to {ttl_msg}"
        }
    except ValueError as e:
        return {
            'success': False,
            'message': str(e)
        }
    except Exception as e:
        logger.error(f"Failed to set redis key expire: {e}")
        raise


@router.post("/redis_manager/{db_index}/flush", response=RedisOperationResponse)
def flush_redis_database(request, db_index: int, data: RedisFlushDBSchema):
    """清空Redis数据库"""
    try:
        service = RedisManagerService(db_index=db_index)
        success = service.flush_db(confirm=data.confirm)
        service.close()

        return {
            'success': success,
            'message': f"Database {db_index} flushed successfully"
        }
    except ValueError as e:
        return {
            'success': False,
            'message': str(e)
        }
    except Exception as e:
        logger.error(f"Failed to flush redis database: {e}")
        raise
