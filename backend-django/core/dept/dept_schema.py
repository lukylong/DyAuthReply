#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: dept_schema.py
@Desc: Dept Schema - 部门数据验证模式
"""
"""
Dept Schema - 部门数据验证模式
"""
from typing import Optional, List

from ninja import ModelSchema, Field, Schema
from pydantic import field_validator

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.dept.dept_model import Dept


class DeptFilters(FuFilters):
    """部门过滤器"""
    name: Optional[str] = Field(None, q="name__icontains", alias="name")
    code: Optional[str] = Field(None, q="code__icontains", alias="code")
    status: Optional[bool] = Field(None, q="status", alias="status")
    dept_type: Optional[str] = Field(None, q="dept_type", alias="dept_type")
    parent_id: Optional[str] = Field(None, q="parent_id", alias="parent_id")
    level: Optional[int] = Field(None, q="level", alias="level")


class DeptSchemaIn(ModelSchema):
    """部门输入模式"""
    parent_id: Optional[str] = Field(None, alias="parent_id")
    lead_id: Optional[str] = Field(None, alias="lead_id")

    @field_validator('code', check_fields=False)
    @classmethod
    def validate_code(cls, v):
        """验证部门编码格式"""
        if v and not v.replace('-', '').replace('_', '').isalnum():
            raise ValueError('部门编码只能包含字母、数字、下划线和横线')
        return v

    @field_validator('dept_type', check_fields=False)
    @classmethod
    def validate_dept_type(cls, v):
        """验证部门类型"""
        valid_types = ['company', 'department', 'team', 'other']
        if v is not None and v not in valid_types:
            raise ValueError(f'部门类型必须是以下之一: {", ".join(valid_types)}')
        return v

    @field_validator('phone', check_fields=False)
    @classmethod
    def validate_phone(cls, v):
        """验证电话格式"""
        if v and not all(c.isdigit() or c in '-+() ' for c in v):
            raise ValueError('电话号码格式不正确')
        return v

    class Config:
        model = Dept
        model_exclude = (*exclude_fields, "parent", "lead", "level", "path")


class DeptSchemaPatch(Schema):
    """部门部分更新模式（PATCH）"""
    name: Optional[str] = None
    code: Optional[str] = None
    dept_type: Optional[str] = None
    parent_id: Optional[str] = None
    lead_id: Optional[str] = None
    sort: Optional[int] = None
    status: Optional[bool] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    description: Optional[str] = None

    @field_validator('code')
    @classmethod
    def validate_code(cls, v):
        """验证部门编码格式"""
        if v is not None and v and not v.replace('-', '').replace('_', '').isalnum():
            raise ValueError('部门编码只能包含字母、数字、下划线和横线')
        return v

    @field_validator('dept_type')
    @classmethod
    def validate_dept_type(cls, v):
        """验证部门类型"""
        if v is not None:
            valid_types = ['company', 'department', 'team', 'other']
            if v not in valid_types:
                raise ValueError(f'部门类型必须是以下之一: {", ".join(valid_types)}')
        return v

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v):
        """验证电话格式"""
        if v is not None and v and not all(c.isdigit() or c in '-+() ' for c in v):
            raise ValueError('电话号码格式不正确')
        return v


class DeptSchemaOut(ModelSchema):
    """部门输出模式"""
    parent_id: Optional[str] = None
    lead_id: Optional[str] = None
    lead_name: Optional[str] = Field(None, alias="lead.name")
    dept_type_display: Optional[str] = None
    child_count: Optional[int] = None
    user_count: Optional[int] = None
    full_name: Optional[str] = None

    class Config:
        model = Dept
        model_fields = "__all__"

    @staticmethod
    def resolve_parent_id(obj):
        """解析父部门ID，将UUID转换为字符串"""
        return str(obj.parent_id) if obj.parent_id else None

    @staticmethod
    def resolve_lead_id(obj):
        """解析负责人ID，将UUID转换为字符串"""
        return str(obj.lead_id) if obj.lead_id else None

    @staticmethod
    def resolve_dept_type_display(obj):
        """解析部门类型显示名称"""
        return obj.get_dept_type_display_name()

    @staticmethod
    def resolve_child_count(obj):
        """解析子部门数量"""
        return obj.get_child_count()

    @staticmethod
    def resolve_user_count(obj):
        """解析用户数量"""
        return obj.get_user_count()

    @staticmethod
    def resolve_full_name(obj):
        """解析部门全名"""
        return obj.get_full_name()


class DeptSchemaTree(Schema):
    """部门树形结构输出"""
    id: str
    name: str
    code: Optional[str]
    parent_id: Optional[str]
    dept_type: str
    status: bool
    child_count: int
    user_count: int
    level: int
    children: Optional[List['DeptSchemaTree']] = []


class DeptSchemaSimple(Schema):
    """部门简单输出（用于选择器）"""
    id: str
    name: str
    code: Optional[str]
    parent_id: Optional[str]
    level: int
    status: bool


class DeptSchemaBatchDeleteIn(Schema):
    """批量删除部门输入"""
    ids: List[str] = Field(..., description="要删除的部门ID列表")


class DeptSchemaBatchDeleteOut(Schema):
    """批量删除部门输出"""
    count: int = Field(..., description="删除的记录数")
    failed_ids: List[str] = Field(default=[], description="删除失败的ID列表")


class DeptBatchUpdateStatusIn(Schema):
    """批量更新部门状态输入"""
    ids: List[str] = Field(..., description="部门ID列表")
    status: bool = Field(..., description="部门状态")


class DeptBatchUpdateStatusOut(Schema):
    """批量更新部门状态输出"""
    count: int = Field(..., description="更新的记录数")


class DeptPathOut(Schema):
    """部门路径输出"""
    dept_id: str
    dept_name: str
    path: List[DeptSchemaSimple] = Field(..., description="从根到当前部门的路径")


class DeptUserSchema(Schema):
    """部门用户信息"""
    id: str
    name: Optional[str]
    username: str
    dept_name: Optional[str] = None
    post_names: Optional[List[str]] = None

    @staticmethod
    def resolve_id(obj):
        """解析用户ID，将UUID转换为字符串"""
        return str(obj.id) if obj.id else None

    @staticmethod
    def resolve_dept_name(obj):
        """解析部门名称"""
        try:
            return obj.dept.name if obj.dept else None
        except Exception:
            return None

    @staticmethod
    def resolve_post_names(obj):
        """解析岗位名称列表"""
        return obj.get_post_names() if hasattr(obj, 'get_post_names') else []


class DeptUserIn(Schema):
    """部门用户操作输入"""
    user_ids: List[str] = Field(default=[], description="用户ID列表")
    user_id: Optional[str] = Field(None, description="单个用户ID")


class DeptUserFilter(Schema):
    """部门用户过滤器"""
    name: Optional[str] = Field(None, description="用户名称")
