#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: worker.py
@Desc: Douyin Worker - 常驻 worker 主循环
        独立进程入口见 backend-django/start_douyin_worker.py（M2 创建）。
        从 DB 加载在线账号 → 为每个账号启动 asyncio.Task →
        执行"扫收件箱 → 匹配规则 → 拟人化发送"闭环，并维护心跳与命令监听。

@TODO M2~M5:
    - class DouyinWorker:
        - async def run(self)
        - async def _account_loop(self, account)
        - async def _handle_command(self, account_id, cmd)  # scan / relogin / send_test
        - async def _tick_heartbeat(self)
    - 监听 Redis 频道 `douyin:cmd:{account_id}` 接收前端/API 下发的即时指令
"""

import asyncio


class DouyinWorker:
    """常驻 worker 骨架"""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}

    async def run(self) -> None:
        """主入口（M2 实现）"""
        raise NotImplementedError("DouyinWorker.run 将在 M2 里程碑实现")

    async def stop(self) -> None:
        """优雅关停（M2 实现）"""
        raise NotImplementedError("DouyinWorker.stop 将在 M2 里程碑实现")
