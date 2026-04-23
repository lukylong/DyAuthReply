#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: dict_item_schema.py
@Desc: Dictionary Item Schema - 字典项数据验证模式
"""
"""
Dictionary Item Schema - 字典项数据验证模式
"""
from typing import Optional

from ninja import ModelSchema, Field

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.dict_item.dict_item_model import DictItem


class DictItemFilters(FuFilters):
    dict_id: Optional[str] = Field(None, alias="dict_id")
    label: Optional[str] = Field(None, q="label__contains", alias="label")
    value: Optional[str] = Field(None, q="value__contains", alias="value")
    status: Optional[bool] = Field(None, alias="status")


class DictItemSchemaIn(ModelSchema):
    dict_id: Optional[str]

    class Config:
        model = DictItem
        model_exclude = (*exclude_fields, "dict")

    # @field_validator('dict_id', mode='before')
    # @classmethod
    # def validate_dict_id(cls, v):
    #     """验证并转换 dict_id 为 dict 对象"""
    #     if not v:
    #         raise ValueError("字典ID不能为空")
    #     dict_obj = Dict.objects.filter(id=v).first()
    #     if not dict_obj:
    #         raise ValueError(f"字典ID '{v}' 不存在")
    #     return dict_obj


class DictItemSchemaOut(ModelSchema):
    dict_id: Optional[str] = Field(None, alias="dict_id")

    class Config:
        model = DictItem
        model_exclude = ("dict",)

    @staticmethod
    def resolve_dict_id(obj):
        """将 dict 对象转为 dict_id"""
        return str(obj.dict_id) if obj.dict_id else None
