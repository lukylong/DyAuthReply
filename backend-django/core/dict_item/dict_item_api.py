#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: dict_item_api.py
@Desc: Dictionary Item API - 字典项数据接口 - 
"""
"""
Dictionary Item API - 字典项数据接口

字典项模块用于管理字典中的各个选项，支持字典项的增删改查和按字典编码查询。
集成缓存机制，优化频繁访问的字典项性能。
"""
import logging
from typing import List

from ninja import Router, Query
from ninja.pagination import paginate

from common.fu_cache import DictCacheManager, CacheStrategy, CacheManager, CacheKeyPrefix
from common.fu_crud import create, retrieve, delete, update
from common.fu_pagination import MyPagination
from core.dict.dict_model import Dict
from core.dict_item.dict_item_model import DictItem
from core.dict_item.dict_item_schema import (
    DictItemSchemaOut,
    DictItemSchemaIn,
    DictItemFilters,
)

logger = logging.getLogger(__name__)
router = Router()


@router.post("/dict_item", response=DictItemSchemaOut, tags=["字典项管理"])
def create_dict_item(request, data: DictItemSchemaIn):
    """
    创建字典项
    
    请求体:
    - dict_id: 字典ID (必填)
    - label: 显示名称 (可选)
    - value: 实际值 (可选)
    - icon: 图标 (可选)
    - status: 状态 (可选，默认为 True)
    - remark: 备注 (可选)
    
    注意: 创建后会自动清除对应字典的项目缓存
    """
    query_set = create(request, data, DictItem)

    # 创建后清除所有字典项缓存（因为列表可能改变）
    if query_set and query_set.dict:
        dict_id = str(query_set.dict.id)
        dict_code = query_set.dict.code
        # 清除字典项缓存
        CacheManager.delete(DictCacheManager.get_dict_items_cache_key(dict_id=dict_id))
        CacheManager.delete(DictCacheManager.get_dict_items_cache_key(dict_code=dict_code))
        # 清除所有字典项列表缓存
        CacheManager.delete(f"{CacheKeyPrefix.DICT_ITEMS}:all:list")
        logger.info(f"字典项已创建，清除缓存: dict_id={dict_id}, dict_code={dict_code}")

    return query_set


@router.delete("/dict_item/{dict_item_id}", response=DictItemSchemaOut, tags=["字典项管理"])
def delete_dict_item(request, dict_item_id: str):
    """
    删除字典项
    
    路径参数:
    - dict_item_id: 字典项ID
    
    注意: 删除后会自动清除对应字典的项目缓存
    """
    # 获取字典信息用于缓存清除
    try:
        dict_item = DictItem.objects.get(id=dict_item_id)
        dict_id = str(dict_item.dict.id) if dict_item.dict else None
        dict_code = dict_item.dict.code if dict_item.dict else None
    except DictItem.DoesNotExist:
        dict_id = None
        dict_code = None

    instance = delete(dict_item_id, DictItem)

    # 删除后清除字典项缓存
    if dict_id or dict_code:
        if dict_id:
            CacheManager.delete(DictCacheManager.get_dict_items_cache_key(dict_id=dict_id))
        if dict_code:
            CacheManager.delete(DictCacheManager.get_dict_items_cache_key(dict_code=dict_code))
        # 清除所有字典项列表缓存
        CacheManager.delete(f"{CacheKeyPrefix.DICT_ITEMS}:all:list")
        logger.info(f"字典项已删除，清除缓存: dict_id={dict_id}, dict_code={dict_code}")

    return instance


@router.put("/dict_item/{dict_item_id}", response=DictItemSchemaOut, tags=["字典项管理"])
def update_dict_item(request, dict_item_id: str, data: DictItemSchemaIn):
    """
    更新字典项
    
    路径参数:
    - dict_item_id: 字典项ID
    
    请求体:
    - dict_id: 字典ID (可选)
    - label: 显示名称 (可选)
    - value: 实际值 (可选)
    - icon: 图标 (可选)
    - status: 状态 (可选)
    - remark: 备注 (可选)
    
    注意: 更新后会自动清除对应字典的项目缓存
    """
    # 获取旧的字典信息用于缓存清除
    try:
        old_item = DictItem.objects.get(id=dict_item_id)
        old_dict_id = str(old_item.dict.id) if old_item.dict else None
        old_dict_code = old_item.dict.code if old_item.dict else None
    except DictItem.DoesNotExist:
        old_dict_id = None
        old_dict_code = None

    instance = update(request, dict_item_id, data, DictItem)

    # 更新后清除缓存
    if old_dict_id or old_dict_code:
        if old_dict_id:
            CacheManager.delete(DictCacheManager.get_dict_items_cache_key(dict_id=old_dict_id))
        if old_dict_code:
            CacheManager.delete(DictCacheManager.get_dict_items_cache_key(dict_code=old_dict_code))
        # 清除所有字典项列表缓存
        CacheManager.delete(f"{CacheKeyPrefix.DICT_ITEMS}:all:list")
        logger.info(f"字典项已更新，清除缓存: dict_id={old_dict_id}, dict_code={old_dict_code}")

    return instance


@router.get("/dict_item", response=List[DictItemSchemaOut], tags=["字典项管理"])
@paginate(MyPagination)
def list_dict_item(request, filters: DictItemFilters = Query(...)):
    """
    获取字典项列表 (分页)
    
    查询参数:
    - page: 页码 (可选，默认为 1)
    - page_size: 每页数量 (可选，默认为 20)
    - dict_id: 字典ID (可选，精确查询)
    - label: 显示名称 (可选，模糊查询)
    - value: 实际值 (可选，模糊查询)
    - status: 状态 (可选，精确查询)
    """
    query_set = retrieve(request, DictItem, filters)
    return query_set


@router.get("/dict_item/get/all", response=List[DictItemSchemaOut], tags=["字典项管理"])
def list_all_dict_item(request):
    """
    获取所有字典项 (不分页，有缓存)
    
    返回所有字典项，不进行分页处理，用于前端下拉框等场景。
    此接口结果会被缓存 1 小时，以提高性能。
    """
    # 尝试从缓存获取
    cache_key = f"{CacheKeyPrefix.DICT_ITEMS}:all:list"
    cached_result = CacheManager.get(cache_key)
    if cached_result is not None:
        logger.debug("从缓存返回所有字典项")
        return cached_result

    # 从数据库查询
    query_set = list(retrieve(request, DictItem))

    # 缓存结果
    CacheManager.set(cache_key, query_set, CacheStrategy.DICT_CACHE)
    logger.debug(f"字典项列表已缓存: {len(query_set)} 项")

    return query_set


@router.get("/dict_item/by/dict_code/{code}", response=List[DictItemSchemaOut], tags=["字典项管理"])
def list_dict_item_by_dict_code(request, code: str):
    """
    按字典编码获取字典项 (有缓存)
    
    路径参数:
    - code: 字典编码
    
    返回指定字典编码下的所有字典项。
    此接口结果会被缓存 1 小时，以提高性能。
    """
    from django.http import Http404

    # 尝试从缓存获取
    cache_key = DictCacheManager.get_dict_items_cache_key(dict_code=code)
    cached_result = CacheManager.get(cache_key)
    if cached_result is not None:
        logger.debug(f"从缓存返回字典项: {code}")
        return cached_result

    # 从数据库查询
    dict_obj = Dict.objects.filter(code=code).first()
    if not dict_obj:
        raise Http404(f"字典编码 '{code}' 不存在")

    query_set = list(dict_obj.dictitem_set.all())

    # 缓存结果
    CacheManager.set(cache_key, query_set, CacheStrategy.DICT_CACHE)
    logger.debug(f"字典项已缓存: {code} ({len(query_set)} 项)")

    return query_set
