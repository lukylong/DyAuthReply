#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single-instance lock for DyAuthReply client launcher."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def acquire_instance_lock(data_dir: Path) -> object | None:
    """Return an open lock handle, or None if another instance holds the lock."""
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = data_dir / 'launcher.lock'

    if sys.platform == 'win32':
        import msvcrt

        fp = open(lock_path, 'a+b')
        try:
            fp.seek(0)
            fp.write(str(os.getpid()).encode())
            fp.flush()
            fp.seek(0)
            msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            fp.close()
            return None
        return fp

    import fcntl

    fp = open(lock_path, 'w')
    try:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fp.close()
        return None
    fp.write(str(os.getpid()))
    fp.flush()
    return fp
