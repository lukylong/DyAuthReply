#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: login_log_api.py
@Desc: 登录日志API - Login Log API - 提供登录日志的查询、分析和管理接口
"""
"""
登录日志API - Login Log API
提供登录日志的查询、分析和管理接口
"""
from datetime import timedelta
from typing import List

from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Router, Query
from ninja.errors import HttpError
from ninja.pagination import paginate

from common.fu_crud import retrieve
from common.fu_pagination import MyPagination
from common.fu_schema import response_success
from core.login_log.login_log_model import LoginLog
from core.login_log.login_log_schema import (
    LoginLogFilters,
    LoginLogSchemaOut,
    LoginLogStatsOut,
    LoginLogIpStatsOut,
    LoginLogDeviceStatsOut,
    LoginLogUserStatsOut,
    LoginLogRecordIn,
    LoginLogDailyStatsOut,
)
from core.login_log.login_log_service import LoginLogService

router = Router()


@router.get("/login-log", response=List[LoginLogSchemaOut], summary="获取登录日志列表（分页）")
@paginate(MyPagination)
def list_login_logs(request, filters: LoginLogFilters = Query(...)):
    """
    获取登录日志列表（分页）
    
    支持的过滤条件：
    - username: 用户名（模糊搜索）
    - user_id: 用户ID
    - status: 登录状态（0-失败，1-成功）
    - failure_reason: 失败原因
    - login_ip: 登录IP（模糊搜索）
    - device_type: 设备类型
    - browser_type: 浏览器类型
    - os_type: 操作系统
    - start_datetime: 开始时间
    - end_datetime: 结束时间
    """
    query_set = retrieve(request, LoginLog, filters)
    return query_set.order_by('-sys_create_datetime')


@router.get("/login-log/{log_id}", response=LoginLogSchemaOut, summary="获取登录日志详情")
def get_login_log(request, log_id: str):
    """获取单条登录日志的详细信息"""
    log = get_object_or_404(LoginLog, id=log_id)
    return log


@router.delete("/login-log/{log_id}", summary="删除登录日志")
def delete_login_log(request, log_id: str):
    """删除单条登录日志"""
    log = get_object_or_404(LoginLog, id=log_id)
    log.delete()
    return response_success("登录日志已删除")


@router.delete("/login-log/batch/delete", summary="批量删除登录日志")
def batch_delete_login_logs(request, ids: List[str] = Query(...)):
    """批量删除登录日志"""
    deleted_count, _ = LoginLog.objects.filter(id__in=ids).delete()
    return response_success(f"成功删除 {deleted_count} 条登录日志", data={"deleted_count": deleted_count})


@router.post("/login-log/record", response=LoginLogSchemaOut, summary="记录登录日志")
def record_login_log(request, data: LoginLogRecordIn):
    """
    记录登录日志
    
    主要用于认证系统调用，记录用户登录操作
    """
    log = LoginLogService.record_login(
        username=data.username,
        status=data.status,
        login_ip=data.login_ip,
        user_id=data.user_id,
        failure_reason=data.failure_reason,
        failure_message=data.failure_message,
        ip_location=data.ip_location,
        user_agent=data.user_agent,
        browser_type=data.browser_type,
        os_type=data.os_type,
        device_type=data.device_type,
        session_id=data.session_id,
        remark=data.remark,
    )
    return log


@router.get("/login-log/stats/overview", response=LoginLogStatsOut, summary="获取登录统计概览")
def get_login_stats(request, days: int = Query(30, description="统计天数")):
    """
    获取登录统计概览
    
    包括：
    - 总登录次数
    - 成功登录次数
    - 失败登录次数
    - 成功率
    - 登录用户数
    - 登录IP数
    """
    stats = LoginLogService.get_login_stats(days=days)
    return LoginLogStatsOut(**stats)


@router.get("/login-log/stats/ip", response=List[LoginLogIpStatsOut], summary="获取IP登录统计")
def get_ip_stats(
        request,
        days: int = Query(30, description="统计天数"),
        limit: int = Query(10, description="限制数量"),
):
    """
    获取IP登录统计（TOP N）
    
    显示登录最频繁的IP地址及其登录情况
    """
    stats = LoginLogService.get_ip_stats(days=days, limit=limit)
    return [LoginLogIpStatsOut(**item) for item in stats]


@router.get("/login-log/stats/device", response=List[LoginLogDeviceStatsOut], summary="获取设备登录统计")
def get_device_stats(
        request,
        days: int = Query(30, description="统计天数"),
):
    """
    获取设备登录统计
    
    按设备类型、浏览器、操作系统统计登录情况
    """
    stats = LoginLogService.get_device_stats(days=days)
    return [LoginLogDeviceStatsOut(**item) for item in stats]


@router.get("/login-log/stats/user", response=List[LoginLogUserStatsOut], summary="获取用户登录统计")
def get_user_stats(
        request,
        days: int = Query(30, description="统计天数"),
        limit: int = Query(10, description="限制数量"),
):
    """
    获取用户登录统计（TOP N）
    
    显示登录最频繁的用户及其登录情况
    """
    stats = LoginLogService.get_user_stats(days=days, limit=limit)
    return [LoginLogUserStatsOut(**item) for item in stats]


@router.get("/login-log/stats/daily", response=List[LoginLogDailyStatsOut], summary="获取每日登录统计")
def get_daily_stats(
        request,
        days: int = Query(30, description="统计天数"),
):
    """
    获取每日登录统计
    
    显示每日的登录、成功、失败统计
    """
    stats = LoginLogService.get_daily_stats(days=days)
    return [LoginLogDailyStatsOut(**item) for item in stats]


@router.get("/login-log/user/{user_id}", response=List[LoginLogSchemaOut], summary="获取用户的登录日志")
@paginate(MyPagination)
def get_user_login_logs(
        request,
        user_id: str,
        days: int = Query(30, description="天数范围"),
):
    """获取指定用户的登录日志"""
    start_date = timezone.now() - timedelta(days=days)
    query_set = LoginLog.objects.filter(
        user_id=user_id,
        sys_create_datetime__gte=start_date,
    ).order_by('-sys_create_datetime')
    return query_set


@router.get("/login-log/user/{user_id}/count", summary="获取用户登录次数")
def get_user_login_count(
        request,
        user_id: str,
        days: int = Query(30, description="天数范围"),
):
    """获取用户登录次数（最近N天）"""
    count = LoginLogService.get_user_login_count(user_id=user_id, days=days)
    failed_count = LoginLogService.get_failed_login_count(user_id=user_id, days=days)
    return response_success(
        "获取成功",
        data={
            "user_id": user_id,
            "total_logins": count,
            "failed_logins": failed_count,
            "success_logins": count - failed_count,
        }
    )


@router.get("/login-log/user/{user_id}/last", response=LoginLogSchemaOut, summary="获取用户最后一次登录")
def get_user_last_login(request, user_id: str):
    """获取用户最后一次登录记录"""
    log = LoginLogService.get_last_login(user_id=user_id)
    if not log:
        raise HttpError(404, "未找到用户的登录记录")
    return log


@router.get("/login-log/user/{user_id}/ips", summary="获取用户登录过的IP地址")
def get_user_login_ips(
        request,
        user_id: str,
        days: int = Query(30, description="天数范围"),
):
    """获取用户最近登录过的IP地址列表"""
    ips = LoginLogService.get_login_ips(user_id=user_id, days=days)
    return response_success(
        "获取成功",
        data={
            "user_id": user_id,
            "ips": ips,
            "ip_count": len(ips),
        }
    )


@router.get("/login-log/suspicious", summary="获取可疑登录记录")
def get_suspicious_logins(
        request,
        failed_threshold: int = Query(5, description="失败次数阈值"),
        hours: int = Query(1, description="小时范围"),
):
    """
    获取可疑登录记录
    
    显示短时间内失败次数过多的登录尝试
    """
    suspicious = LoginLogService.get_suspicious_logins(
        max_failed_attempts=failed_threshold,
        hours=hours,
    )
    return response_success(
        "获取成功",
        data={
            "suspicious_count": len(suspicious),
            "records": suspicious,
        }
    )


@router.post("/login-log/clean", summary="清理旧的登录日志")
def clean_old_logs(
        request,
        days: int = Query(90, description="保留天数"),
):
    """
    清理旧的登录日志
    
    删除指定天数前的登录日志记录
    """
    # TODO: 添加权限检查，仅管理员可操作
    deleted_count = LoginLogService.clean_old_logs(days=days)
    return response_success(
        f"成功清理 {deleted_count} 条旧登录日志",
        data={"deleted_count": deleted_count}
    )


@router.post("/login-log/export", summary="导出登录日志")
def export_login_logs(request):
    """
    导出登录日志为CSV或Excel
    
    用于数据备份和审计
    """
    # TODO: 实现导出功能
    return response_success("导出功能待实现")


@router.get("/login-log/username/{username}", response=List[LoginLogSchemaOut], summary="根据用户名获取登录日志")
@paginate(MyPagination)
def get_logs_by_username(
        request,
        username: str,
        days: int = Query(30, description="天数范围"),
):
    """根据用户名获取登录日志"""
    start_date = timezone.now() - timedelta(days=days)
    query_set = LoginLog.objects.filter(
        username=username,
        sys_create_datetime__gte=start_date,
    ).order_by('-sys_create_datetime')
    return query_set


@router.get("/login-log/ip/{login_ip}", response=List[LoginLogSchemaOut], summary="根据IP地址获取登录日志")
@paginate(MyPagination)
def get_logs_by_ip(
        request,
        login_ip: str,
        days: int = Query(30, description="天数范围"),
):
    """根据IP地址获取登录日志"""
    start_date = timezone.now() - timedelta(days=days)
    query_set = LoginLog.objects.filter(
        login_ip=login_ip,
        sys_create_datetime__gte=start_date,
    ).order_by('-sys_create_datetime')
    return query_set


@router.get("/login-log/failed-attempts/{username}", summary="获取用户登录失败次数")
def get_failed_attempts(
        request,
        username: str,
        hours: int = Query(1, description="小时范围"),
):
    """
    获取用户在指定时间内的登录失败次数
    
    用于判断是否应该锁定账户
    """
    from datetime import timedelta

    start_time = timezone.now() - timedelta(hours=hours)

    failed_count = LoginLog.objects.filter(
        username=username,
        status=0,
        sys_create_datetime__gte=start_time,
    ).count()

    should_lock = LoginLogService.check_user_locked(
        username=username,
        failed_threshold=5,
        hours=hours,
    )

    return response_success(
        "获取成功",
        data={
            "username": username,
            "failed_attempts": failed_count,
            "should_lock": should_lock,
        }
    )
