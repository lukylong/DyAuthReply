#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: api.py
@Desc: Scheduler API - 定时任务管理接口 - 提供定时任务的 CRUD 操作和管理功能
"""
"""
Scheduler API - 定时任务管理接口
提供定时任务的 CRUD 操作和管理功能
"""
from datetime import datetime, timedelta
from typing import List

from django.db.models import Q, Count
from django.shortcuts import get_object_or_404
from ninja import Router, Query
from ninja.errors import HttpError
from ninja.pagination import paginate

from common.fu_crud import create, retrieve, delete
from common.fu_pagination import MyPagination
from common.fu_schema import response_success
from scheduler.models import SchedulerJob, SchedulerLog
from scheduler.schema import (
    SchedulerJobSchemaIn,
    SchedulerJobSchemaPatch,
    SchedulerJobSchemaOut,
    SchedulerJobSchemaDetail,
    SchedulerJobFilters,
    SchedulerJobSimpleOut,
    SchedulerJobBatchDeleteIn,
    SchedulerJobBatchDeleteOut,
    SchedulerJobBatchUpdateStatusIn,
    SchedulerJobBatchUpdateStatusOut,
    SchedulerJobExecuteIn,
    SchedulerJobExecuteOut,
    SchedulerJobStatisticsOut,
    SchedulerLogSchemaOut,
    SchedulerLogFilters,
    SchedulerLogBatchDeleteIn,
    SchedulerLogBatchDeleteOut,
    SchedulerLogCleanIn,
    SchedulerLogCleanOut,
)
from scheduler.service import scheduler_service

router = Router()


# ==================== SchedulerJob APIs ====================

@router.post("/job", response=SchedulerJobSchemaOut, summary="创建定时任务")
def create_scheduler_job(request, data: SchedulerJobSchemaIn):
    """
    创建新的定时任务
    
    改进点：
    - 检查任务编码唯一性
    - 自动添加到调度器（如果启用）
    """
    # 检查任务编码是否已存在
    if SchedulerJob.objects.filter(code=data.code).exists():
        raise HttpError(400, f"任务编码已存在: {data.code}")

    # 创建任务
    job = create(request, data.dict(), SchedulerJob)

    # 如果任务是启用状态，添加到调度器
    if job.is_enabled() and scheduler_service.is_running():
        scheduler_service.add_job(job)

    return job


@router.delete("/job/{job_id}", response=SchedulerJobSchemaOut, summary="删除定时任务")
def delete_scheduler_job(request, job_id: str):
    """
    删除定时任务
    
    改进点：
    - 同时从调度器移除
    """
    job = get_object_or_404(SchedulerJob, id=job_id)

    # 从调度器移除
    if scheduler_service.is_running():
        scheduler_service.remove_job(job.code)

    instance = delete(job_id, SchedulerJob)
    return instance


@router.delete("/job/batch/delete", response=SchedulerJobBatchDeleteOut, summary="批量删除定时任务")
def delete_batch_scheduler_job(request, data: SchedulerJobBatchDeleteIn):
    """
    批量删除定时任务
    
    改进点：
    - 同时从调度器批量移除
    """
    success_count = 0
    failed_ids = []

    for job_id in data.ids:
        try:
            job = SchedulerJob.objects.get(id=job_id)

            # 从调度器移除
            if scheduler_service.is_running():
                scheduler_service.remove_job(job.code)

            job.delete()
            success_count += 1
        except SchedulerJob.DoesNotExist:
            failed_ids.append(job_id)
        except Exception as e:
            failed_ids.append(job_id)

    return SchedulerJobBatchDeleteOut(count=success_count, failed_ids=failed_ids)


@router.put("/job/{job_id}", response=SchedulerJobSchemaOut, summary="更新定时任务（完全替换）")
def update_scheduler_job(request, job_id: str, data: SchedulerJobSchemaIn):
    """
    更新定时任务（PUT - 完全替换）
    
    改进点：
    - 检查任务编码唯一性（排除自身）
    - 同步更新调度器中的任务
    """
    job = get_object_or_404(SchedulerJob, id=job_id)

    # 检查任务编码是否已存在（排除自身）
    if SchedulerJob.objects.filter(code=data.code).exclude(id=job_id).exists():
        raise HttpError(400, f"任务编码已存在: {data.code}")

    # 更新任务信息
    for attr, value in data.dict().items():
        if value is not None:
            setattr(job, attr, value)

    job.save()

    # 同步更新调度器
    if scheduler_service.is_running():
        scheduler_service.modify_job(job)

    return job


@router.patch("/job/{job_id}", response=SchedulerJobSchemaOut, summary="部分更新定时任务")
def patch_scheduler_job(request, job_id: str, data: SchedulerJobSchemaPatch):
    """
    部分更新定时任务（PATCH - 只更新提供的字段）
    
    改进点：
    - 只更新提供的字段
    - 同步更新调度器
    """
    job = get_object_or_404(SchedulerJob, id=job_id)

    # 只更新提供的字段
    update_data = data.dict(exclude_unset=True)

    # 检查任务编码是否已存在（排除自身）
    if 'code' in update_data and update_data['code']:
        if SchedulerJob.objects.filter(code=update_data['code']).exclude(id=job_id).exists():
            raise HttpError(400, f"任务编码已存在: {update_data['code']}")

    # 更新字段
    for attr, value in update_data.items():
        setattr(job, attr, value)

    job.save()

    # 同步更新调度器
    if scheduler_service.is_running():
        scheduler_service.modify_job(job)

    return job


@router.get("/job", response=List[SchedulerJobSchemaOut], summary="获取定时任务列表（分页）")
@paginate(MyPagination)
def list_scheduler_job(request, filters: SchedulerJobFilters = Query(...)):
    """
    获取定时任务列表（分页）
    
    改进点：
    - 支持多种过滤条件
    """
    query_set = retrieve(request, SchedulerJob, filters)
    return query_set


@router.get("/job/all", response=List[SchedulerJobSimpleOut], summary="获取所有定时任务（简化版）")
def list_all_scheduler_job(request):
    """
    获取所有定时任务（不分页，简化版）
    
    用于任务选择器等场景
    """
    query_set = SchedulerJob.objects.all().order_by('-priority', 'name')
    return query_set


@router.get("/job/{job_id}", response=SchedulerJobSchemaDetail, summary="获取定时任务详情")
def get_scheduler_job(request, job_id: str):
    """
    获取单个定时任务的详细信息
    
    改进点：
    - 包含统计信息
    """
    job = get_object_or_404(SchedulerJob, id=job_id)
    return job


@router.post("/job/batch/update-status", response=SchedulerJobBatchUpdateStatusOut, summary="批量更新任务状态")
def batch_update_scheduler_job_status(request, data: SchedulerJobBatchUpdateStatusIn):
    """
    批量启用、禁用或暂停任务
    
    改进点：
    - 同步更新调度器
    """
    jobs = SchedulerJob.objects.filter(id__in=data.ids)
    count = jobs.update(status=data.status)

    # 同步更新调度器
    if scheduler_service.is_running():
        for job in jobs:
            job.refresh_from_db()
            if job.is_enabled():
                scheduler_service.add_job(job)
            elif job.is_paused():
                scheduler_service.pause_job(job.code)
            else:
                scheduler_service.remove_job(job.code)

    return SchedulerJobBatchUpdateStatusOut(count=count)


@router.post("/job/execute", response=SchedulerJobExecuteOut, summary="立即执行任务")
def execute_scheduler_job(request, data: SchedulerJobExecuteIn):
    """
    立即执行指定任务（不影响正常调度）
    
    改进点：
    - 创建执行日志
    """
    job = get_object_or_404(SchedulerJob, id=data.job_id)

    if not scheduler_service.is_running():
        raise HttpError(400, "调度器未运行")

    # 立即执行任务
    success = scheduler_service.run_job_now(job.code)

    if success:
        return SchedulerJobExecuteOut(
            success=True,
            message=f"任务 {job.name} 将立即执行"
        )
    else:
        return SchedulerJobExecuteOut(
            success=False,
            message=f"任务 {job.name} 执行失败"
        )


@router.get("/job/search", response=List[SchedulerJobSchemaOut], summary="搜索定时任务")
@paginate(MyPagination)
def search_scheduler_job(request, keyword: str = Query(None)):
    """
    搜索定时任务
    
    改进点：
    - 支持关键词搜索（名称、编码、描述）
    """
    query_set = SchedulerJob.objects.all()

    if keyword:
        query_set = query_set.filter(
            Q(name__icontains=keyword) |
            Q(code__icontains=keyword) |
            Q(description__icontains=keyword)
        )

    return query_set


@router.get("/job/statistics/data", response=SchedulerJobStatisticsOut, summary="获取任务统计信息")
def get_scheduler_job_statistics(request):
    """
    获取任务统计信息
    
    改进点：
    - 提供全局统计数据
    """
    # 任务统计
    job_stats = SchedulerJob.objects.aggregate(
        total=Count('id'),
        enabled=Count('id', filter=Q(status=1)),
        disabled=Count('id', filter=Q(status=0)),
        paused=Count('id', filter=Q(status=2)),
    )

    # 执行统计
    exec_stats = SchedulerLog.objects.aggregate(
        total=Count('id'),
        success=Count('id', filter=Q(status='success')),
        failed=Count('id', filter=Q(status='failed')),
    )

    # 计算成功率
    total_exec = exec_stats['total'] or 0
    success_exec = exec_stats['success'] or 0
    success_rate = round(success_exec / total_exec * 100, 2) if total_exec > 0 else 0

    return SchedulerJobStatisticsOut(
        total_jobs=job_stats['total'],
        enabled_jobs=job_stats['enabled'],
        disabled_jobs=job_stats['disabled'],
        paused_jobs=job_stats['paused'],
        total_executions=total_exec,
        success_executions=success_exec,
        failed_executions=exec_stats['failed'],
        success_rate=success_rate,
    )


# ==================== SchedulerLog APIs ====================

@router.get("/log", response=List[SchedulerLogSchemaOut], summary="获取任务执行日志列表（分页）")
@paginate(MyPagination)
def list_scheduler_log(request, filters: SchedulerLogFilters = Query(...)):
    """
    获取任务执行日志列表（分页）
    
    改进点：
    - 支持多种过滤条件
    - 支持时间范围查询
    """
    query_set = retrieve(request, SchedulerLog, filters)
    return query_set


@router.get("/log/{log_id}", response=SchedulerLogSchemaOut, summary="获取任务执行日志详情")
def get_scheduler_log(request, log_id: str):
    """获取单个任务执行日志的详细信息"""
    log = get_object_or_404(SchedulerLog, id=log_id)
    return log


@router.delete("/log/{log_id}", response=SchedulerLogSchemaOut, summary="删除任务执行日志")
def delete_scheduler_log(request, log_id: str):
    """删除任务执行日志"""
    instance = delete(log_id, SchedulerLog)
    return instance


@router.delete("/log/batch/delete", response=SchedulerLogBatchDeleteOut, summary="批量删除任务执行日志")
def delete_batch_scheduler_log(request, data: SchedulerLogBatchDeleteIn):
    """批量删除任务执行日志"""
    count = SchedulerLog.objects.filter(id__in=data.ids).delete()[0]
    return SchedulerLogBatchDeleteOut(count=count)


@router.post("/log/clean", response=SchedulerLogCleanOut, summary="清理旧日志")
def clean_scheduler_log(request, data: SchedulerLogCleanIn):
    """
    清理旧日志
    
    改进点：
    - 支持按天数清理
    - 支持按状态清理
    """
    cutoff_date = datetime.now() - timedelta(days=data.days)

    query_set = SchedulerLog.objects.filter(start_time__lt=cutoff_date)

    if data.status:
        query_set = query_set.filter(status=data.status)

    count = query_set.delete()[0]

    return SchedulerLogCleanOut(count=count)


@router.get("/log/by/job/{job_id}", response=List[SchedulerLogSchemaOut], summary="获取指定任务的执行日志")
@paginate(MyPagination)
def list_scheduler_log_by_job(request, job_id: str):
    """获取指定任务的所有执行日志"""
    query_set = SchedulerLog.objects.filter(job_id=job_id).order_by('-start_time')
    return query_set


# ==================== Scheduler Control APIs ====================

@router.post("/start", summary="启动调度器")
def start_scheduler(request):
    """启动调度器"""
    if scheduler_service.is_running():
        raise HttpError(400, "调度器已在运行中")

    scheduler_service.start()
    return response_success("调度器已启动")


@router.post("/shutdown", summary="关闭调度器")
def shutdown_scheduler(request):
    """关闭调度器"""
    if not scheduler_service.is_running():
        raise HttpError(400, "调度器未运行")

    scheduler_service.shutdown()
    return response_success("调度器已关闭")


@router.post("/pause", summary="暂停调度器")
def pause_scheduler(request):
    """暂停调度器"""
    if not scheduler_service.is_running():
        raise HttpError(400, "调度器未运行")

    scheduler_service.pause()
    return response_success("调度器已暂停")


@router.post("/resume", summary="恢复调度器")
def resume_scheduler(request):
    """恢复调度器"""
    scheduler_service.resume()
    return response_success("调度器已恢复")


@router.get("/status", summary="获取调度器状态")
def get_scheduler_status(request):
    """获取调度器状态"""
    is_running = scheduler_service.is_running()
    jobs = scheduler_service.get_all_jobs() if is_running else []

    return {
        "is_running": is_running,
        "job_count": len(jobs),
        "jobs": jobs,
    }
