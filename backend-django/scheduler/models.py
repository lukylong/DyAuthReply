#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: models.py
@Desc: Scheduler Model - 定时任务模型 - 用于管理定时任务和执行记录
"""
"""
Scheduler Model - 定时任务模型
用于管理定时任务和执行记录
"""
from django.core.validators import RegexValidator
from django.db import models

from common.fu_model import RootModel


class SchedulerJob(RootModel):
    """
    定时任务模型 - 用于管理定时任务配置
    
    功能特点：
    1. 支持多种触发器类型（cron、interval、date）
    2. 支持任务启用/禁用
    3. 支持任务分组管理
    4. 支持任务优先级
    5. 记录任务执行统计信息
    6. 支持任务参数配置
    """

    # 任务类型选择
    TRIGGER_TYPE_CHOICES = [
        ('cron', 'Cron表达式'),
        ('interval', '间隔执行'),
        ('date', '指定时间'),
    ]

    # 任务状态选择
    STATUS_CHOICES = [
        (0, '禁用'),
        (1, '启用'),
        (2, '暂停'),
    ]

    # 任务名称
    name = models.CharField(
        max_length=128,
        help_text="任务名称",
        db_index=True,
    )

    # 任务编码（唯一标识）
    code = models.CharField(
        max_length=128,
        unique=True,
        help_text="任务编码",
        db_index=True,
        validators=[
            RegexValidator(
                regex=r'^[a-zA-Z0-9_]+$',
                message='任务编码只能包含字母、数字和下划线',
            )
        ]
    )

    # 任务描述
    description = models.TextField(
        blank=True,
        null=True,
        help_text="任务描述",
    )

    # 任务分组
    group = models.CharField(
        max_length=64,
        default='default',
        help_text="任务分组",
        db_index=True,
    )

    # 触发器类型
    trigger_type = models.CharField(
        max_length=20,
        choices=TRIGGER_TYPE_CHOICES,
        default='cron',
        help_text="触发器类型",
        db_index=True,
    )

    # Cron 表达式（用于 cron 类型）
    cron_expression = models.CharField(
        max_length=128,
        blank=True,
        null=True,
        help_text="Cron表达式（如：0 0 * * *）",
    )

    # 间隔时间（秒，用于 interval 类型）
    interval_seconds = models.IntegerField(
        blank=True,
        null=True,
        help_text="间隔时间（秒）",
    )

    # 指定执行时间（用于 date 类型）
    run_date = models.DateTimeField(
        blank=True,
        null=True,
        help_text="指定执行时间",
    )

    # 任务函数路径（如：core.scheduler.tasks.test_task）
    task_func = models.CharField(
        max_length=256,
        help_text="任务函数路径",
    )

    # 任务参数（JSON格式）
    task_args = models.TextField(
        blank=True,
        null=True,
        help_text="任务位置参数（JSON数组格式）",
    )

    # 任务关键字参数（JSON格式）
    task_kwargs = models.TextField(
        blank=True,
        null=True,
        help_text="任务关键字参数（JSON对象格式）",
    )

    # 任务状态
    status = models.IntegerField(
        choices=STATUS_CHOICES,
        default=0,
        help_text="任务状态",
        db_index=True,
    )

    # 任务优先级（数字越大优先级越高）
    priority = models.IntegerField(
        default=0,
        help_text="任务优先级",
        db_index=True,
    )

    # 最大实例数（同时运行的任务实例数）
    max_instances = models.IntegerField(
        default=1,
        help_text="最大实例数",
    )

    # 错误重试次数
    max_retries = models.IntegerField(
        default=0,
        help_text="错误重试次数",
    )

    # 超时时间（秒）
    timeout = models.IntegerField(
        blank=True,
        null=True,
        help_text="超时时间（秒）",
    )

    # 是否合并执行（如果上次未执行完，是否跳过本次）
    coalesce = models.BooleanField(
        default=True,
        help_text="是否合并执行",
    )

    # 是否允许并发执行
    allow_concurrent = models.BooleanField(
        default=False,
        help_text="是否允许并发执行",
    )

    # 执行统计
    total_run_count = models.IntegerField(
        default=0,
        help_text="总执行次数",
    )

    success_count = models.IntegerField(
        default=0,
        help_text="成功次数",
    )

    failure_count = models.IntegerField(
        default=0,
        help_text="失败次数",
    )

    # 最后执行时间
    last_run_time = models.DateTimeField(
        blank=True,
        null=True,
        help_text="最后执行时间",
    )

    # 下次执行时间
    next_run_time = models.DateTimeField(
        blank=True,
        null=True,
        help_text="下次执行时间",
    )

    # 最后执行状态
    last_run_status = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        help_text="最后执行状态",
    )

    # 最后执行结果
    last_run_result = models.TextField(
        blank=True,
        null=True,
        help_text="最后执行结果",
    )

    # 备注
    remark = models.TextField(
        blank=True,
        null=True,
        help_text="备注信息",
    )

    class Meta:
        db_table = "core_scheduler_job"
        ordering = ("-priority", "-sys_update_datetime")
        verbose_name = "定时任务"
        verbose_name_plural = verbose_name
        indexes = [
            models.Index(fields=['status', 'trigger_type']),
            models.Index(fields=['group', 'status']),
            models.Index(fields=['priority', 'status']),
            models.Index(fields=['next_run_time', 'status']),
        ]

    def __str__(self):
        return f"{self.name} ({self.code})"

    def is_enabled(self):
        """判断任务是否启用"""
        return self.status == 1

    def is_paused(self):
        """判断任务是否暂停"""
        return self.status == 2

    def is_disabled(self):
        """判断任务是否禁用"""
        return self.status == 0

    def get_status_display_name(self):
        """获取状态的显示名称"""
        status_map = dict(self.STATUS_CHOICES)
        return status_map.get(self.status, 'UNKNOWN')

    def get_trigger_type_display_name(self):
        """获取触发器类型的显示名称"""
        type_map = dict(self.TRIGGER_TYPE_CHOICES)
        return type_map.get(self.trigger_type, 'UNKNOWN')

    def get_success_rate(self):
        """获取成功率"""
        if self.total_run_count == 0:
            return 0
        return round(self.success_count / self.total_run_count * 100, 2)

    def increment_run_count(self, success=True):
        """增加执行次数"""
        self.total_run_count += 1
        if success:
            self.success_count += 1
        else:
            self.failure_count += 1
        self.save(update_fields=['total_run_count', 'success_count', 'failure_count'])


class SchedulerLog(RootModel):
    """
    定时任务执行日志模型
    
    功能特点：
    1. 记录每次任务执行的详细信息
    2. 记录执行时间、状态、结果
    3. 记录异常信息
    4. 支持日志查询和统计
    """

    # 执行状态选择
    STATUS_CHOICES = [
        ('pending', '等待执行'),
        ('running', '执行中'),
        ('success', '执行成功'),
        ('failed', '执行失败'),
        ('timeout', '执行超时'),
        ('skipped', '跳过执行'),
    ]

    # 关联的任务
    job = models.ForeignKey(
        to="SchedulerJob",
        on_delete=models.CASCADE,
        db_constraint=False,
        help_text="关联的任务",
        related_name="logs",
    )

    # 任务名称（冗余字段，便于查询）
    job_name = models.CharField(
        max_length=128,
        help_text="任务名称",
        db_index=True,
    )

    # 任务编码（冗余字段，便于查询）
    job_code = models.CharField(
        max_length=128,
        help_text="任务编码",
        db_index=True,
    )

    # 执行状态
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        help_text="执行状态",
        db_index=True,
    )

    # 开始时间
    start_time = models.DateTimeField(
        help_text="开始时间",
        db_index=True,
    )

    # 结束时间
    end_time = models.DateTimeField(
        blank=True,
        null=True,
        help_text="结束时间",
    )

    # 执行耗时（秒）
    duration = models.FloatField(
        blank=True,
        null=True,
        help_text="执行耗时（秒）",
    )

    # 执行结果
    result = models.TextField(
        blank=True,
        null=True,
        help_text="执行结果",
    )

    # 异常信息
    exception = models.TextField(
        blank=True,
        null=True,
        help_text="异常信息",
    )

    # 异常堆栈
    traceback = models.TextField(
        blank=True,
        null=True,
        help_text="异常堆栈",
    )

    # 执行主机
    hostname = models.CharField(
        max_length=128,
        blank=True,
        null=True,
        help_text="执行主机",
    )

    # 进程ID
    process_id = models.IntegerField(
        blank=True,
        null=True,
        help_text="进程ID",
    )

    # 重试次数
    retry_count = models.IntegerField(
        default=0,
        help_text="重试次数",
    )

    class Meta:
        db_table = "core_scheduler_log"
        ordering = ("-start_time",)
        verbose_name = "定时任务执行日志"
        verbose_name_plural = verbose_name
        indexes = [
            models.Index(fields=['job', 'status']),
            models.Index(fields=['status', 'start_time']),
            models.Index(fields=['job_code', 'start_time']),
        ]

    def __str__(self):
        return f"{self.job_name} - {self.status} - {self.start_time}"

    def is_success(self):
        """判断是否执行成功"""
        return self.status == 'success'

    def is_failed(self):
        """判断是否执行失败"""
        return self.status == 'failed'

    def is_running(self):
        """判断是否正在执行"""
        return self.status == 'running'

    def get_status_display_name(self):
        """获取状态的显示名称"""
        status_map = dict(self.STATUS_CHOICES)
        return status_map.get(self.status, 'UNKNOWN')
