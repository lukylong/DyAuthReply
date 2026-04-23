#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: server_monitor_api.py
@Desc: 获取服务器完整监控信息 - try:
"""
# time: 2024/12/19
# file: server_monitor_api.py
# author: AI Assistant

from ninja import Router

from .server_info import ServerInfoCollector
from .server_monitor_schema import (
    ServerMonitorResponseSchema,
    RealtimeStatsSchema,
    BatteryInfoSchema
)

router = Router()

# 创建全局的服务器信息收集器实例
server_collector = ServerInfoCollector()


@router.get("/server_monitor/overview", response=ServerMonitorResponseSchema, auth=None)
def get_server_overview(request):
    """获取服务器完整监控信息"""
    try:
        data = server_collector.get_all_info()
        return data
    except Exception as e:
        print(f"Error in get_server_overview: {e}")
        # 返回错误信息，但保持API响应结构
        return {
            "basic_info": {
                "hostname": "error",
                "ip_address": "error",
                "system": "error",
                "platform": str(e),
                "architecture": "error",
                "processor": "error",
                "python_version": "error",
                "machine": "error",
                "node": "error",
                "release": "error",
                "version": "error"
            },
            "cpu_info": {
                "physical_cores": 0,
                "total_cores": 0,
                "cpu_percent": 0.0,
                "cpu_percent_per_core": [],
                "max_frequency": 0.0,
                "min_frequency": 0.0,
                "current_frequency": 0.0,
                "cpu_times": {},
                "cpu_stats": {}
            },
            "memory_info": {
                "virtual": {
                    "total": 0.0,
                    "available": 0.0,
                    "used": 0.0,
                    "free": 0.0,
                    "percent": 0.0,
                    "active": 0.0,
                    "inactive": 0.0,
                    "buffers": 0.0,
                    "cached": 0.0,
                    "shared": 0.0
                },
                "swap": {
                    "total": 0.0,
                    "used": 0.0,
                    "free": 0.0,
                    "percent": 0.0,
                    "sin": 0.0,
                    "sout": 0.0
                }
            },
            "disk_info": {
                "partitions": [],
                "total_read_bytes": 0.0,
                "total_write_bytes": 0.0,
                "total_read_count": 0,
                "total_write_count": 0
            },
            "network_info": {
                "total": {
                    "bytes_sent": 0.0,
                    "bytes_recv": 0.0,
                    "packets_sent": 0,
                    "packets_recv": 0,
                    "errin": 0,
                    "errout": 0,
                    "dropin": 0,
                    "dropout": 0
                },
                "per_interface": {},
                "interfaces": {},
                "connections": []
            },
            "process_info": {
                "total_processes": 0,
                "top_processes": [],
                "running_processes": 0,
                "sleeping_processes": 0
            },
            "system_load": {
                "load_1min": 0.0,
                "load_5min": 0.0,
                "load_15min": 0.0,
                "cpu_count": 0
            },
            "boot_time": {
                "boot_time": "",
                "uptime_seconds": 0,
                "uptime_formatted": "",
                "uptime_days": 0,
                "uptime_hours": 0,
                "uptime_minutes": 0
            },
            "users_info": [],
            "timestamp": ""
        }


@router.get("/server_monitor/realtime", response=RealtimeStatsSchema, auth=None)
def get_realtime_stats(request):
    """获取实时统计信息"""
    try:
        return server_collector.get_realtime_stats()
    except Exception as e:
        print(f"Error in get_realtime_stats: {e}")
        return {
            "cpu_percent": 0.0,
            "memory_percent": 0.0,
            "disk_io": {"read_speed": 0, "write_speed": 0},
            "network_io": {"upload_speed": 0, "download_speed": 0},
            "timestamp": ""
        }


@router.get("/server_monitor/basic_info", auth=None)
def get_basic_info(request):
    """获取基础系统信息"""
    try:
        return server_collector.get_basic_info()
    except Exception as e:
        return {"error": str(e)}


@router.get("/server_monitor/cpu_info", auth=None)
def get_cpu_info(request):
    """获取CPU信息"""
    try:
        return server_collector.get_cpu_info()
    except Exception as e:
        return {"error": str(e)}


@router.get("/server_monitor/memory_info", auth=None)
def get_memory_info(request):
    """获取内存信息"""
    try:
        return server_collector.get_memory_info()
    except Exception as e:
        return {"error": str(e)}


@router.get("/server_monitor/disk_info", auth=None)
def get_disk_info(request):
    """获取磁盘信息"""
    try:
        return server_collector.get_disk_info()
    except Exception as e:
        return {"error": str(e)}


@router.get("/server_monitor/network_info", auth=None)
def get_network_info(request):
    """获取网络信息"""
    try:
        return server_collector.get_network_info()
    except Exception as e:
        return {"error": str(e)}


@router.get("/server_monitor/process_info", auth=None)
def get_process_info(request):
    """获取进程信息"""
    try:
        return server_collector.get_process_info()
    except Exception as e:
        return {"error": str(e)}


@router.get("/server_monitor/system_load", auth=None)
def get_system_load(request):
    """获取系统负载信息"""
    try:
        return server_collector.get_system_load()
    except Exception as e:
        return {"error": str(e)}


@router.get("/server_monitor/boot_time", auth=None)
def get_boot_time(request):
    """获取系统启动时间信息"""
    try:
        return server_collector.get_boot_time()
    except Exception as e:
        return {"error": str(e)}


@router.get("/server_monitor/users_info", auth=None)
def get_users_info(request):
    """获取用户信息"""
    try:
        return server_collector.get_users_info()
    except Exception as e:
        return {"error": str(e)}


@router.get("/server_monitor/temperature_info", auth=None)
def get_temperature_info(request):
    """获取温度信息"""
    try:
        return server_collector.get_temperature_info()
    except Exception as e:
        return {"error": str(e)}


@router.get("/server_monitor/battery_info", response=BatteryInfoSchema, auth=None)
def get_battery_info(request):
    """获取电池信息"""
    try:
        battery_info = server_collector.get_battery_info()
        if battery_info:
            return battery_info
        else:
            return {"error": "No battery found"}
    except Exception as e:
        return {"error": str(e)}
