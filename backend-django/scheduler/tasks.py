#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: tasks.py
@Desc: Scheduler Tasks - 定时任务定义 - 在此文件中定义所有的定时任务函数
"""
"""
Scheduler Tasks - 定时任务定义
在此文件中定义所有的定时任务函数
"""
import logging
import os
import subprocess
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)


def load_initial_data():
    """
    加载初始数据任务
    执行 manage.py loaddata db_init.json
    
    使用方法：
    在调度器中配置此任务，任务代码为：scheduler.tasks.load_initial_data
    """
    try:
        # 获取项目根目录（backend-django目录）
        base_dir = settings.BASE_DIR
        
        # manage.py 路径
        manage_py = base_dir / 'manage.py'
        
        # db_init.json 文件路径（假设在项目根目录）
        fixture_file = base_dir / 'db_init.json'
        
        # 检查文件是否存在
        if not manage_py.exists():
            error_msg = f"manage.py 文件不存在: {manage_py}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
        
        if not fixture_file.exists():
            error_msg = f"db_init.json 文件不存在: {fixture_file}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
        
        logger.info(f"开始执行 loaddata 命令，加载文件: {fixture_file}")
        
        # 构建命令
        # 使用当前 Python 解释器执行 manage.py
        cmd = [
            'python',
            str(manage_py),
            'loaddata',
            str(fixture_file)
        ]
        
        # 执行命令
        result = subprocess.run(
            cmd,
            cwd=str(base_dir),
            capture_output=True,
            text=True,
            timeout=300  # 5分钟超时
        )
        
        # 记录输出
        if result.stdout:
            logger.info(f"命令输出: {result.stdout}")
        
        if result.stderr:
            logger.warning(f"命令错误输出: {result.stderr}")
        
        # 检查返回码
        if result.returncode == 0:
            success_msg = f"成功加载初始数据: {fixture_file}"
            logger.info(success_msg)
            return {
                "success": True,
                "message": success_msg,
                "stdout": result.stdout,
                "stderr": result.stderr
            }
        else:
            error_msg = f"加载初始数据失败，返回码: {result.returncode}"
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr
            }
            
    except subprocess.TimeoutExpired:
        error_msg = "执行 loaddata 命令超时（超过5分钟）"
        logger.error(error_msg)
        return {"success": False, "error": error_msg}
    
    except Exception as e:
        error_msg = f"执行 loaddata 命令时发生异常: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {"success": False, "error": error_msg, "exception": str(e)}


def example_task():
    """
    示例任务
    演示如何编写一个简单的定时任务
    
    使用方法：
    在调度器中配置此任务，任务代码为：scheduler.tasks.example_task
    """
    logger.info("示例任务开始执行")
    
    try:
        # 执行任务逻辑
        result = "任务执行成功"
        logger.info(result)
        return {"success": True, "message": result}
    
    except Exception as e:
        error_msg = f"任务执行失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {"success": False, "error": error_msg}


def cleanup_old_logs():
    """
    清理旧日志任务
    删除超过30天的调度器执行日志
    
    使用方法：
    在调度器中配置此任务，任务代码为：scheduler.tasks.cleanup_old_logs
    建议配置为每天凌晨执行一次
    """
    from datetime import timedelta
    from django.utils import timezone
    from scheduler.models import SchedulerLog
    
    try:
        # 计算30天前的时间
        cutoff_date = timezone.now() - timedelta(days=30)
        
        # 删除旧日志
        deleted_count, _ = SchedulerLog.objects.filter(
            start_time__lt=cutoff_date
        ).delete()
        
        success_msg = f"成功清理 {deleted_count} 条旧日志记录"
        logger.info(success_msg)
        
        return {
            "success": True,
            "message": success_msg,
            "deleted_count": deleted_count
        }
    
    except Exception as e:
        error_msg = f"清理旧日志失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {"success": False, "error": error_msg}


# =============================================================================
# 抖音托管相关定时任务
# =============================================================================

def douyin_reset_daily_quota():
    """
    每日 0 点重置所有抖音账号的 reply_today = 0。
    建议 cron: 0 0 * * *

    任务代码：scheduler.tasks.douyin_reset_daily_quota
    """
    try:
        from core.douyin.douyin_account_model import DouyinAccount
        from core.douyin.douyin_session_model import DouyinSession

        acc_count = DouyinAccount.objects.update(reply_today=0)
        DouyinSession.objects.update(messages_today=0, replies_today=0, errors_today=0)
        msg = f"已重置 {acc_count} 个抖音账号的日配额与会话日计数"
        logger.info(msg)
        return {"success": True, "message": msg, "count": acc_count}
    except Exception as e:  # noqa: BLE001
        error_msg = f"重置抖音日配额失败: {e}"
        logger.error(error_msg, exc_info=True)
        return {"success": False, "error": error_msg}


def douyin_aggregate_daily_stats():
    """
    每小时聚合"今日"数据到 DouyinDailyStat（便于前端 Dashboard 快速读取）。
    建议 cron: 5 * * * *

    任务代码：scheduler.tasks.douyin_aggregate_daily_stats
    """
    try:
        from datetime import timedelta
        from django.db.models import Count, Q
        from django.utils import timezone

        from core.douyin.douyin_account_model import DouyinAccount
        from core.douyin.douyin_conversation_model import DouyinConversation
        from core.douyin.douyin_daily_stat_model import DouyinDailyStat
        from core.douyin.douyin_event_model import DouyinEvent
        from core.douyin.douyin_message_model import DouyinMessage
        from core.douyin.douyin_reply_log_model import DouyinReplyLog

        # settings.USE_TZ=False 时 localdate()/make_aware 都会出错，直接用 now() 拆。
        now = timezone.now()
        today = now.date()
        start = timezone.datetime.combine(today, timezone.datetime.min.time())
        if timezone.is_aware(now):
            start = timezone.make_aware(start)
        end = start + timedelta(days=1)

        updated = 0
        for acc in DouyinAccount.objects.all():
            # 收到消息数
            msg_recv = DouyinMessage.objects.filter(
                conversation__account=acc, direction='in',
                sys_create_datetime__gte=start, sys_create_datetime__lt=end,
            ).count()

            logs = DouyinReplyLog.objects.filter(
                account=acc, sys_create_datetime__gte=start, sys_create_datetime__lt=end,
            )
            sent = logs.filter(result='success').count()
            failed = logs.filter(result='failed').count()
            skipped = logs.filter(result__in=['skipped', 'cooldown', 'quota_exceeded', 'silent']).count()

            unique_peers = DouyinConversation.objects.filter(
                account=acc, last_message_at__gte=start, last_message_at__lt=end,
            ).count()

            evt = DouyinEvent.objects.filter(
                account=acc, occurred_at__gte=start, occurred_at__lt=end,
            )
            warns = evt.filter(level='warning').count()
            errs = evt.filter(level__in=['error', 'critical']).count()

            avg_ms = 0
            agg = logs.filter(result='success').aggregate(
                c=Count('id'),
            )
            if agg['c'] > 0:
                total_ms = sum(logs.filter(result='success').values_list('duration_ms', flat=True))
                avg_ms = int(total_ms / agg['c'])

            DouyinDailyStat.objects.update_or_create(
                account=acc, stat_date=today,
                defaults={
                    'messages_received': msg_recv,
                    'replies_sent': sent,
                    'replies_failed': failed,
                    'replies_skipped': skipped,
                    'unique_peers': unique_peers,
                    'avg_response_ms': avg_ms,
                    'events_warning': warns,
                    'events_error': errs,
                },
            )
            updated += 1

        logger.info(f"已聚合 {updated} 个账号的今日统计")
        return {"success": True, "count": updated}
    except Exception as e:  # noqa: BLE001
        error_msg = f"聚合抖音日统计失败: {e}"
        logger.error(error_msg, exc_info=True)
        return {"success": False, "error": error_msg}
