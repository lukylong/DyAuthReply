#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single-instance lock for DyAuthReply client launcher."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _read_lock_pid(lock_path: Path) -> int | None:
    if not lock_path.is_file():
        return None
    try:
        raw = lock_path.read_bytes()[:32].decode('ascii', errors='ignore').strip()
        if raw.isdigit():
            return int(raw)
    except OSError:
        return None
    return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == 'win32':
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            pid,
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _clear_stale_lock(lock_path: Path) -> None:
    pid = _read_lock_pid(lock_path)
    if pid is None:
        return
    if not _pid_alive(pid):
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def acquire_instance_lock(data_dir: Path) -> object | None:
    """Return an open lock handle, or None if another instance holds the lock."""
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = data_dir / 'launcher.lock'
    _clear_stale_lock(lock_path)

    if sys.platform == 'win32':
        import msvcrt

        fp = open(lock_path, 'a+b')
        try:
            fp.seek(0)
            fp.truncate()
            fp.write(str(os.getpid()).encode('ascii'))
            fp.flush()
            fp.seek(0)
            msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            fp.close()
            holder = _read_lock_pid(lock_path)
            if holder is not None and not _pid_alive(holder):
                _clear_stale_lock(lock_path)
                return acquire_instance_lock(data_dir)
            return None
        return fp

    import fcntl

    fp = open(lock_path, 'w')
    try:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fp.close()
        holder = _read_lock_pid(lock_path)
        if holder is not None and not _pid_alive(holder):
            _clear_stale_lock(lock_path)
            return acquire_instance_lock(data_dir)
        return None
    fp.write(str(os.getpid()))
    fp.flush()
    return fp
