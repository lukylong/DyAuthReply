#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Launcher stdout/stderr tee + Windows console encoding fixes."""
from __future__ import annotations

import io
import os
import sys
from datetime import datetime
from pathlib import Path


class _TeeStream:
    def __init__(self, primary: io.TextIOBase, log_path: Path) -> None:
        self._primary = primary
        self._log_path = log_path
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_fp = self._log_path.open('a', encoding='utf-8', buffering=1)

    def write(self, data: str) -> int:
        if not data:
            return 0
        try:
            self._primary.write(data)
            self._primary.flush()
        except Exception:
            pass
        try:
            self._log_fp.write(data)
            self._log_fp.flush()
        except Exception:
            pass
        return len(data)

    def flush(self) -> None:
        try:
            self._primary.flush()
        except Exception:
            pass
        try:
            self._log_fp.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        try:
            return self._primary.isatty()
        except Exception:
            return False

    @property
    def encoding(self) -> str:
        return 'utf-8'


def configure_windows_stdio() -> None:
    """Avoid UnicodeEncodeError on cp936/cp1252 consoles when printing Chinese."""
    if sys.platform != 'win32':
        return

    os.environ.setdefault('PYTHONUTF8', '1')
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

    for name in ('stdout', 'stderr'):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        buffer = getattr(stream, 'buffer', None)
        if buffer is None:
            continue
        wrapper = io.TextIOWrapper(
            buffer,
            encoding='utf-8',
            errors='replace',
            line_buffering=True,
        )
        setattr(sys, name, wrapper)


def setup_launcher_logging(data_dir: Path) -> Path:
    """Mirror launcher stdout/stderr to %APPDATA%/DyAuthReply/logs/launcher.log."""
    log_dir = data_dir / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / 'launcher.log'

    header = (
        f"\n{'=' * 60}\n"
        f"[{datetime.now().isoformat(timespec='seconds')}] launcher start "
        f"pid={os.getpid()} platform={sys.platform}\n"
    )
    try:
        with log_path.open('a', encoding='utf-8') as fp:
            fp.write(header)
    except Exception:
        pass

    if not isinstance(sys.stdout, _TeeStream):
        sys.stdout = _TeeStream(sys.stdout, log_path)  # type: ignore[assignment]
    if not isinstance(sys.stderr, _TeeStream):
        sys.stderr = _TeeStream(sys.stderr, log_path)  # type: ignore[assignment]

    return log_path
