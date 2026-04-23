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
