#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: schema.py
@Desc: Scheduler Schema - 定时任务数据验证和序列化
"""
"""
Scheduler Schema - 定时任务数据验证和序列化
"""
from datetime import datetime
from typing import Optional, List

from ninja import Schema, FilterSchema
from pydantic import BaseModel, Field, validator


# ==================== SchedulerJob Schemas ====================

class SchedulerJobSchemaIn(BaseModel):
    """定时任务创建输入"""
    name: str = Field(..., description="任务名称", max_length=128)
    code: str = Field(..., description="任务编码", max_length=128)
    description: Optional[str] = Field(None, description="任务描述")
    group: str = Field("default", description="任务分组", max_length=64)
    trigger_type: str = Field(..., description="触发器类型：cron/interval/date")
    cron_expression: Optional[str] = Field(None, description="Cron表达式")
    interval_seconds: Optional[int] = Field(None, description="间隔时间（秒）", ge=1)
    run_date: Optional[datetime] = Field(None, description="指定执行时间")
    task_func: str = Field(..., description="任务函数路径", max_length=256)
    task_args: Optional[str] = Field(None, description="任务位置参数（JSON）")
    task_kwargs: Optional[str] = Field(None, description="任务关键字参数（JSON）")
    status: int = Field(0, description="任务状态：0-禁用，1-启用，2-暂停")
    priority: int = Field(0, description="任务优先级")
    max_instances: int = Field(1, description="最大实例数", ge=1)
    max_retries: int = Field(0, description="错误重试次数", ge=0)
    timeout: Optional[int] = Field(None, description="超时时间（秒）", ge=1)
    coalesce: bool = Field(True, description="是否合并执行")
    allow_concurrent: bool = Field(False, description="是否允许并发执行")
    remark: Optional[str] = Field(None, description="备注信息")

    @validator('trigger_type')
    def validate_trigger_type(cls, v):
        """验证触发器类型"""
        if v not in ['cron', 'interval', 'date']:
            raise ValueError('触发器类型必须是 cron、interval 或 date')
        return v

    @validator('status')
    def validate_status(cls, v):
        """验证状态"""
        if v not in [0, 1, 2]:
            raise ValueError('状态必须是 0（禁用）、1（启用）或 2（暂停）')
        return v

    @validator('cron_expression')
    def validate_cron_expression(cls, v, values):
        """验证 Cron 表达式"""
        if values.get('trigger_type') == 'cron' and not v:
            raise ValueError('Cron 类型任务必须提供 cron_expression')
        return v

    @validator('interval_seconds')
    def validate_interval_seconds(cls, v, values):
        """验证间隔时间"""
        if values.get('trigger_type') == 'interval' and not v:
            raise ValueError('Interval 类型任务必须提供 interval_seconds')
        return v

    @validator('run_date')
    def validate_run_date(cls, v, values):
        """验证指定时间"""
        if values.get('trigger_type') == 'date' and not v:
            raise ValueError('Date 类型任务必须提供 run_date')
        return v


class SchedulerJobSchemaPatch(BaseModel):
    """定时任务部分更新输入"""
    name: Optional[str] = Field(None, description="任务名称", max_length=128)
    code: Optional[str] = Field(None, description="任务编码", max_length=128)
    description: Optional[str] = Field(None, description="任务描述")
    group: Optional[str] = Field(None, description="任务分组", max_length=64)
    trigger_type: Optional[str] = Field(None, description="触发器类型")
    cron_expression: Optional[str] = Field(None, description="Cron表达式")
    interval_seconds: Optional[int] = Field(None, description="间隔时间（秒）")
    run_date: Optional[datetime] = Field(None, description="指定执行时间")
    task_func: Optional[str] = Field(None, description="任务函数路径")
    task_args: Optional[str] = Field(None, description="任务位置参数（JSON）")
    task_kwargs: Optional[str] = Field(None, description="任务关键字参数（JSON）")
    status: Optional[int] = Field(None, description="任务状态")
    priority: Optional[int] = Field(None, description="任务优先级")
    max_instances: Optional[int] = Field(None, description="最大实例数")
    max_retries: Optional[int] = Field(None, description="错误重试次数")
    timeout: Optional[int] = Field(None, description="超时时间（秒）")
    coalesce: Optional[bool] = Field(None, description="是否合并执行")
    allow_concurrent: Optional[bool] = Field(None, description="是否允许并发执行")
    remark: Optional[str] = Field(None, description="备注信息")


class SchedulerJobSchemaOut(Schema):
    """定时任务输出"""
    id: str
    name: str
    code: str
    description: Optional[str] = None
    group: str
    trigger_type: str
    cron_expression: Optional[str] = None
    interval_seconds: Optional[int] = None
    run_date: Optional[datetime] = None
    task_func: str
    task_args: Optional[str] = None
    task_kwargs: Optional[str] = None
    status: int
    priority: int
    max_instances: int
    max_retries: int
    timeout: Optional[int] = None
    coalesce: bool
    allow_concurrent: bool
    total_run_count: int
    success_count: int
    failure_count: int
    last_run_time: Optional[datetime] = None
    next_run_time: Optional[datetime] = None
    last_run_status: Optional[str] = None
    last_run_result: Optional[str] = None
    remark: Optional[str] = None
    sys_create_datetime: Optional[datetime] = None
    sys_update_datetime: Optional[datetime] = None
    sort: int


class SchedulerJobSchemaDetail(SchedulerJobSchemaOut):
    """定时任务详情输出（包含统计信息）"""
    success_rate: Optional[float] = None

    @staticmethod
    def resolve_success_rate(obj):
        """计算成功率"""
        return obj.get_success_rate()


class SchedulerJobFilters(FilterSchema):
    """定时任务过滤器"""
    name: Optional[str] = Field(None, q='name__icontains')
    code: Optional[str] = Field(None, q='code__icontains')
    group: Optional[str] = Field(None, q='group')
    trigger_type: Optional[str] = Field(None, q='trigger_type')
    status: Optional[int] = Field(None, q='status')


class SchedulerJobSimpleOut(Schema):
    """定时任务简化输出（用于选择器）"""
    id: str
    name: str
    code: str
    group: str
    status: int


class SchedulerJobBatchDeleteIn(BaseModel):
    """批量删除输入"""
    ids: List[str] = Field(..., description="任务ID列表")


class SchedulerJobBatchDeleteOut(BaseModel):
    """批量删除输出"""
    count: int = Field(..., description="删除成功数量")
    failed_ids: List[str] = Field(default_factory=list, description="删除失败的ID列表")


class SchedulerJobBatchUpdateStatusIn(BaseModel):
    """批量更新状态输入"""
    ids: List[str] = Field(..., description="任务ID列表")
    status: int = Field(..., description="目标状态：0-禁用，1-启用，2-暂停")


class SchedulerJobBatchUpdateStatusOut(BaseModel):
    """批量更新状态输出"""
    count: int = Field(..., description="更新成功数量")


class SchedulerJobExecuteIn(BaseModel):
    """立即执行任务输入"""
    job_id: str = Field(..., description="任务ID")


class SchedulerJobExecuteOut(BaseModel):
    """立即执行任务输出"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(..., description="消息")
    log_id: Optional[str] = Field(None, description="日志ID")


class SchedulerJobStatisticsOut(BaseModel):
    """任务统计输出"""
    total_jobs: int = Field(..., description="总任务数")
    enabled_jobs: int = Field(..., description="启用任务数")
    disabled_jobs: int = Field(..., description="禁用任务数")
    paused_jobs: int = Field(..., description="暂停任务数")
    total_executions: int = Field(..., description="总执行次数")
    success_executions: int = Field(..., description="成功执行次数")
    failed_executions: int = Field(..., description="失败执行次数")
    success_rate: float = Field(..., description="成功率")


# ==================== SchedulerLog Schemas ====================

class SchedulerLogSchemaOut(Schema):
    """定时任务日志输出"""
    id: str
    job_id: str
    job_name: str
    job_code: str
    status: str
    start_time: datetime
    end_time: Optional[datetime] = None
    duration: Optional[float] = None
    result: Optional[str] = None
    exception: Optional[str] = None
    traceback: Optional[str] = None
    hostname: Optional[str] = None
    process_id: Optional[int] = None
    retry_count: int
    sys_create_datetime: Optional[datetime] = None


class SchedulerLogFilters(FilterSchema):
    """定时任务日志过滤器"""
    job_id: Optional[str] = Field(None, q='job_id')
    job_code: Optional[str] = Field(None, q='job_code__icontains')
    job_name: Optional[str] = Field(None, q='job_name__icontains')
    status: Optional[str] = Field(None, q='status')
    start_time__gte: Optional[datetime] = Field(None, q='start_time__gte')
    start_time__lte: Optional[datetime] = Field(None, q='start_time__lte')


class SchedulerLogBatchDeleteIn(BaseModel):
    """批量删除日志输入"""
    ids: List[str] = Field(..., description="日志ID列表")


class SchedulerLogBatchDeleteOut(BaseModel):
    """批量删除日志输出"""
    count: int = Field(..., description="删除成功数量")


class SchedulerLogCleanIn(BaseModel):
    """清理日志输入"""
    days: int = Field(..., description="保留最近N天的日志", ge=1)
    status: Optional[str] = Field(None, description="只清理指定状态的日志")


class SchedulerLogCleanOut(BaseModel):
    """清理日志输出"""
    count: int = Field(..., description="清理数量")
