#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: menu_schema.py
@Desc: Menu Schema - 菜单数据验证模式
"""
"""
Menu Schema - 菜单数据验证模式
"""
from typing import Optional, List

from ninja import ModelSchema, Field, Schema
from pydantic import field_validator

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.menu.menu_model import Menu


class MenuFilters(FuFilters):
    """菜单过滤器"""
    name: Optional[str] = Field(None, q="name__icontains", alias="name")
    title: Optional[str] = Field(None, q="title__icontains", alias="title")
    type: Optional[str] = Field(None, q="type", alias="type")
    parent_id: Optional[str] = Field(None, q="parent_id", alias="parent_id")


class MenuSchemaIn(ModelSchema):
    """菜单输入模式"""
    parent_id: Optional[str] = Field(None, alias="parent_id")

    @field_validator('name', check_fields=False)
    @classmethod
    def validate_name(cls, v):
        """验证菜单名称格式"""
        if not v:
            raise ValueError('菜单名称不能为空')
        if not v[0].isalpha():
            raise ValueError('菜单名称必须以字母开头')
        if not all(c.isalnum() or c in '_-' for c in v):
            raise ValueError('菜单名称只能包含字母、数字、下划线和横线')
        return v

    @field_validator('path', check_fields=False)
    @classmethod
    def validate_path(cls, v):
        """验证路由路径"""
        if not v:
            raise ValueError('路由路径不能为空')
        return v

    @field_validator('type', check_fields=False)
    @classmethod
    def validate_type(cls, v):
        """验证菜单类型"""
        if v not in ['catalog', 'menu', 'external', 'online_form', 'online_page']:
            raise ValueError('菜单类型必须为 catalog、menu、external、online_form 或 online_page')
        return v

    @field_validator('order', check_fields=False)
    @classmethod
    def validate_order(cls, v):
        """验证排序值"""
        if v is not None and v < 0:
            raise ValueError('排序值不能为负数')
        return v

    class Config:
        model = Menu
        model_exclude = (*exclude_fields, "parent")


class MenuSchemaPatch(Schema):
    """菜单部分更新模式（PATCH）"""
    name: Optional[str] = None
    title: Optional[str] = None
    path: Optional[str] = None
    component: Optional[str] = None
    redirect: Optional[str] = None
    icon: Optional[str] = None
    type: Optional[str] = None
    parent_id: Optional[str] = None
    order: Optional[int] = None
    status: Optional[int] = None
    is_link: Optional[bool] = None
    is_frame: Optional[bool] = None
    is_cache: Optional[bool] = None
    is_affix: Optional[bool] = None
    is_hidden: Optional[bool] = None
    permission: Optional[str] = None
    meta: Optional[dict] = None

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        """验证菜单名称格式"""
        if v is not None:
            if not v:
                raise ValueError('菜单名称不能为空')
            if not v[0].isalpha():
                raise ValueError('菜单名称必须以字母开头')
            if not all(c.isalnum() or c in '_-' for c in v):
                raise ValueError('菜单名称只能包含字母、数字、下划线和横线')
        return v

    @field_validator('path')
    @classmethod
    def validate_path(cls, v):
        """验证路由路径"""
        if v is not None and not v:
            raise ValueError('路由路径不能为空')
        return v

    @field_validator('type')
    @classmethod
    def validate_type(cls, v):
        """验证菜单类型"""
        if v is not None and v not in ['catalog', 'menu', 'external', 'online_form', 'online_page']:
            raise ValueError('菜单类型必须为 catalog、menu、external、online_form 或 online_page')
        return v

    @field_validator('order')
    @classmethod
    def validate_order(cls, v):
        """验证排序值"""
        if v is not None and v < 0:
            raise ValueError('排序值不能为负数')
        return v


class MenuSchemaOut(ModelSchema):
    """菜单输出模式"""
    parent_id: Optional[str] = Field(None, alias="parent_id")
    level: Optional[int] = None
    child_count: Optional[int] = None
    full_path: Optional[str] = None

    class Config:
        model = Menu
        model_fields = "__all__"

    @staticmethod
    def resolve_level(obj):
        """解析菜单层级"""
        return obj.get_level()

    @staticmethod
    def resolve_child_count(obj):
        """解析子菜单数量"""
        return obj.get_child_count()

    @staticmethod
    def resolve_full_path(obj):
        """解析完整路径"""
        return obj.get_full_path()


class MenuSchemaTree(Schema):
    """菜单树形结构输出"""
    id: str
    name: str
    title: Optional[str]
    path: str
    type: str
    parent_id: Optional[str]
    icon: Optional[str]
    order: int
    level: int
    child_count: int
    children: Optional[List['MenuSchemaTree']] = []


class MenuSchemaSimple(Schema):
    """菜单简单输出（用于选择器）"""
    id: str
    name: str
    title: Optional[str]
    path: str
    type: str
    parent_id: Optional[str]
    level: int


class MenuSchemaRoute(Schema):
    """菜单路由输出（前端路由格式）"""
    name: str
    path: str
    component: Optional[str]
    redirect: Optional[str]
    meta: dict
    children: Optional[List['MenuSchemaRoute']] = []


class MenuBatchDeleteIn(Schema):
    """批量删除菜单输入"""
    ids: List[str] = Field(..., description="要删除的菜单ID列表")


class MenuBatchDeleteOut(Schema):
    """批量删除菜单输出"""
    count: int = Field(..., description="删除的记录数")
    failed_ids: List[str] = Field(default=[], description="删除失败的ID列表")


class MenuPathOut(Schema):
    """菜单路径输出"""
    menu_id: str
    menu_name: str
    path: List[MenuSchemaSimple] = Field(..., description="从根到当前菜单的路径")


class MenuStatsOut(Schema):
    """菜单统计输出"""
    total_count: int
    type_stats: dict
    max_level: int


class MenuCheckNameIn(Schema):
    """检查菜单名称输入"""
    name: str = Field(..., description="菜单名称")
    exclude_id: Optional[str] = Field(None, description="排除的菜单ID（用于编辑时）")


class MenuCheckPathIn(Schema):
    """检查路由路径输入"""
    path: str = Field(..., description="路由路径")
    exclude_id: Optional[str] = Field(None, description="排除的菜单ID（用于编辑时）")


class MenuCheckOut(Schema):
    """检查结果输出"""
    exists: bool = Field(..., description="是否已存在")
    message: Optional[str] = Field(None, description="提示信息")
