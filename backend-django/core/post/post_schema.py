#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: post_schema.py
@Desc: Post Schema - 岗位数据验证模式
"""
"""
Post Schema - 岗位数据验证模式
"""
from datetime import datetime
from typing import Optional, List

from ninja import ModelSchema, Field, Schema, FilterSchema
from pydantic import field_validator

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.post.post_model import Post


class PostFilters(FuFilters):
    """岗位过滤器"""
    id: Optional[list] = Field(None, q="id__in", alias="id[]")
    name: Optional[str] = Field(None, q="name__icontains", alias="name")
    code: Optional[str] = Field(None, q="code__icontains", alias="code")
    post_type: Optional[int] = Field(None, q="post_type", alias="post_type")
    post_level: Optional[int] = Field(None, q="post_level", alias="post_level")
    status: Optional[bool] = Field(None, q="status", alias="status")
    dept_id: Optional[str] = Field(None, q="dept_id", alias="dept_id")
    sys_create_datetime: Optional[datetime] = Field(
        None, q="sys_create_datetime__date", alias="sys_create_datetime"
    )


class PostSchemaIn(ModelSchema):
    """岗位输入模式"""
    dept_id: Optional[str] = Field(None, alias="dept_id")

    @field_validator('code', check_fields=False)
    @classmethod
    def validate_code(cls, v):
        """验证岗位编码格式"""
        if not v:
            raise ValueError('岗位编码不能为空')
        if not all(c.isalnum() or c in '_-' for c in v):
            raise ValueError('岗位编码只能包含字母、数字、下划线和横线')
        return v

    @field_validator('post_type', check_fields=False)
    @classmethod
    def validate_post_type(cls, v):
        """验证岗位类型"""
        if v is not None and v not in [0, 1, 2, 3, 4]:
            raise ValueError('岗位类型必须在 0-4 之间')
        return v

    @field_validator('post_level', check_fields=False)
    @classmethod
    def validate_post_level(cls, v):
        """验证岗位级别"""
        if v is not None and v not in [0, 1, 2, 3]:
            raise ValueError('岗位级别必须在 0-3 之间')
        return v

    class Config:
        model = Post
        model_exclude = (*exclude_fields, "dept")


class PostSchemaPatch(Schema):
    """岗位部分更新模式（PATCH）"""
    name: Optional[str] = None
    code: Optional[str] = None
    post_type: Optional[int] = None
    post_level: Optional[int] = None
    dept_id: Optional[str] = None
    status: Optional[bool] = None
    description: Optional[str] = None

    @field_validator('code')
    @classmethod
    def validate_code(cls, v):
        """验证岗位编码格式"""
        if v is not None:
            if not v:
                raise ValueError('岗位编码不能为空')
            if not all(c.isalnum() or c in '_-' for c in v):
                raise ValueError('岗位编码只能包含字母、数字、下划线和横线')
        return v

    @field_validator('post_type')
    @classmethod
    def validate_post_type(cls, v):
        """验证岗位类型"""
        if v is not None and v not in [0, 1, 2, 3, 4]:
            raise ValueError('岗位类型必须在 0-4 之间')
        return v

    @field_validator('post_level')
    @classmethod
    def validate_post_level(cls, v):
        """验证岗位级别"""
        if v is not None and v not in [0, 1, 2, 3]:
            raise ValueError('岗位级别必须在 0-3 之间')
        return v


class PostSchemaOut(ModelSchema):
    """岗位输出模式"""
    dept_id: Optional[str] = None
    dept_name: Optional[str] = Field(None, alias="dept.name")
    post_type_display: Optional[str] = None
    post_level_display: Optional[str] = None
    user_count: Optional[int] = None

    class Config:
        model = Post
        model_fields = "__all__"

    @staticmethod
    def resolve_dept_id(obj):
        """解析部门ID，将UUID转换为字符串"""
        return str(obj.dept_id) if obj.dept_id else None

    @staticmethod
    def resolve_post_type_display(obj):
        """解析岗位类型显示名称"""
        return obj.get_post_type_display_name()

    @staticmethod
    def resolve_post_level_display(obj):
        """解析岗位级别显示名称"""
        return obj.get_post_level_display_name()

    @staticmethod
    def resolve_user_count(obj):
        """解析用户数量"""
        return obj.get_user_count()


class PostSchemaSimple(Schema):
    """岗位简单输出（用于选择器）"""
    id: str
    name: str
    code: str
    post_type: int
    post_level: int
    status: bool


class PostSchemaBatchDeleteIn(Schema):
    """批量删除岗位输入"""
    ids: List[str] = Field(..., description="要删除的岗位ID列表")


class PostSchemaBatchDeleteOut(Schema):
    """批量删除岗位输出"""
    count: int = Field(..., description="删除的记录数")
    failed_ids: List[str] = Field(default=[], description="删除失败的ID列表")


class PostBatchUpdateStatusIn(Schema):
    """批量更新岗位状态输入"""
    ids: List[str] = Field(..., description="岗位ID列表")
    status: bool = Field(..., description="岗位状态")


class PostBatchUpdateStatusOut(Schema):
    """批量更新岗位状态输出"""
    count: int = Field(..., description="更新的记录数")


class PostUserSchema(Schema):
    """岗位用户信息"""
    id: str
    name: Optional[str]
    username: str
    avatar: Optional[str]
    email: Optional[str]
    dept_name: Optional[str] = None

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


class PostUserIn(Schema):
    """岗位用户操作输入"""
    post_id: str = Field(..., description="岗位ID")
    user_ids: List[str] = Field(default=[], description="用户ID列表")
    user_id: Optional[str] = Field(None, description="单个用户ID")


class PostUserFilter(FilterSchema):
    """岗位用户过滤器"""
    post_id: str = Field(..., description="岗位ID")
    name: Optional[str] = Field(None, description="用户名称")


class PostStatsOut(Schema):
    """岗位统计输出"""
    total_count: int
    active_count: int
    inactive_count: int
    type_stats: dict
    level_stats: dict
