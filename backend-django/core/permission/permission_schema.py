#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: permission_schema.py
@Desc: Permission Schema - 权限数据验证模式
"""
"""
Permission Schema - 权限数据验证模式
"""
from typing import Optional, List

from ninja import ModelSchema, Field, Schema
from pydantic import field_validator

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.permission.permission_model import Permission


class PermissionFilters(FuFilters):
    """权限过滤器"""
    name: Optional[str] = Field(None, q="name__icontains", alias="name")
    code: Optional[str] = Field(None, q="code__icontains", alias="code")
    menu_id: Optional[str] = Field(None, q="menu_id", alias="menu_id")
    permission_type: Optional[int] = Field(None, q="permission_type", alias="permission_type")
    is_active: Optional[bool] = Field(None, q="is_active", alias="is_active")


class PermissionSchemaIn(ModelSchema):
    """权限输入模式"""
    menu_id: str = Field(..., alias="menu_id", description="菜单ID")

    @field_validator('code', check_fields=False)
    @classmethod
    def validate_code(cls, v):
        """验证权限编码格式"""
        if not v:
            raise ValueError('权限编码不能为空')
        if not all(c.isalnum() or c in '_:' for c in v):
            raise ValueError('权限编码只能包含字母、数字、下划线和冒号')
        return v

    @field_validator('http_method', check_fields=False)
    @classmethod
    def validate_http_method(cls, v):
        """验证 HTTP 方法"""
        if v not in [0, 1, 2, 3, 4, 5]:
            raise ValueError('HTTP方法必须在 0-5 之间')
        return v

    @field_validator('permission_type', check_fields=False)
    @classmethod
    def validate_permission_type(cls, v):
        """验证权限类型"""
        if v not in [0, 1, 2, 3]:
            raise ValueError('权限类型必须在 0-3 之间')
        return v

    class Config:
        model = Permission
        model_exclude = (*exclude_fields, "menu")


class PermissionSchemaPatch(Schema):
    """权限部分更新模式（PATCH）"""
    name: Optional[str] = None
    code: Optional[str] = None
    menu_id: Optional[str] = None
    api_path: Optional[str] = None
    http_method: Optional[int] = None
    permission_type: Optional[int] = None
    is_active: Optional[bool] = None
    description: Optional[str] = None

    @field_validator('code')
    @classmethod
    def validate_code(cls, v):
        """验证权限编码格式"""
        if v is not None:
            if not v:
                raise ValueError('权限编码不能为空')
            if not all(c.isalnum() or c in '_:' for c in v):
                raise ValueError('权限编码只能包含字母、数字、下划线和冒号')
        return v

    @field_validator('http_method')
    @classmethod
    def validate_http_method(cls, v):
        """验证 HTTP 方法"""
        if v is not None and v not in [0, 1, 2, 3, 4, 5]:
            raise ValueError('HTTP方法必须在 0-5 之间')
        return v

    @field_validator('permission_type')
    @classmethod
    def validate_permission_type(cls, v):
        """验证权限类型"""
        if v is not None and v not in [0, 1, 2, 3]:
            raise ValueError('权限类型必须在 0-3 之间')
        return v


class PermissionSchemaOut(ModelSchema):
    """权限输出模式"""
    menu_id: Optional[str] = Field(None, alias="menu_id")
    menu_name: Optional[str] = Field(None, alias="menu.name")
    http_method_display: Optional[str] = None
    permission_type_display: Optional[str] = None

    class Config:
        model = Permission
        model_fields = "__all__"

    @staticmethod
    def resolve_http_method_display(obj):
        """解析 HTTP 方法显示名称"""
        return obj.get_http_method_display_name()

    @staticmethod
    def resolve_permission_type_display(obj):
        """解析权限类型显示名称"""
        return obj.get_permission_type_display_name()


class PermissionSchemaDetail(PermissionSchemaOut):
    """权限详情输出模式（包含更多信息）"""
    pass


class PermissionBatchDeleteIn(Schema):
    """批量删除权限输入"""
    ids: List[str] = Field(..., description="要删除的权限ID列表")


class PermissionBatchDeleteOut(Schema):
    """批量删除权限输出"""
    count: int = Field(..., description="删除的记录数")


class PermissionBatchUpdateStatusIn(Schema):
    """批量更新权限状态输入"""
    ids: List[str] = Field(..., description="权限ID列表")
    is_active: bool = Field(..., description="是否启用")


class PermissionBatchUpdateStatusOut(Schema):
    """批量更新权限状态输出"""
    count: int = Field(..., description="更新的记录数")


class PermissionRouteItem(Schema):
    """单个路由权限项"""
    path: str = Field(..., description="API路径")
    method: str = Field(..., description="HTTP方法")
    name: str = Field(..., description="权限名称")
    code: str = Field(..., description="权限编码")
    summary: Optional[str] = Field(None, description="权限描述")
    permission_type: int = Field(default=1, description="权限类型")
    http_method: int = Field(..., description="HTTP方法编码")
    is_active: bool = Field(default=True, description="是否启用")


class PermissionBatchCreateFromRoutesIn(Schema):
    """从路由批量创建权限输入"""
    menu_id: str = Field(..., description="菜单ID")
    routes: List[PermissionRouteItem] = Field(..., description="要创建的权限列表")


class PermissionBatchCreateFromRoutesOut(Schema):
    """从路由批量创建权限输出"""
    created: int = Field(..., description="创建的权限数")
    skipped: int = Field(..., description="跳过的权限数")
    failed: int = Field(..., description="失败的权限数")
    errors: List[str] = Field(default_factory=list, description="错误信息列表")


class PermissionByMenuOut(Schema):
    """根据菜单获取权限输出"""
    menu_id: str
    menu_name: str
    permissions: List[PermissionSchemaOut]
