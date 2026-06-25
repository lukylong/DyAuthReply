#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Launcher stdout/stderr tee + Windows console encoding fixes.

launcher.log 采用「按天分片 + 仅保留最近 N 天」策略（默认 7 天，CLIENT_LOG_BACKUP_DAYS
可覆盖），与 Django/worker 的 TimedRotatingFileHandler 行为一致：当天写入 launcher.log，
跨天后归档为 launcher.log.YYYY-MM-DD，并自动删除过期归档。
"""
from __future__ import annotations

import io
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 归档文件名形如 server.log.2026-06-24 / launcher.log.2026-06-24(.1)
_DATE_SUFFIX_RE = re.compile(r'\.(\d{4}-\d{2}-\d{2})(?:\.\d+)?$')


def _backup_days() -> int:
    try:
        return max(1, int(os.environ.get('CLIENT_LOG_BACKUP_DAYS', '7')))
    except (TypeError, ValueError):
        return 7


def _max_log_bytes() -> int:
    try:
        mb = max(1, int(os.environ.get('CLIENT_LOG_MAX_MB', '10')))
    except (TypeError, ValueError):
        mb = 10
    return mb * 1024 * 1024


def prune_old_logs(logs_dir: Path, *, backup_days: int | None = None) -> None:
    """删除 logs 目录下超过 backup_days 天的按天归档日志（*.log.YYYY-MM-DD）。

    只清理带日期后缀的归档；当前活动文件（*.log）保留。统一兜底清理，覆盖
    launcher / server / error 等所有按天滚动文件遗留的过期备份。
    """
    days = backup_days if backup_days is not None else _backup_days()
    if days <= 0 or not logs_dir.is_dir():
        return
    cutoff = datetime.now().date() - timedelta(days=days)
    for path in logs_dir.iterdir():
        if not path.is_file():
            continue
        m = _DATE_SUFFIX_RE.search(path.name)
        if not m:
            continue
        try:
            file_date = datetime.strptime(m.group(1), '%Y-%m-%d').date()
        except ValueError:
            continue
        if file_date < cutoff:
            try:
                path.unlink()
            except OSError:
                pass


class _DailyRotatingTee:
    """把 stdout/stderr 镜像到按天分片的 launcher.log（跨天自动归档 + 清理过期）。"""

    def __init__(self, primary: io.TextIOBase, log_path: Path, *, backup_days: int | None = None) -> None:
        self._primary = primary
        self._log_path = log_path
        self._backup_days = backup_days if backup_days is not None else _backup_days()
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._date = self._today()
        # 启动时若现有 launcher.log 是「以前某天」的，先归档，避免新旧日志混在一个文件里
        self._archive_stale_on_start()
        self._log_fp = self._log_path.open('a', encoding='utf-8', buffering=1)
        prune_old_logs(self._log_path.parent, backup_days=self._backup_days)

    @staticmethod
    def _today() -> str:
        return datetime.now().strftime('%Y-%m-%d')

    def _archive_stale_on_start(self) -> None:
        """启动时归档「隔天的」或「体积过大的」现有 launcher.log，另起当天新文件。

        体积保护用于一次性收掉历史遗留的、从不滚动的超大 launcher.log；也兜底防止
        单日日志暴涨成巨型文件。阈值 CLIENT_LOG_MAX_MB（默认 10）。
        """
        try:
            stat = self._log_path.stat() if self._log_path.exists() else None
            if not stat or stat.st_size == 0:
                return
            mdate = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d')
            oversized = stat.st_size > _max_log_bytes()
            if mdate != self._date or oversized:
                self._archive(mdate)
        except OSError:
            pass

    def _archive(self, date_str: str) -> None:
        dest = self._log_path.with_name(f'{self._log_path.name}.{date_str}')
        if dest.exists():
            idx = 1
            while True:
                alt = self._log_path.with_name(f'{self._log_path.name}.{date_str}.{idx}')
                if not alt.exists():
                    dest = alt
                    break
                idx += 1
        try:
            self._log_path.replace(dest)
        except OSError:
            pass

    def _maybe_rollover(self) -> None:
        now = self._today()
        if now == self._date:
            return
        try:
            self._log_fp.close()
        except Exception:
            pass
        self._archive(self._date)
        self._date = now
        self._log_fp = self._log_path.open('a', encoding='utf-8', buffering=1)
        prune_old_logs(self._log_path.parent, backup_days=self._backup_days)

    def write(self, data: str) -> int:
        if not data:
            return 0
        try:
            self._primary.write(data)
            self._primary.flush()
        except Exception:
            pass
        try:
            self._maybe_rollover()
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
    """Mirror launcher stdout/stderr to %APPDATA%/DyAuthReply/logs/launcher.log.

    按天分片：当天写 launcher.log，跨天归档为 launcher.log.YYYY-MM-DD，仅保留最近
    CLIENT_LOG_BACKUP_DAYS（默认 7）天。
    """
    log_dir = data_dir / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / 'launcher.log'

    # 先把 stdout/stderr 切到按天滚动的 tee（构造时会归档隔天的旧文件并清理过期归档），
    # 再写启动 banner，确保 banner 落在当天的新文件里。
    if not isinstance(sys.stdout, _DailyRotatingTee):
        sys.stdout = _DailyRotatingTee(sys.stdout, log_path)  # type: ignore[assignment]
    if not isinstance(sys.stderr, _DailyRotatingTee):
        sys.stderr = _DailyRotatingTee(sys.stderr, log_path)  # type: ignore[assignment]

    header = (
        f"\n{'=' * 60}\n"
        f"[{datetime.now().isoformat(timespec='seconds')}] launcher start "
        f"pid={os.getpid()} platform={sys.platform}\n"
    )
    try:
        sys.stdout.write(header)
    except Exception:
        pass

    return log_path
