#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: server_monitor_schema.py
@Desc: Pydantic schemas
"""
# time: 2024/12/19
# file: server_monitor_schema.py
# author: AI Assistant

from typing import Dict, List, Any, Optional

from ninja import Schema


class BasicInfoSchema(Schema):
    hostname: str
    ip_address: str
    system: str
    platform: str
    architecture: str
    processor: str
    python_version: str
    machine: str
    node: str
    release: str
    version: str


class CpuInfoSchema(Schema):
    physical_cores: int
    total_cores: int
    cpu_percent: float
    cpu_percent_per_core: List[float]
    max_frequency: float
    min_frequency: float
    current_frequency: float
    cpu_times: Dict[str, Any]
    cpu_stats: Dict[str, Any]


class MemoryVirtualSchema(Schema):
    total: float
    available: float
    used: float
    free: float
    percent: float
    active: float
    inactive: float
    buffers: float
    cached: float
    shared: float


class MemorySwapSchema(Schema):
    total: float
    used: float
    free: float
    percent: float
    sin: float
    sout: float


class MemoryInfoSchema(Schema):
    virtual: MemoryVirtualSchema
    swap: MemorySwapSchema


class DiskPartitionSchema(Schema):
    device: str
    mountpoint: str
    file_system: str
    total_size: float
    used: float
    free: float
    percent: float


class DiskInfoSchema(Schema):
    partitions: List[DiskPartitionSchema]
    total_read_bytes: float
    total_write_bytes: float
    total_read_count: int
    total_write_count: int
    total_read_time: Optional[int] = None
    total_write_time: Optional[int] = None


class NetworkTotalSchema(Schema):
    bytes_sent: int  # 原始字节数
    bytes_recv: int  # 原始字节数
    packets_sent: int
    packets_recv: int
    errin: int
    errout: int
    dropin: int
    dropout: int


class NetworkInterfaceStatsSchema(Schema):
    bytes_sent: int  # 原始字节数
    bytes_recv: int  # 原始字节数
    packets_sent: int
    packets_recv: int
    errin: int  # 接收错误数
    errout: int  # 发送错误数
    dropin: int  # 接收丢包数
    dropout: int  # 发送丢包数


class NetworkAddressSchema(Schema):
    family: str
    address: str
    netmask: Optional[str]
    broadcast: Optional[str]


class NetworkInterfaceStatsDetailSchema(Schema):
    is_up: bool
    duplex: str
    speed: int
    mtu: int


class NetworkInterfaceSchema(Schema):
    addresses: List[NetworkAddressSchema]
    stats: NetworkInterfaceStatsDetailSchema


class NetworkConnectionSchema(Schema):
    local_address: str
    status: str
    pid: Optional[int]


class NetworkInfoSchema(Schema):
    total: NetworkTotalSchema
    per_interface: Dict[str, NetworkInterfaceStatsSchema]
    interfaces: Dict[str, NetworkInterfaceSchema]
    connections: List[NetworkConnectionSchema]


class ProcessSchema(Schema):
    pid: int
    name: str
    cpu_percent: float
    memory_percent: float
    status: str
    create_time: str


class ProcessInfoSchema(Schema):
    total_processes: int
    top_processes: List[ProcessSchema]
    running_processes: int
    sleeping_processes: int


class SystemLoadSchema(Schema):
    load_1min: float
    load_5min: float
    load_15min: float
    cpu_count: int


class BootTimeSchema(Schema):
    boot_time: str
    uptime_seconds: int
    uptime_formatted: str
    uptime_days: int
    uptime_hours: int
    uptime_minutes: int


class UserInfoSchema(Schema):
    name: str
    terminal: Optional[str]
    host: Optional[str]
    started: Optional[str]
    pid: Optional[int]


class BatteryInfoSchema(Schema):
    percent: float
    power_plugged: bool
    seconds_left: Optional[int]


class RealtimeStatsSchema(Schema):
    cpu_percent: float
    memory_percent: float
    disk_io: Dict[str, float]
    network_io: Dict[str, float]
    network_total: Dict[str, int]  # 网络累计统计
    disk_total: Dict[str, int]  # 磁盘累计统计
    cpu_details: Dict[str, Any]  # CPU详细信息
    memory_details: Dict[str, float]  # 内存详细信息
    system_load: Dict[str, float]  # 系统负载信息
    process_stats: Dict[str, int]  # 进程统计信息
    process_info: ProcessInfoSchema  # 详细进程信息
    network_interfaces: Dict[str, Dict[str, int]]  # 网络接口详细统计
    network_connections: List[Dict[str, Any]]  # 网络连接
    timestamp: str


class ServerMonitorResponseSchema(Schema):
    basic_info: BasicInfoSchema
    cpu_info: CpuInfoSchema
    memory_info: MemoryInfoSchema
    disk_info: DiskInfoSchema
    network_info: NetworkInfoSchema
    process_info: ProcessInfoSchema
    system_load: SystemLoadSchema
    boot_time: BootTimeSchema
    users_info: List[UserInfoSchema]
    timestamp: str
