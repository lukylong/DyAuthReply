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
    """Compatibility hook: OS lock release handles crashes; never unlink an inode.

    A stale PID is not evidence that another process does not currently own the
    same file, especially while upgrading between Python and Rust launchers.
    """
    return None


def acquire_instance_lock(data_dir: Path) -> object | None:
    """Nonblocking OS ownership. No truncate, PID write or deletion before lock."""
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = data_dir / 'launcher.lock'
    if lock_path.is_symlink():
        raise RuntimeError('launcher lock must not be a symbolic link')
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    fp = os.fdopen(fd, 'r+b')
    try:
        fp.seek(0)
        if sys.platform == 'win32':
            import msvcrt
            msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fp.close()
        return None
    try:
        fp.seek(0)
        fp.truncate()
        fp.write(str(os.getpid()).encode('ascii'))
        fp.flush()
        os.fsync(fp.fileno())
    except BaseException:
        fp.close()
        raise
    return fp
