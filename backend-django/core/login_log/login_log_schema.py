#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: login_log_schema.py
@Desc: 登录日志数据验证模式 - Login Log Schema
"""
"""
登录日志数据验证模式 - Login Log Schema
"""
from datetime import datetime
from typing import Optional

from ninja import ModelSchema, Field, Schema
from pydantic import field_validator

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.login_log.login_log_model import LoginLog


class LoginLogFilters(FuFilters):
    """登录日志过滤器"""
    username: Optional[str] = Field(None, q="username__icontains", alias="username")
    user_id: Optional[str] = Field(None, q="user_id", alias="user_id")
    status: Optional[int] = Field(None, q="status", alias="status")
    failure_reason: Optional[int] = Field(None, q="failure_reason", alias="failure_reason")
    login_ip: Optional[str] = Field(None, q="login_ip__icontains", alias="login_ip")
    device_type: Optional[str] = Field(None, q="device_type", alias="device_type")
    browser_type: Optional[str] = Field(None, q="browser_type", alias="browser_type")
    os_type: Optional[str] = Field(None, q="os_type", alias="os_type")
    start_datetime: Optional[datetime] = Field(None, q="sys_create_datetime__gte", alias="start_datetime")
    end_datetime: Optional[datetime] = Field(None, q="sys_create_datetime__lte", alias="end_datetime")


class LoginLogSchemaIn(ModelSchema):
    """登录日志输入模式"""

    @field_validator('username', check_fields=False)
    @classmethod
    def validate_username(cls, v):
        """验证用户名"""
        if not v:
            raise ValueError('用户名不能为空')
        return v

    @field_validator('status', check_fields=False)
    @classmethod
    def validate_status(cls, v):
        """验证登录状态"""
        if v not in [0, 1]:
            raise ValueError('登录状态必须为 0(失败) 或 1(成功)')
        return v

    class Config:
        model = LoginLog
        model_exclude = (
            "session_id",
            *exclude_fields,
        )


class LoginLogSchemaOut(ModelSchema):
    """登录日志输出模式"""
    status_display: Optional[str] = None
    failure_reason_display: Optional[str] = None

    class Config:
        model = LoginLog
        model_fields = "__all__"

    @staticmethod
    def resolve_status_display(obj):
        """解析登录状态显示名称"""
        return obj.get_status_display_name()

    @staticmethod
    def resolve_failure_reason_display(obj):
        """解析登录失败原因显示名称"""
        return obj.get_failure_reason_display_name()


class LoginLogStatsOut(Schema):
    """登录统计输出"""
    total_logins: int = Field(..., description="总登录次数")
    success_logins: int = Field(..., description="成功登录次数")
    failed_logins: int = Field(..., description="失败登录次数")
    success_rate: float = Field(..., description="成功率（%）")
    unique_users: int = Field(..., description="登录用户数")
    unique_ips: int = Field(..., description="登录IP数")


class LoginLogIpStatsOut(Schema):
    """IP登录统计"""
    login_ip: str = Field(..., description="IP地址")
    ip_location: Optional[str] = Field(None, description="IP属地")
    login_count: int = Field(..., description="登录次数")
    failed_count: int = Field(..., description="失败次数")
    last_login_time: Optional[datetime] = Field(None, description="最后登录时间")


class LoginLogDeviceStatsOut(Schema):
    """设备登录统计"""
    device_type: Optional[str] = Field(..., description="设备类型")
    browser_type: Optional[str] = Field(..., description="浏览器类型")
    os_type: Optional[str] = Field(..., description="操作系统")
    login_count: int = Field(..., description="登录次数")
    last_login_time: Optional[datetime] = Field(None, description="最后登录时间")


class LoginLogUserStatsOut(Schema):
    """用户登录统计"""
    user_id: Optional[str] = Field(None, description="用户ID")
    username: str = Field(..., description="用户名")
    total_logins: int = Field(..., description="登录次数")
    failed_logins: int = Field(..., description="失败次数")
    last_login_time: Optional[datetime] = Field(None, description="最后登录时间")
    last_login_ip: Optional[str] = Field(None, description="最后登录IP")


class LoginLogRecordIn(Schema):
    """记录登录日志输入"""
    username: str = Field(..., description="用户名")
    user_id: Optional[str] = Field(None, description="用户ID")
    status: int = Field(..., description="登录状态：0-失败，1-成功")
    failure_reason: Optional[int] = Field(None, description="失败原因")
    failure_message: Optional[str] = Field(None, description="失败信息")
    login_ip: str = Field(..., description="登录IP")
    ip_location: Optional[str] = Field(None, description="IP属地")
    user_agent: Optional[str] = Field(None, description="用户代理")
    browser_type: Optional[str] = Field(None, description="浏览器类型")
    os_type: Optional[str] = Field(None, description="操作系统")
    device_type: Optional[str] = Field(None, description="设备类型")
    session_id: Optional[str] = Field(None, description="会话ID")
    remark: Optional[str] = Field(None, description="备注")


class LoginLogDailyStatsOut(Schema):
    """每日登录统计"""
    date: str = Field(..., description="日期")
    total_logins: int = Field(..., description="登录总数")
    success_logins: int = Field(..., description="成功登录数")
    failed_logins: int = Field(..., description="失败登录数")
    unique_users: int = Field(..., description="登录用户数")
