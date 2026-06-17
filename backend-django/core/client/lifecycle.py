#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""客户端生命周期：优雅退出 launcher 及其子进程。"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

_shutdown_lock = threading.Lock()
_shutdown_scheduled = False


def _launcher_pid() -> int:
    from env import CLIENT_DATA_DIR

    lock_path = Path(CLIENT_DATA_DIR) / 'launcher.lock'
    if lock_path.is_file():
        try:
            return int(lock_path.read_text(encoding='utf-8').strip())
        except (ValueError, OSError):
            pass
    return os.getpid()


def _terminate_pid(pid: int) -> None:
    if pid <= 0:
        return
    if sys.platform == 'win32':
        subprocess.run(
            ['taskkill', '/F', '/T', '/PID', str(pid)],
            capture_output=True,
            timeout=15,
            check=False,
        )
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        os.kill(pid, signal.SIGKILL)


def schedule_shutdown(*, reason: str = 'client') -> dict:
    """异步终止 launcher 进程树（dev: 读 lock 文件；bundled: 当前进程）。"""
    global _shutdown_scheduled
    with _shutdown_lock:
        if _shutdown_scheduled:
            return {'ok': True, 'already': True}
        _shutdown_scheduled = True

    target_pid = _launcher_pid()

    def _fire() -> None:
        time.sleep(0.25)
        _terminate_pid(target_pid)

    threading.Thread(target=_fire, name='client-shutdown', daemon=True).start()
    return {'ok': True, 'reason': reason, 'pid': target_pid}
