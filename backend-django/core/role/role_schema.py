#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: role_schema.py
@Desc: Role Schema - 角色数据验证模式
"""
"""
Role Schema - 角色数据验证模式
"""
from typing import Optional, List

from ninja import ModelSchema, Schema, Field, FilterSchema
from pydantic import field_validator

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.role.role_model import Role


class RoleFilters(FuFilters):
    """角色过滤器"""
    id: Optional[list] = Field(None, q="id__in", alias="id[]")
    name: Optional[str] = Field(None, q="name__icontains", alias="name")
    code: Optional[str] = Field(None, q="code__icontains", alias="code")
    status: Optional[bool] = Field(None, q="status", alias="status")
    role_type: Optional[int] = Field(None, q="role_type", alias="role_type")


class RoleSchemaIn(ModelSchema):
    """角色输入模式"""
    menu: List[str] = Field(default=[], description="菜单ID列表")
    permission: List[str] = Field(default=[], description="权限ID列表")
    dept: List[str] = Field(default=[], description="部门组ID列表")

    @field_validator('code', check_fields=False)
    @classmethod
    def validate_code(cls, v):
        """验证角色编码格式"""
        if not v:
            raise ValueError('角色编码不能为空')
        if not v.replace('_', '').isalnum():
            raise ValueError('角色编码只能包含字母、数字和下划线')
        return v

    @field_validator('role_type', check_fields=False)
    @classmethod
    def validate_role_type(cls, v):
        """验证角色类型"""
        if v not in [0, 1]:
            raise ValueError('角色类型必须为 0(系统角色) 或 1(自定义角色)')
        return v

    @field_validator('data_scope', check_fields=False)
    @classmethod
    def validate_data_scope(cls, v):
        """验证数据范围"""
        if v not in [0, 1, 2, 3, 4]:
            raise ValueError('数据范围必须在 0-4 之间')
        return v

    @field_validator('priority', check_fields=False)
    @classmethod
    def validate_priority(cls, v):
        """验证优先级"""
        if v < 0:
            raise ValueError('优先级不能为负数')
        return v

    class Config:
        model = Role
        model_exclude = (*exclude_fields, "menu", "permission", "dept")


class RoleSchemaPatch(Schema):
    """角色部分更新模式（PATCH）"""
    name: Optional[str] = None
    code: Optional[str] = None
    role_type: Optional[int] = None
    data_scope: Optional[int] = None
    priority: Optional[int] = None
    status: Optional[bool] = None
    description: Optional[str] = None
    remark: Optional[str] = None
    menu: Optional[List[str]] = None
    permission: Optional[List[str]] = None
    dept: Optional[List[str]] = None

    @field_validator('code')
    @classmethod
    def validate_code(cls, v):
        """验证角色编码格式"""
        if v is not None:
            if not v:
                raise ValueError('角色编码不能为空')
            if not v.replace('_', '').isalnum():
                raise ValueError('角色编码只能包含字母、数字和下划线')
        return v

    @field_validator('role_type')
    @classmethod
    def validate_role_type(cls, v):
        """验证角色类型"""
        if v is not None and v not in [0, 1]:
            raise ValueError('角色类型必须为 0(系统角色) 或 1(自定义角色)')
        return v

    @field_validator('data_scope')
    @classmethod
    def validate_data_scope(cls, v):
        """验证数据范围"""
        if v is not None and v not in [0, 1, 2, 3, 4]:
            raise ValueError('数据范围必须在 0-4 之间')
        return v

    @field_validator('priority')
    @classmethod
    def validate_priority(cls, v):
        """验证优先级"""
        if v is not None and v < 0:
            raise ValueError('优先级不能为负数')
        return v


class RoleSchemaOut(ModelSchema):
    """角色输出模式"""
    role_type_display: Optional[str] = None
    data_scope_display: Optional[str] = None
    user_count: Optional[int] = None
    menu_count: Optional[int] = None
    permission_count: Optional[int] = None
    can_delete: Optional[bool] = None

    class Config:
        model = Role
        model_fields = "__all__"

    @staticmethod
    def resolve_role_type_display(obj):
        """解析角色类型显示名称"""
        return obj.get_role_type_display_name()

    @staticmethod
    def resolve_data_scope_display(obj):
        """解析数据范围显示名称"""
        return obj.get_data_scope_display_name()

    @staticmethod
    def resolve_user_count(obj):
        """解析用户数量"""
        return obj.get_user_count()

    @staticmethod
    def resolve_menu_count(obj):
        """解析菜单数量"""
        return obj.get_menu_count()

    @staticmethod
    def resolve_permission_count(obj):
        """解析权限数量"""
        return obj.get_permission_count()

    @staticmethod
    def resolve_can_delete(obj):
        """解析是否可删除"""
        return obj.can_delete()


class RoleSchemaDetail(RoleSchemaOut):
    """角色详情输出模式（包含关联数据）"""
    menu_ids: Optional[List[str]] = None
    permission_ids: Optional[List[str]] = None
    dept_ids: Optional[List[str]] = None

    @staticmethod
    def resolve_menu_ids(obj):
        """解析菜单ID列表"""
        return [str(m.id) for m in obj.menu.all()]

    @staticmethod
    def resolve_permission_ids(obj):
        """解析权限ID列表"""
        return [str(p.id) for p in obj.permission.all()]

    @staticmethod
    def resolve_dept_ids(obj):
        """解析部门组ID列表"""
        return [str(g.id) for g in obj.dept.all()]


class RoleBatchDeleteIn(Schema):
    """批量删除角色输入"""
    ids: List[str] = Field(..., description="要删除的角色ID列表")


class RoleBatchDeleteOut(Schema):
    """批量删除角色输出"""
    count: int = Field(..., description="删除的记录数")
    failed_ids: List[str] = Field(default=[], description="删除失败的ID列表（系统角色）")


class RoleUserSchema(Schema):
    """角色关联的用户信息"""
    id: str
    name: Optional[str]
    username: Optional[str]
    avatar: Optional[str]
    email: Optional[str]
    mobile: Optional[str]
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


class RoleUserFilter(FilterSchema):
    """角色用户过滤器"""
    role_id: str = Field(..., description="角色ID")
    name: Optional[str] = Field(None, description="用户名称")


class RoleUserIn(Schema):
    """角色用户操作输入"""
    role_id: str = Field(..., description="角色ID")
    user_ids: List[str] = Field(default=[], description="用户ID列表")
    user_id: Optional[str] = Field(None, description="单个用户ID")


class RoleBatchUpdateStatusIn(Schema):
    """批量更新角色状态输入"""
    ids: List[str] = Field(..., description="角色ID列表")
    status: bool = Field(..., description="角色状态")


class RoleBatchUpdateStatusOut(Schema):
    """批量更新角色状态输出"""
    count: int = Field(..., description="更新的记录数")


class RoleMenuPermissionTree(Schema):
    """角色菜单权限树节点"""
    id: str
    title: str
    parent_id: Optional[str]
    children: Optional[List['RoleMenuPermissionTree']] = []
    permission_type: Optional[str] = None  # 'menu' or 'permission'


class RoleSimpleOut(Schema):
    """角色简单输出（用于选择器）"""
    id: str
    name: str
    code: str
    status: bool
    role_type: int


class RolePermissionUpdateIn(Schema):
    """更新角色权限输入"""
    permission_ids: List[str]


class RoleMenuUpdateIn(Schema):
    """更新角色菜单输入"""
    menu_ids: List[str]


class RoleMenuPermissionUpdateIn(Schema):
    """更新角色菜单和权限输入"""
    menu_ids: List[str]
    permission_ids: List[str]


class MenuItemOut(Schema):
    """菜单项输出"""
    id: str
    name: str
    label: str
    parent_id: Optional[str]
    checked: bool
    permission_count: int = 0
    children: Optional[List['MenuItemOut']] = []


class RoleMenuListOut(Schema):
    """角色菜单列表输出"""
    menu_tree: List[MenuItemOut]
    selected_menu_ids: List[str]


class PermissionItemOut(Schema):
    """权限项输出"""
    id: str
    name: str
    label: str
    code: str
    permission_type: int
    permission_type_display: str
    checked: bool


class MenuPermissionsOut(Schema):
    """菜单权限列表输出"""
    menu_id: str
    permissions: List[PermissionItemOut]
