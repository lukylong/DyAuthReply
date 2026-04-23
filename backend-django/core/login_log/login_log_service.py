#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: login_log_service.py
@Desc: 登录日志服务层 - Login Log Service - 处理登录日志的业务逻辑
"""
"""
登录日志服务层 - Login Log Service
处理登录日志的业务逻辑
"""
from datetime import timedelta
from typing import Optional, List, Dict

from django.db.models import Q, Count, Max
from django.utils import timezone

from core.login_log.login_log_model import LoginLog


class LoginLogService:
    """登录日志服务类 - 提供登录日志的业务操作"""

    @staticmethod
    def record_login(
            username: str,
            status: int,
            login_ip: str,
            user_id: Optional[str] = None,
            failure_reason: Optional[int] = None,
            failure_message: Optional[str] = None,
            ip_location: Optional[str] = None,
            user_agent: Optional[str] = None,
            browser_type: Optional[str] = None,
            os_type: Optional[str] = None,
            device_type: Optional[str] = None,
            session_id: Optional[str] = None,
            remark: Optional[str] = None,
            login_type: str = 'password',
    ) -> LoginLog:
        """
        记录登录日志
        
        Args:
            username: 用户名
            status: 登录状态（0-失败，1-成功）
            login_ip: 登录IP地址
            user_id: 用户ID
            failure_reason: 失败原因
            failure_message: 失败信息
            ip_location: IP属地
            user_agent: 用户代理
            browser_type: 浏览器类型
            os_type: 操作系统
            device_type: 设备类型
            session_id: 会话ID
            remark: 备注
            login_type: 登录方式
        
        Returns:
            LoginLog: 创建的登录日志对象
        """
        login_log = LoginLog(
            username=username,
            status=status,
            login_ip=login_ip,
            user_id=user_id,
            failure_reason=failure_reason,
            failure_message=failure_message,
            ip_location=ip_location,
            user_agent=user_agent,
            browser_type=browser_type,
            os_type=os_type,
            device_type=device_type,
            session_id=session_id,
            remark=remark,
            login_type=login_type,
        )
        login_log.save()
        return login_log

    @staticmethod
    def record_success_login(
            username: str,
            user_id: Optional[str] = None,
            login_ip: Optional[str] = None,
            ip_location: Optional[str] = None,
            user_agent: Optional[str] = None,
            browser_type: Optional[str] = None,
            os_type: Optional[str] = None,
            device_type: Optional[str] = None,
            session_id: Optional[str] = None,
            remark: Optional[str] = None,
            login_type: str = 'password',
    ) -> LoginLog:
        """
        记录成功登录
        
        Args:
            username: 用户名
            user_id: 用户ID
            login_ip: 登录IP地址
            ip_location: IP属地
            user_agent: 用户代理
            browser_type: 浏览器类型
            os_type: 操作系统
            device_type: 设备类型
            session_id: 会话ID
            remark: 备注
            login_type: 登录方式
        
        Returns:
            LoginLog: 创建的登录日志对象
        """
        return LoginLogService.record_login(
            username=username,
            status=1,
            login_ip=login_ip or "0.0.0.0",
            user_id=user_id,
            ip_location=ip_location,
            user_agent=user_agent,
            browser_type=browser_type,
            os_type=os_type,
            device_type=device_type,
            session_id=session_id,
            remark=remark,
            login_type=login_type,
        )

    @staticmethod
    def record_failed_login(
            username: str,
            login_ip: str,
            failure_reason: int,
            failure_message: Optional[str] = None,
            ip_location: Optional[str] = None,
            user_agent: Optional[str] = None,
            browser_type: Optional[str] = None,
            os_type: Optional[str] = None,
            device_type: Optional[str] = None,
            remark: Optional[str] = None,
    ) -> LoginLog:
        """
        记录失败登录
        
        Args:
            username: 用户名
            login_ip: 登录IP地址
            failure_reason: 失败原因
            failure_message: 失败信息
            ip_location: IP属地
            user_agent: 用户代理
            browser_type: 浏览器类型
            os_type: 操作系统
            device_type: 设备类型
            remark: 备注
        
        Returns:
            LoginLog: 创建的登录日志对象
        """
        return LoginLogService.record_login(
            username=username,
            status=0,
            login_ip=login_ip,
            failure_reason=failure_reason,
            failure_message=failure_message,
            ip_location=ip_location,
            user_agent=user_agent,
            browser_type=browser_type,
            os_type=os_type,
            device_type=device_type,
        )

    @staticmethod
    def get_user_login_count(
            user_id: Optional[str] = None,
            username: Optional[str] = None,
            days: int = 30,
    ) -> int:
        """
        获取用户登录次数（最近N天）
        
        Args:
            user_id: 用户ID
            username: 用户名
            days: 天数范围
        
        Returns:
            int: 登录次数
        """
        start_date = timezone.now() - timedelta(days=days)
        query = LoginLog.objects.filter(
            status=1,
            sys_create_datetime__gte=start_date,
        )

        if user_id:
            query = query.filter(user_id=user_id)
        elif username:
            query = query.filter(username=username)

        return query.count()

    @staticmethod
    def get_failed_login_count(
            user_id: Optional[str] = None,
            username: Optional[str] = None,
            days: int = 30,
    ) -> int:
        """
        获取用户失败登录次数（最近N天）
        
        Args:
            user_id: 用户ID
            username: 用户名
            days: 天数范围
        
        Returns:
            int: 失败登录次数
        """
        start_date = timezone.now() - timedelta(days=days)
        query = LoginLog.objects.filter(
            status=0,
            sys_create_datetime__gte=start_date,
        )

        if user_id:
            query = query.filter(user_id=user_id)
        elif username:
            query = query.filter(username=username)

        return query.count()

    @staticmethod
    def get_last_login(
            user_id: Optional[str] = None,
            username: Optional[str] = None,
    ) -> Optional[LoginLog]:
        """
        获取用户最后一次登录记录
        
        Args:
            user_id: 用户ID
            username: 用户名
        
        Returns:
            LoginLog: 最后一次登录记录，如果没有则返回None
        """
        query = LoginLog.objects.filter(status=1)

        if user_id:
            query = query.filter(user_id=user_id)
        elif username:
            query = query.filter(username=username)

        return query.order_by('-sys_create_datetime').first()

    @staticmethod
    def get_login_ips(
            user_id: Optional[str] = None,
            username: Optional[str] = None,
            days: int = 30,
    ) -> List[str]:
        """
        获取用户登录过的IP地址列表
        
        Args:
            user_id: 用户ID
            username: 用户名
            days: 天数范围
        
        Returns:
            List[str]: IP地址列表
        """
        start_date = timezone.now() - timedelta(days=days)
        query = LoginLog.objects.filter(
            status=1,
            sys_create_datetime__gte=start_date,
        )

        if user_id:
            query = query.filter(user_id=user_id)
        elif username:
            query = query.filter(username=username)

        ips = query.values_list('login_ip', flat=True).distinct()
        return list(ips)

    @staticmethod
    def get_suspicious_logins(
            max_failed_attempts: int = 5,
            hours: int = 1,
    ) -> List[Dict]:
        """
        获取可疑登录（短时间内失败次数过多）
        
        Args:
            max_failed_attempts: 失败次数阈值
            hours: 小时范围
        
        Returns:
            List[Dict]: 可疑登录信息列表
        """
        start_time = timezone.now() - timedelta(hours=hours)

        suspicious = LoginLog.objects.filter(
            status=0,
            sys_create_datetime__gte=start_time,
        ).values('username', 'login_ip').annotate(
            count=Count('id'),
            last_attempt=Max('sys_create_datetime'),
        ).filter(count__gte=max_failed_attempts)

        return list(suspicious)

    @staticmethod
    def get_login_stats(
            days: int = 30,
    ) -> Dict:
        """
        获取登录统计信息
        
        Args:
            days: 天数范围
        
        Returns:
            Dict: 统计信息
        """
        start_date = timezone.now() - timedelta(days=days)

        query = LoginLog.objects.filter(sys_create_datetime__gte=start_date)

        total = query.count()
        success = query.filter(status=1).count()
        failed = query.filter(status=0).count()
        unique_users = query.values('user_id').distinct().count()
        unique_ips = query.values('login_ip').distinct().count()

        success_rate = (success / total * 100) if total > 0 else 0

        return {
            'total_logins': total,
            'success_logins': success,
            'failed_logins': failed,
            'success_rate': round(success_rate, 2),
            'unique_users': unique_users,
            'unique_ips': unique_ips,
        }

    @staticmethod
    def get_ip_stats(
            days: int = 30,
            limit: int = 10,
    ) -> List[Dict]:
        """
        获取IP登录统计（TOP N）
        
        Args:
            days: 天数范围
            limit: 限制数量
        
        Returns:
            List[Dict]: IP统计信息
        """
        start_date = timezone.now() - timedelta(days=days)

        stats = LoginLog.objects.filter(
            sys_create_datetime__gte=start_date,
        ).values('login_ip', 'ip_location').annotate(
            login_count=Count('id', filter=Q(status=1)),
            failed_count=Count('id', filter=Q(status=0)),
            last_login_time=Max('sys_create_datetime'),
        ).order_by('-login_count')[:limit]

        return list(stats)

    @staticmethod
    def get_device_stats(
            days: int = 30,
    ) -> List[Dict]:
        """
        获取设备登录统计
        
        Args:
            days: 天数范围
        
        Returns:
            List[Dict]: 设备统计信息
        """
        start_date = timezone.now() - timedelta(days=days)

        stats = LoginLog.objects.filter(
            status=1,
            sys_create_datetime__gte=start_date,
        ).values('device_type', 'browser_type', 'os_type').annotate(
            login_count=Count('id'),
            last_login_time=Max('sys_create_datetime'),
        ).order_by('-login_count')

        return list(stats)

    @staticmethod
    def get_user_stats(
            days: int = 30,
            limit: int = 10,
    ) -> List[Dict]:
        """
        获取用户登录统计（TOP N）
        
        Args:
            days: 天数范围
            limit: 限制数量
        
        Returns:
            List[Dict]: 用户统计信息
        """
        start_date = timezone.now() - timedelta(days=days)

        stats = LoginLog.objects.filter(
            sys_create_datetime__gte=start_date,
        ).values('user_id', 'username').annotate(
            total_logins=Count('id', filter=Q(status=1)),
            failed_logins=Count('id', filter=Q(status=0)),
            last_login_time=Max('sys_create_datetime', filter=Q(status=1)),
            last_login_ip=Max(
                'login_ip',
                filter=Q(status=1),
            ),
        ).order_by('-total_logins')[:limit]

        return list(stats)

    @staticmethod
    def get_daily_stats(
            days: int = 30,
    ) -> List[Dict]:
        """
        获取每日登录统计
        
        Args:
            days: 天数范围
        
        Returns:
            List[Dict]: 每日统计信息
        """
        from django.db.models.functions import TruncDate

        start_date = timezone.now() - timedelta(days=days)

        stats = LoginLog.objects.filter(
            sys_create_datetime__gte=start_date,
        ).annotate(
            date=TruncDate('sys_create_datetime')
        ).values('date').annotate(
            total_logins=Count('id'),
            success_logins=Count('id', filter=Q(status=1)),
            failed_logins=Count('id', filter=Q(status=0)),
            unique_users=Count('user_id', distinct=True),
        ).order_by('date')

        return list(stats)

    @staticmethod
    def clean_old_logs(
            days: int = 90,
    ) -> int:
        """
        清理旧的登录日志（默认保留90天）
        
        Args:
            days: 天数范围
        
        Returns:
            int: 删除的记录数
        """
        cutoff_date = timezone.now() - timedelta(days=days)
        deleted_count, _ = LoginLog.objects.filter(
            sys_create_datetime__lt=cutoff_date,
        ).delete()
        return deleted_count

    @staticmethod
    def check_user_locked(
            username: str,
            failed_threshold: int = 5,
            hours: int = 1,
    ) -> bool:
        """
        检查用户是否应该被锁定（失败次数过多）
        
        Args:
            username: 用户名
            failed_threshold: 失败次数阈值
            hours: 小时范围
        
        Returns:
            bool: 是否应该被锁定
        """
        start_time = timezone.now() - timedelta(hours=hours)

        failed_count = LoginLog.objects.filter(
            username=username,
            status=0,
            sys_create_datetime__gte=start_time,
        ).count()

        return failed_count >= failed_threshold
