#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: service.py
@Desc: Scheduler Service - APScheduler 调度服务 - 基于 APScheduler 实现的定时任务调度核心服务
"""
"""
Scheduler Service - APScheduler 调度服务
基于 APScheduler 实现的定时任务调度核心服务
"""
import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any

from apscheduler.events import (
    EVENT_JOB_EXECUTED,
    EVENT_JOB_ERROR,
    EVENT_JOB_MISSED,
    JobExecutionEvent,
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from django.conf import settings

logger = logging.getLogger(__name__)


class SchedulerService:
    """
    定时任务调度服务
    
    功能特点：
    1. 单例模式，全局唯一调度器实例
    2. 支持多种触发器类型（cron、interval、date）
    3. 自动从数据库加载任务
    4. 监听任务执行事件
    5. 自动更新任务状态
    """

    _instance = None
    _scheduler: Optional[BackgroundScheduler] = None
    _initialized = False

    def __new__(cls):
        """单例模式"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """初始化调度器"""
        if not self._initialized:
            self._init_scheduler()
            self._initialized = True

    def _init_scheduler(self):
        """初始化 APScheduler"""
        try:
            # 创建后台调度器
            self._scheduler = BackgroundScheduler(
                timezone=settings.TIME_ZONE,
                job_defaults={
                    'coalesce': True,  # 合并执行
                    'max_instances': 1,  # 最大实例数
                    'misfire_grace_time': 60,  # 错过执行的宽限时间（秒）
                }
            )

            # 添加事件监听器
            self._scheduler.add_listener(
                self._job_executed_listener,
                EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED
            )

            logger.info("APScheduler 初始化成功")
        except Exception as e:
            logger.error(f"APScheduler 初始化失败: {str(e)}")
            raise

    def start(self):
        """启动调度器"""
        if self._scheduler and not self._scheduler.running:
            try:
                self._scheduler.start()
                logger.info("APScheduler 已启动")
                # 加载数据库中的任务
                self.load_jobs_from_db()
            except Exception as e:
                logger.error(f"APScheduler 启动失败: {str(e)}")
                raise

    def shutdown(self, wait=True):
        """关闭调度器"""
        if self._scheduler and self._scheduler.running:
            try:
                self._scheduler.shutdown(wait=wait)
                logger.info("APScheduler 已关闭")
            except Exception as e:
                logger.error(f"APScheduler 关闭失败: {str(e)}")
                raise

    def pause(self):
        """暂停调度器"""
        if self._scheduler and self._scheduler.running:
            try:
                self._scheduler.pause()
                logger.info("APScheduler 已暂停")
            except Exception as e:
                logger.error(f"APScheduler 暂停失败: {str(e)}")
                raise

    def resume(self):
        """恢复调度器"""
        if self._scheduler:
            try:
                self._scheduler.resume()
                logger.info("APScheduler 已恢复")
            except Exception as e:
                logger.error(f"APScheduler 恢复失败: {str(e)}")
                raise

    def is_running(self) -> bool:
        """判断调度器是否运行中"""
        return self._scheduler and self._scheduler.running

    def load_jobs_from_db(self):
        """从数据库加载所有启用的任务"""
        try:
            from scheduler.models import SchedulerJob

            # 获取所有启用的任务
            jobs = SchedulerJob.objects.filter(status=1)

            for job in jobs:
                try:
                    self.add_job(job)
                    logger.info(f"加载任务成功: {job.code}")
                except Exception as e:
                    logger.error(f"加载任务失败 {job.code}: {str(e)}")

            logger.info(f"从数据库加载了 {len(jobs)} 个任务")

            # 启动定期清理任务
            self._start_cleanup_job()
        except Exception as e:
            logger.error(f"从数据库加载任务失败: {str(e)}")

    def _start_cleanup_job(self):
        """启动定期清理过期任务的定时任务"""
        cleanup_job_id = '_scheduler_cleanup'

        if self._scheduler.get_job(cleanup_job_id):
            return

        # 每天凌晨3点清理过期任务
        self._scheduler.add_job(
            func=lambda: self.cleanup_expired_jobs(days=7),
            trigger=CronTrigger(hour=3, minute=0),
            id=cleanup_job_id,
            name='清理过期任务',
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        logger.info("定期清理任务已启动（每天凌晨3点）")

    def enable_job_sync(self, interval_seconds: int = 60):
        """
        启用定期同步任务（可选功能）
        
        仅在调度器独立运行时需要（如 start_scheduler.py）
        如果调度器随 Django 应用启动，不需要此功能，
        因为 add_job() 会直接添加到运行中的调度器。
        
        Args:
            interval_seconds: 同步间隔（秒），默认60秒
        """
        sync_job_id = '_scheduler_sync_jobs'

        # 检查是否已存在
        if self._scheduler.get_job(sync_job_id):
            return

        # 记录启动时间，用于增量查询
        self._last_sync_time = datetime.now()

        self._scheduler.add_job(
            func=self._sync_new_jobs,
            trigger=IntervalTrigger(seconds=interval_seconds),
            id=sync_job_id,
            name='同步新任务',
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        logger.info(f"定期同步任务已启动（每{interval_seconds}秒）")

    def _sync_new_jobs(self):
        """同步数据库中新增的任务到调度器（增量查询）"""
        try:
            from scheduler.models import SchedulerJob

            # 获取调度器中已有的任务ID
            existing_job_ids = {job.id for job in self._scheduler.get_jobs()}

            # 只查询上次同步后新增的启用任务
            last_sync = getattr(self, '_last_sync_time', None)
            query = SchedulerJob.objects.filter(status=1)

            if last_sync:
                query = query.filter(sys_create_datetime__gte=last_sync)

            self._last_sync_time = datetime.now()

            new_count = 0
            for job in query:
                if job.code not in existing_job_ids:
                    try:
                        self.add_job(job)
                        new_count += 1
                    except Exception as e:
                        logger.error(f"同步任务失败 {job.code}: {str(e)}")

            if new_count > 0:
                logger.info(f"同步了 {new_count} 个新任务")

        except Exception as e:
            logger.error(f"同步任务失败: {str(e)}")

    def add_job(self, job_obj):
        """添加任务到调度器"""
        try:
            # 构建触发器
            trigger = self._build_trigger(job_obj)
            if not trigger:
                logger.error(f"无法为任务 {job_obj.code} 构建触发器")
                return False

            # 解析任务参数
            args = json.loads(job_obj.task_args) if job_obj.task_args else []
            kwargs = json.loads(job_obj.task_kwargs) if job_obj.task_kwargs else {}

            # ✅ 关键修复：添加 job_code 到 kwargs 中
            # 这样任务执行器可以通过 kwargs.get('job_code') 获取任务编码
            kwargs['job_code'] = job_obj.code

            # 导入任务函数
            task_func = self._import_task_func(job_obj.task_func)
            if not task_func:
                logger.error(f"无法导入任务函数: {job_obj.task_func}")
                return False

            # 添加任务
            self._scheduler.add_job(
                func=task_func,
                trigger=trigger,
                args=args,
                kwargs=kwargs,
                id=job_obj.code,
                name=job_obj.name,
                max_instances=job_obj.max_instances,
                coalesce=job_obj.coalesce,
                replace_existing=True,
            )

            # 更新下次执行时间
            self._update_next_run_time(job_obj)

            logger.info(f"任务 {job_obj.code} 已添加到调度器")
            return True
        except Exception as e:
            logger.error(f"添加任务失败 {job_obj.code}: {str(e)}")
            return False

    def remove_job(self, job_code: str):
        """从调度器移除任务"""
        try:
            if self._scheduler.get_job(job_code):
                self._scheduler.remove_job(job_code)
                logger.info(f"任务 {job_code} 已从调度器移除")
                return True
            return False
        except Exception as e:
            logger.error(f"移除任务失败 {job_code}: {str(e)}")
            return False

    def pause_job(self, job_code: str):
        """暂停任务"""
        try:
            if self._scheduler.get_job(job_code):
                self._scheduler.pause_job(job_code)
                logger.info(f"任务 {job_code} 已暂停")
                return True
            return False
        except Exception as e:
            logger.error(f"暂停任务失败 {job_code}: {str(e)}")
            return False

    def resume_job(self, job_code: str):
        """恢复任务"""
        try:
            if self._scheduler.get_job(job_code):
                self._scheduler.resume_job(job_code)
                logger.info(f"任务 {job_code} 已恢复")
                return True
            return False
        except Exception as e:
            logger.error(f"恢复任务失败 {job_code}: {str(e)}")
            return False

    def modify_job(self, job_obj):
        """修改任务"""
        try:
            # 先移除旧任务
            self.remove_job(job_obj.code)

            # 如果任务是启用状态，重新添加
            if job_obj.is_enabled():
                return self.add_job(job_obj)

            return True
        except Exception as e:
            logger.error(f"修改任务失败 {job_obj.code}: {str(e)}")
            return False

    def run_job_now(self, job_code: str):
        """立即执行任务（不影响正常调度）"""
        try:
            job = self._scheduler.get_job(job_code)
            if job:
                job.modify(next_run_time=datetime.now())
                logger.info(f"任务 {job_code} 将立即执行")
                return True
            return False
        except Exception as e:
            logger.error(f"立即执行任务失败 {job_code}: {str(e)}")
            return False

    def get_job_info(self, job_code: str) -> Optional[Dict[str, Any]]:
        """获取任务信息"""
        try:
            job = self._scheduler.get_job(job_code)
            if job:
                return {
                    'id': job.id,
                    'name': job.name,
                    'next_run_time': job.next_run_time,
                    'trigger': str(job.trigger),
                }
            return None
        except Exception as e:
            logger.error(f"获取任务信息失败 {job_code}: {str(e)}")
            return None

    def get_all_jobs(self) -> list:
        """获取所有任务"""
        try:
            jobs = self._scheduler.get_jobs()
            return [
                {
                    'id': job.id,
                    'name': job.name,
                    'next_run_time': job.next_run_time,
                    'trigger': str(job.trigger),
                }
                for job in jobs
            ]
        except Exception as e:
            logger.error(f"获取所有任务失败: {str(e)}")
            return []

    def _build_trigger(self, job_obj):
        """构建触发器"""
        try:
            if job_obj.trigger_type == 'cron':
                # Cron 触发器
                # 解析 cron 表达式：minute hour day month day_of_week
                parts = job_obj.cron_expression.split()
                if len(parts) != 5:
                    logger.error(f"Cron 表达式格式错误: {job_obj.cron_expression}")
                    return None

                return CronTrigger(
                    minute=parts[0],
                    hour=parts[1],
                    day=parts[2],
                    month=parts[3],
                    day_of_week=parts[4],
                    timezone=settings.TIME_ZONE,
                )

            elif job_obj.trigger_type == 'interval':
                # 间隔触发器
                return IntervalTrigger(
                    seconds=job_obj.interval_seconds,
                    timezone=settings.TIME_ZONE,
                )

            elif job_obj.trigger_type == 'date':
                # 指定时间触发器
                return DateTrigger(
                    run_date=job_obj.run_date,
                    timezone=settings.TIME_ZONE,
                )

            else:
                logger.error(f"不支持的触发器类型: {job_obj.trigger_type}")
                return None

        except Exception as e:
            logger.error(f"构建触发器失败: {str(e)}")
            return None

    def _import_task_func(self, task_path: str):
        """动态导入任务函数"""
        try:
            module_path, func_name = task_path.rsplit('.', 1)
            module = __import__(module_path, fromlist=[func_name])
            return getattr(module, func_name)
        except Exception as e:
            logger.error(f"导入任务函数失败 {task_path}: {str(e)}")
            return None

    def _update_next_run_time(self, job_obj):
        """更新任务的下次执行时间"""
        try:
            job = self._scheduler.get_job(job_obj.code)
            if job and job.next_run_time:
                from scheduler.models import SchedulerJob
                SchedulerJob.objects.filter(id=job_obj.id).update(
                    next_run_time=job.next_run_time
                )
        except Exception as e:
            logger.error(f"更新下次执行时间失败: {str(e)}")

    def _job_executed_listener(self, event: JobExecutionEvent):
        """任务执行事件监听器"""
        try:
            from scheduler.models import SchedulerJob

            job_code = event.job_id

            # 跳过内部同步任务
            if job_code.startswith('_'):
                return

            # 更新任务统计信息
            job_obj = SchedulerJob.objects.filter(code=job_code).first()
            if job_obj:
                if event.exception:
                    # 执行失败
                    job_obj.last_run_status = 'failed'
                    job_obj.last_run_result = str(event.exception)
                    job_obj.increment_run_count(success=False)
                else:
                    # 执行成功
                    job_obj.last_run_status = 'success'
                    job_obj.last_run_result = str(event.retval) if event.retval else None
                    job_obj.increment_run_count(success=True)

                job_obj.last_run_time = datetime.now()
                job_obj.save(update_fields=[
                    'last_run_status',
                    'last_run_result',
                    'last_run_time',
                ])

                # 一次性任务（date 类型）执行后自动清理
                if job_obj.trigger_type == 'date':
                    self._cleanup_one_time_job(job_obj)
                else:
                    # 更新下次执行时间
                    self._update_next_run_time(job_obj)

        except Exception as e:
            logger.error(f"处理任务执行事件失败: {str(e)}")

    def _cleanup_one_time_job(self, job_obj):
        """清理一次性任务"""
        try:
            job_code = job_obj.code

            # 从调度器移除（APScheduler 会自动移除已执行的 date 任务，但保险起见）
            if self._scheduler.get_job(job_code):
                self._scheduler.remove_job(job_code)

            # 删除数据库记录
            job_obj.delete()

            logger.debug(f"一次性任务已清理: {job_code}")
        except Exception as e:
            logger.error(f"清理一次性任务失败 {job_obj.code}: {str(e)}")

    def cleanup_expired_jobs(self, days: int = 7):
        """
        清理过期的一次性任务（用于清理可能遗漏的历史任务）
        
        Args:
            days: 清理多少天前的任务，默认7天
        """
        try:
            from datetime import timedelta
            from scheduler.models import SchedulerJob

            cutoff = datetime.now() - timedelta(days=days)

            # 查找过期的一次性任务（date 类型，且执行时间已过）
            expired_jobs = SchedulerJob.objects.filter(
                trigger_type='date',
                run_date__lt=cutoff,
            )

            count = expired_jobs.count()
            if count > 0:
                # 从调度器移除
                for job in expired_jobs:
                    if self._scheduler.get_job(job.code):
                        self._scheduler.remove_job(job.code)

                # 删除数据库记录
                expired_jobs.delete()
                logger.info(f"清理了 {count} 个过期的一次性任务")

            return count
        except Exception as e:
            logger.error(f"清理过期任务失败: {str(e)}")
            return 0


# 全局调度器实例
scheduler_service = SchedulerService()
