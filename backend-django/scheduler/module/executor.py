#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: executor.py
@Desc: Scheduler Executor - 任务执行器 - 负责任务的实际执行和日志记录
"""
"""
Scheduler Executor - 任务执行器
负责任务的实际执行和日志记录
"""
import logging
import os
import socket
import time
import traceback
from datetime import datetime
from functools import wraps
from typing import Any, Callable

logger = logging.getLogger(__name__)


def create_execution_log(job_obj):
    """创建任务执行日志"""
    try:
        from scheduler.models import SchedulerLog

        log = SchedulerLog.objects.create(
            job=job_obj,
            job_name=job_obj.name,
            job_code=job_obj.code,
            status='pending',
            start_time=datetime.now(),
            hostname=socket.gethostname(),
            process_id=os.getpid(),
        )
        return log
    except Exception as e:
        logger.error(f"创建执行日志失败: {str(e)}")
        return None


def update_execution_log(log, status, result=None, exception=None, tb=None):
    """更新任务执行日志"""
    try:
        if log:
            log.status = status
            log.end_time = datetime.now()
            log.duration = (log.end_time - log.start_time).total_seconds()

            if result is not None:
                log.result = str(result)[:5000]  # 限制长度

            if exception is not None:
                log.exception = str(exception)[:2000]  # 限制长度

            if tb is not None:
                log.traceback = tb[:5000]  # 限制长度

            log.save()
    except Exception as e:
        logger.error(f"更新执行日志失败: {str(e)}")


def scheduler_task(func: Callable) -> Callable:
    """
    定时任务装饰器
    
    功能：
    1. 自动创建执行日志
    2. 捕获异常并记录
    3. 记录执行时间
    4. 更新任务统计信息
    
    使用示例：
        @scheduler_task
        def my_task(arg1, arg2):
            # 任务逻辑
            pass
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        job_code = kwargs.get('job_code')
        log = None

        try:
            # 获取任务对象
            from scheduler.models import SchedulerJob
            job_obj = SchedulerJob.objects.filter(code=job_code).first()

            if not job_obj:
                logger.error(f"任务不存在: {job_code}")
                return None

            # 创建执行日志
            log = create_execution_log(job_obj)

            if log:
                log.status = 'running'
                log.save(update_fields=['status'])

            # 执行任务
            logger.info(f"开始执行任务: {job_code}")
            start_time = time.time()

            result = func(*args, **kwargs)

            duration = time.time() - start_time
            logger.info(f"任务执行完成: {job_code}, 耗时: {duration:.2f}秒")

            # 更新日志为成功
            update_execution_log(log, 'success', result=result)

            return result

        except Exception as e:
            logger.error(f"任务执行失败: {job_code}, 错误: {str(e)}")

            # 获取异常堆栈
            tb = traceback.format_exc()

            # 更新日志为失败
            update_execution_log(log, 'failed', exception=e, tb=tb)

            # 重新抛出异常，让 APScheduler 处理
            raise

    return wrapper


class TaskExecutor:
    """
    任务执行器
    
    提供任务执行的辅助方法
    """

    @staticmethod
    def execute_with_retry(func: Callable, max_retries: int = 3, *args, **kwargs) -> Any:
        """
        带重试的任务执行
        
        Args:
            func: 要执行的函数
            max_retries: 最大重试次数
            *args: 位置参数
            **kwargs: 关键字参数
        
        Returns:
            函数执行结果
        """
        last_exception = None

        for retry in range(max_retries + 1):
            try:
                if retry > 0:
                    logger.info(f"重试第 {retry} 次...")
                    time.sleep(retry * 2)  # 指数退避

                return func(*args, **kwargs)

            except Exception as e:
                last_exception = e
                logger.error(f"执行失败 (尝试 {retry + 1}/{max_retries + 1}): {str(e)}")

                if retry >= max_retries:
                    break

        # 所有重试都失败
        raise last_exception

    @staticmethod
    def execute_with_timeout(func: Callable, timeout: int, *args, **kwargs) -> Any:
        """
        带超时的任务执行
        
        Args:
            func: 要执行的函数
            timeout: 超时时间（秒）
            *args: 位置参数
            **kwargs: 关键字参数
        
        Returns:
            函数执行结果
        """
        import signal

        def timeout_handler(signum, frame):
            raise TimeoutError(f"任务执行超时（{timeout}秒）")

        # 设置超时信号
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(timeout)

        try:
            result = func(*args, **kwargs)
            signal.alarm(0)  # 取消超时
            return result
        except TimeoutError:
            logger.error(f"任务执行超时: {timeout}秒")
            raise
        finally:
            signal.alarm(0)  # 确保取消超时


# 示例任务函数
@scheduler_task
def example_task(message: str, **kwargs):
    """
    示例任务
    
    Args:
        message: 消息内容
        **kwargs: 其他参数（必须包含 job_code）
    """
    logger.info(f"执行示例任务: {message}")
    return f"任务完成: {message}"


@scheduler_task
def cleanup_old_logs(**kwargs):
    """
    清理旧日志任务
    
    清理 30 天前的执行日志
    """
    try:
        from datetime import timedelta
        from scheduler.models import SchedulerLog

        cutoff_date = datetime.now() - timedelta(days=30)
        deleted_count = SchedulerLog.objects.filter(
            start_time__lt=cutoff_date
        ).delete()[0]

        logger.info(f"清理了 {deleted_count} 条旧日志")
        return f"清理了 {deleted_count} 条旧日志"

    except Exception as e:
        logger.error(f"清理旧日志失败: {str(e)}")
        raise


@scheduler_task
def update_job_statistics(**kwargs):
    """
    更新任务统计信息
    
    定期更新所有任务的统计数据
    """
    try:
        from scheduler.models import SchedulerJob, SchedulerLog
        from django.db.models import Count, Q

        jobs = SchedulerJob.objects.all()

        for job in jobs:
            # 统计执行次数
            stats = SchedulerLog.objects.filter(job=job).aggregate(
                total=Count('id'),
                success=Count('id', filter=Q(status='success')),
                failed=Count('id', filter=Q(status='failed')),
            )

            job.total_run_count = stats['total']
            job.success_count = stats['success']
            job.failure_count = stats['failed']
            job.save(update_fields=['total_run_count', 'success_count', 'failure_count'])

        logger.info(f"更新了 {len(jobs)} 个任务的统计信息")
        return f"更新了 {len(jobs)} 个任务的统计信息"

    except Exception as e:
        logger.error(f"更新任务统计信息失败: {str(e)}")
        raise


@scheduler_task
def database_backup():
    """
    数据库备份任务示例
    
    这是一个示例，实际使用时需要根据具体数据库类型实现
    """
    try:
        logger.info("开始数据库备份...")

        # TODO: 实现实际的数据库备份逻辑
        # 这里只是一个示例

        logger.info("数据库备份完成")
        return "数据库备份完成"

    except Exception as e:
        logger.error(f"数据库备份失败: {str(e)}")
        raise


@scheduler_task
def send_daily_report(**kwargs):
    """
    发送每日报表任务示例
    """
    try:
        logger.info("开始生成每日报表...")

        # TODO: 实现实际的报表生成和发送逻辑

        logger.info("每日报表发送完成")
        return "每日报表发送完成"

    except Exception as e:
        logger.error(f"发送每日报表失败: {str(e)}")
        raise
