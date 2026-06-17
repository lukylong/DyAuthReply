#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: start_douyin_worker.py
@Desc: Douyin Worker 独立进程入口

用法：
    python start_douyin_worker.py
或 docker：
    docker compose run --rm douyin-worker

依赖：
    纯 HTTP 协议，无浏览器；JS 签名走 Node.js（dy_ab.js + bd-ticket-guard），
    确保运行环境已安装 Node.js。
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
    worker_log_file = log_dir / 'douyin_worker.log'
    logging.basicConfig(
        level=os.environ.get('DOUYIN_WORKER_LOG_LEVEL', 'INFO').upper(),
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
    from core.douyin.runtime.worker import DouyinWorker

    worker = DouyinWorker()

    loop = asyncio.get_running_loop()

    def _graceful(*_):
        logging.getLogger(__name__).info("收到退出信号，开始优雅关停 …")
        asyncio.create_task(worker.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _graceful)
        except NotImplementedError:
            # Windows 下某些平台不支持 add_signal_handler
            pass

    await worker.run()


def main() -> None:
    _setup_django()
    _setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("========== Douyin Worker 启动 ==========")
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass
    except asyncio.CancelledError:
        pass
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Worker 异常退出: {e}")
        sys.exit(1)
    logger.info("========== Douyin Worker 正常退出 ==========")


if __name__ == '__main__':
    main()
