#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: dict_api.py
@Desc: Dictionary API - 字典数据接口 - 
"""
"""
Dictionary API - 字典数据接口

字典模块用于管理系统字典数据，支持字典的增删改查和批量操作。
集成缓存机制，优化频繁访问的字典数据性能。
"""
import logging
from typing import List

from ninja import Router, Query
from ninja.pagination import paginate

from common.fu_cache import DictCacheManager, CacheStrategy, CacheManager, CacheKeyPrefix
from common.fu_crud import create, retrieve, delete, update
from common.fu_pagination import MyPagination
from core.dict.dict_model import Dict
from core.dict.dict_schema import DictSchemaOut, DictSchemaIn, DictFilters

logger = logging.getLogger(__name__)
router = Router()


@router.post("/dict", response=DictSchemaOut, tags=["字典管理"])
def create_dict(request, data: DictSchemaIn):
    """
    创建字典
    
    请求体:
    - name: 字典名称 (必填)
    - code: 字典编码 (必填，唯一)
    - status: 状态 (可选，默认为 True)
    - remark: 备注 (可选)
    """
    query_set = create(request, data, Dict)

    # 创建后立即缓存
    if query_set:
        DictCacheManager.set_dict(query_set, dict_id=str(query_set.id), dict_code=query_set.code)
        logger.info(f"字典已创建并缓存: {query_set.code}")

    return query_set


@router.delete("/dict/{dict_id}", response=DictSchemaOut, tags=["字典管理"])
def delete_dict(request, dict_id: str):
    """
    删除字典
    
    路径参数:
    - dict_id: 字典ID
    
    注意: 删除字典时会级联删除所有关联的字典项，并清除相关缓存
    """
    # 获取字典信息用于缓存清除
    try:
        dict_obj = Dict.objects.get(id=dict_id)
        dict_code = dict_obj.code
    except Dict.DoesNotExist:
        dict_code = None

    instance = delete(dict_id, Dict)

    # 删除后清除缓存
    if dict_code:
        DictCacheManager.invalidate_dict(dict_id=dict_id, dict_code=dict_code)
        logger.info(f"字典缓存已清除: {dict_code}")

    return instance


@router.put("/dict/{dict_id}", response=DictSchemaOut, tags=["字典管理"])
def update_dict(request, dict_id: str, data: DictSchemaIn):
    """
    更新字典
    
    路径参数:
    - dict_id: 字典ID
    
    请求体:
    - name: 字典名称 (可选)
    - code: 字典编码 (可选)
    - status: 状态 (可选)
    - remark: 备注 (可选)
    
    注意: 更新后会自动更新缓存
    """
    # 获取旧的字典编码用于缓存清除
    try:
        old_dict = Dict.objects.get(id=dict_id)
        old_code = old_dict.code
    except Dict.DoesNotExist:
        old_code = None

    instance = update(request, dict_id, data, Dict)

    # 更新后重新缓存
    if instance:
        # 清除旧缓存
        if old_code and old_code != instance.code:
            DictCacheManager.invalidate_dict(dict_id=dict_id, dict_code=old_code)

        # 设置新缓存
        DictCacheManager.set_dict(instance, dict_id=str(instance.id), dict_code=instance.code)
        logger.info(f"字典已更新并重新缓存: {instance.code}")

    return instance


@router.get("/dict", response=List[DictSchemaOut], tags=["字典管理"])
@paginate(MyPagination)
def list_dict(request, filters: DictFilters = Query(...)):
    """
    获取字典列表 (分页)
    
    查询参数:
    - page: 页码 (可选，默认为 1)
    - page_size: 每页数量 (可选，默认为 20)
    - name: 字典名称 (可选，模糊查询)
    - code: 字典编码 (可选，模糊查询)
    - status: 状态 (可选，精确查询)
    """
    query_set = retrieve(request, Dict, filters)
    return query_set


@router.get("/dict/get/all", response=List[DictSchemaOut], tags=["字典管理"])
def list_all_dict(request):
    """
    获取所有字典 (不分页，有缓存)
    
    返回所有字典，不进行分页处理，用于前端下拉框等场景。
    此接口结果会被缓存 1 小时，以提高性能。
    """
    # 尝试从缓存获取
    cache_key = f"{CacheKeyPrefix.DICT}:all:list"
    cached_result = CacheManager.get(cache_key)
    if cached_result is not None:
        logger.debug("从缓存返回所有字典")
        return cached_result

    # 从数据库查询
    query_set = list(retrieve(request, Dict))

    # 缓存结果
    CacheManager.set(cache_key, query_set, CacheStrategy.DICT_CACHE)
    logger.debug(f"字典列表已缓存: {len(query_set)} 项")

    return query_set
