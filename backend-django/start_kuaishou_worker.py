#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: start_kuaishou_worker.py
@Desc: Kuaishou Worker 独立进程入口

用法：
    python start_kuaishou_worker.py

说明：
    快手私信走 HTTP 协议路径，保活依赖 Cookie/token，不需要常驻浏览器。
    协议逆向完成前，worker 主循环会以"等待"姿态运行（不会崩溃），
    便于先联通管理后台、账号录入与数据链路。
"""
import asyncio
import logging
import os
import signal
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import django


def _setup_django() -> None:
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'application.settings')
    django.setup()


def _setup_logging() -> None:
    log_dir = Path(__file__).resolve().parent / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    worker_log_file = log_dir / 'kuaishou_worker.log'
    logging.basicConfig(
        level=os.environ.get('KUAISHOU_WORKER_LOG_LEVEL', 'INFO').upper(),
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            TimedRotatingFileHandler(
                str(worker_log_file),
                when='midnight',
                interval=1,
                backupCount=30,
                encoding='utf-8',
            ),
        ],
    )


async def _amain() -> None:
    from core.kuaishou.runtime.worker import KuaishouWorker

    worker = KuaishouWorker()

    loop = asyncio.get_running_loop()

    def _graceful(*_):
        logging.getLogger(__name__).info("收到退出信号，开始优雅关停 …")
        worker.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _graceful)
        except NotImplementedError:
            pass

    await worker.run()


def main() -> None:
    _setup_django()
    _setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("========== Kuaishou Worker 启动 ==========")
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Worker 异常退出: {e}")
        sys.exit(1)
    logger.info("========== Kuaishou Worker 正常退出 ==========")


if __name__ == '__main__':
    main()
