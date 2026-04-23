#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: user_schema.py
@Desc: User Schema - 用户数据验证模式
"""
"""
User Schema - 用户数据验证模式
"""
from datetime import date
from typing import Optional, List

from ninja import ModelSchema, Field, Schema
from pydantic import field_validator

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.user.user_model import User


class UserFilters(FuFilters):
    """用户过滤器"""
    name: Optional[str] = Field(None, q="name__icontains", alias="name")
    username: Optional[str] = Field(None, q="username__icontains", alias="username")
    user_status: Optional[int] = Field(None, q="user_status", alias="user_status")
    user_type: Optional[int] = Field(None, q="user_type", alias="user_type")
    dept_id: Optional[list] = Field(None, q="dept_id__in", alias="dept_ids")
    id: Optional[str] = Field(None, q="id", alias="id")
    mobile: Optional[str] = Field(None, q="mobile__icontains", alias="mobile")
    email: Optional[str] = Field(None, q="email__icontains", alias="email")


class UserSchemaIn(ModelSchema):
    """用户输入模式"""
    dept_id: Optional[str] = Field(None, alias="dept_id")
    manager_id: Optional[str] = Field(None, alias="manager_id")
    post: List[str] = Field(default=[], description="岗位ID列表")
    core_roles: List[str] = Field(default=[], description="角色ID列表")

    @field_validator('username', check_fields=False)
    @classmethod
    def validate_username(cls, v):
        """验证用户名"""
        if not v:
            raise ValueError('用户名不能为空')
        if len(v) < 3:
            raise ValueError('用户名长度不能少于3个字符')
        if len(v) > 150:
            raise ValueError('用户名长度不能超过150个字符')
        return v

    @field_validator('mobile', check_fields=False)
    @classmethod
    def validate_mobile(cls, v):
        """验证手机号"""
        if v and not v.isdigit():
            raise ValueError('手机号只能包含数字')
        if v and len(v) != 11:
            raise ValueError('手机号必须为11位')
        return v

    @field_validator('user_status', check_fields=False)
    @classmethod
    def validate_user_status(cls, v):
        """验证用户状态"""
        if v not in [0, 1, 2]:
            raise ValueError('用户状态必须为 0(禁用)、1(正常) 或 2(锁定)')
        return v

    @field_validator('user_type', check_fields=False)
    @classmethod
    def validate_user_type(cls, v):
        """验证用户类型"""
        if v not in [0, 1, 2]:
            raise ValueError('用户类型必须为 0(系统用户)、1(普通用户) 或 2(外部用户)')
        return v

    @field_validator('gender', check_fields=False)
    @classmethod
    def validate_gender(cls, v):
        """验证性别"""
        if v not in [0, 1, 2]:
            raise ValueError('性别必须为 0(未知)、1(男) 或 2(女)')
        return v

    class Config:
        model = User
        model_exclude = (
            "password",
            "is_superuser",
            "post",
            "core_roles",
            "dept",
            "last_login_ip",
            "last_login",
            "manager",
            *exclude_fields,
        )


class UserSchemaPatch(Schema):
    """用户部分更新模式（PATCH）"""
    username: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    mobile: Optional[str] = None
    avatar: Optional[str] = None
    gender: Optional[int] = None
    user_type: Optional[int] = None
    user_status: Optional[int] = None
    is_active: Optional[bool] = None
    dept_id: Optional[str] = None
    manager_id: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    employee_no: Optional[str] = None
    date_joined: Optional[date] = None
    post: Optional[List[str]] = None
    core_roles: Optional[List[str]] = None

    @field_validator('username')
    @classmethod
    def validate_username(cls, v):
        """验证用户名"""
        if v is not None:
            if not v:
                raise ValueError('用户名不能为空')
            if len(v) < 3:
                raise ValueError('用户名长度不能少于3个字符')
            if len(v) > 150:
                raise ValueError('用户名长度不能超过150个字符')
        return v

    @field_validator('mobile')
    @classmethod
    def validate_mobile(cls, v):
        """验证手机号"""
        if v is not None:
            if v and not v.isdigit():
                raise ValueError('手机号只能包含数字')
            if v and len(v) != 11:
                raise ValueError('手机号必须为11位')
        return v

    @field_validator('user_status')
    @classmethod
    def validate_user_status(cls, v):
        """验证用户状态"""
        if v is not None and v not in [0, 1, 2]:
            raise ValueError('用户状态必须为 0(禁用)、1(正常) 或 2(锁定)')
        return v

    @field_validator('user_type')
    @classmethod
    def validate_user_type(cls, v):
        """验证用户类型"""
        if v is not None and v not in [0, 1, 2]:
            raise ValueError('用户类型必须为 0(系统用户)、1(普通用户) 或 2(外部用户)')
        return v

    @field_validator('gender')
    @classmethod
    def validate_gender(cls, v):
        """验证性别"""
        if v is not None and v not in [0, 1, 2]:
            raise ValueError('性别必须为 0(未知)、1(男) 或 2(女)')
        return v


class UserSchemaOut(ModelSchema):
    """用户输出模式"""
    dept_id: Optional[str] = Field(None, alias="dept_id")
    dept_name: Optional[str] = Field(None, alias="dept.name")
    manager_id: Optional[str] = Field(None, alias="manager_id")
    manager_name: Optional[str] = Field(None, alias="manager.name")
    user_type_display: Optional[str] = None
    user_status_display: Optional[str] = None
    gender_display: Optional[str] = None
    role_names: Optional[List[str]] = None
    post_names: Optional[List[str]] = None

    class Config:
        model = User
        model_exclude = ("password",)

    @staticmethod
    def resolve_user_type_display(obj):
        """解析用户类型显示名称"""
        return obj.get_user_type_display_name()

    @staticmethod
    def resolve_user_status_display(obj):
        """解析用户状态显示名称"""
        return obj.get_user_status_display_name()

    @staticmethod
    def resolve_gender_display(obj):
        """解析性别显示名称"""
        return obj.get_gender_display_name()

    @staticmethod
    def resolve_role_names(obj):
        """解析角色名称列表"""
        return obj.get_role_names()

    @staticmethod
    def resolve_post_names(obj):
        """解析岗位名称列表"""
        return obj.get_post_names()


class UserSchemaDetail(UserSchemaOut):
    """用户详情输出模式（包含更多信息）"""
    role_ids: Optional[List[str]] = None
    post_ids: Optional[List[str]] = None
    permissions: Optional[List[str]] = None

    @staticmethod
    def resolve_role_ids(obj):
        """解析角色ID列表"""
        return [str(role.id) for role in obj.core_roles.all()]

    @staticmethod
    def resolve_post_ids(obj):
        """解析岗位ID列表"""
        return [str(post.id) for post in obj.post.all()]

    @staticmethod
    def resolve_permissions(obj):
        """解析用户权限列表"""
        permissions = obj.get_all_permissions()
        return [perm.code for perm in permissions]


class UserSchemaSimple(Schema):
    """用户简单输出（用于选择器）"""
    id: str
    name: Optional[str]
    username: str
    avatar: Optional[str]
    email: Optional[str]
    mobile: Optional[str]
    dept_name: Optional[str] = Field(None, alias="dept.name")


class UserSchemaAvatarOut(Schema):
    """用户头像输出"""
    avatar: Optional[str]
    name: Optional[str]
    id: Optional[str]


class UserSchemaGetNameIn(Schema):
    """获取用户名称输入"""
    ids: Optional[List[str]] = None
    level: Optional[int] = 1
    until: Optional[int] = 1


class UserSchemaBatchDeleteIn(Schema):
    """批量删除用户输入"""
    ids: List[str] = Field(..., description="要删除的用户ID列表")


class UserSchemaBatchDeleteOut(Schema):
    """批量删除用户输出"""
    count: int = Field(..., description="删除的记录数")
    failed_ids: List[str] = Field(default=[], description="删除失败的ID列表")


class UserPasswordResetIn(Schema):
    """重置密码输入"""
    old_password: str = Field(..., description="旧密码")
    new_password: str = Field(..., description="新密码")
    confirm_password: str = Field(..., description="确认新密码")

    @field_validator('new_password', check_fields=False)
    @classmethod
    def validate_new_password(cls, v):
        """验证新密码"""
        if len(v) < 6:
            raise ValueError('新密码长度不能少于6个字符')
        if len(v) > 20:
            raise ValueError('新密码长度不能超过20个字符')
        return v

    @field_validator('confirm_password', check_fields=False)
    @classmethod
    def validate_confirm_password(cls, v, info):
        """验证确认密码"""
        if info.data.get('new_password') and v != info.data.get('new_password'):
            raise ValueError('两次输入的密码不一致')
        return v


class UserBatchUpdateStatusIn(Schema):
    """批量更新用户状态输入"""
    ids: List[str] = Field(..., description="用户ID列表")
    user_status: int = Field(..., description="用户状态：0-禁用，1-正常，2-锁定")

    @field_validator('user_status', check_fields=False)
    @classmethod
    def validate_user_status(cls, v):
        """验证用户状态"""
        if v not in [0, 1, 2]:
            raise ValueError('用户状态必须为 0(禁用)、1(正常) 或 2(锁定)')
        return v


class UserBatchUpdateStatusOut(Schema):
    """批量更新用户状态输出"""
    count: int = Field(..., description="更新的记录数")


class UserProfileUpdateIn(Schema):
    """用户个人信息更新输入"""
    name: Optional[str] = None
    email: Optional[str] = None
    mobile: Optional[str] = None
    avatar: Optional[str] = None
    gender: Optional[int] = None
    birthday: Optional[date] = None
    city: Optional[str] = None
    address: Optional[str] = None
    bio: Optional[str] = None

    @field_validator('mobile', check_fields=False)
    @classmethod
    def validate_mobile(cls, v):
        """验证手机号"""
        if v and not v.isdigit():
            raise ValueError('手机号只能包含数字')
        if v and len(v) != 11:
            raise ValueError('手机号必须为11位')
        return v

    @field_validator('gender', check_fields=False)
    @classmethod
    def validate_gender(cls, v):
        """验证性别"""
        if v is not None and v not in [0, 1, 2]:
            raise ValueError('性别必须为 0(未知)、1(男) 或 2(女)')
        return v


class UserPermissionCheckIn(Schema):
    """用户权限检查输入"""
    permission_codes: List[str] = Field(..., description="权限编码列表")


class UserPermissionCheckOut(Schema):
    """用户权限检查输出"""
    permissions: dict = Field(..., description="权限检查结果，格式：{permission_code: has_permission}")


class UserSubordinatesOut(Schema):
    """用户下属列表输出"""
    subordinates: List[UserSchemaSimple]
