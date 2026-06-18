#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""桌面客户端运行日志读取（tail 本地 log 文件）。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path


def _data_root() -> Path:
    from env import CLIENT_DATA_DIR

    return Path(CLIENT_DATA_DIR)


def _logs_dir(data_root: Path | None = None) -> Path:
    root = data_root or _data_root()
    return root / 'logs'


def _known_log_names() -> tuple[str, ...]:
    return (
        'launcher.log',
        'server.log',
        'error.log',
        'douyin_worker.log',
        'client.log',
    )


def ensure_runtime_log_files() -> Path:
    """Ensure log directory exists and seed empty files so admin console can tail them."""
    logs_dir = _logs_dir()
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for name in _known_log_names():
        path = logs_dir / name
        if path.is_file() and path.stat().st_size > 0:
            continue
        path.write_text(f'[{stamp}] log file initialized\n', encoding='utf-8')
    migration = _data_root() / 'migration_error.log'
    if migration.is_file() and migration.stat().st_size == 0:
        migration.unlink(missing_ok=True)
    return logs_dir


def _candidate_files(data_root: Path) -> list[Path]:
    logs_dir = _logs_dir(data_root)
    seen: set[str] = set()
    files: list[Path] = []

    def _add(path: Path) -> None:
        if not path.is_file() or path.name in seen:
            return
        seen.add(path.name)
        files.append(path)

    for name in _known_log_names():
        _add(logs_dir / name)

    if logs_dir.is_dir():
        for path in sorted(logs_dir.glob('*.log'), key=lambda p: p.stat().st_mtime, reverse=True):
            _add(path)

    _add(data_root / 'migration_error.log')
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def list_runtime_log_files() -> list[dict]:
    data_root = _data_root()
    out: list[dict] = []
    for path in _candidate_files(data_root):
        stat = path.stat()
        out.append(
            {
                'name': path.name,
                'path': str(path),
                'size': stat.st_size,
                'modified_at': stat.st_mtime,
            }
        )
    return out


def _tail_file(path: Path, *, max_lines: int) -> list[str]:
    if not path.is_file() or max_lines <= 0:
        return []
    try:
        with path.open('r', encoding='utf-8', errors='replace') as fp:
            return fp.readlines()[-max_lines:]
    except OSError:
        return []


def _format_file_summary(files: list[Path]) -> str:
    parts: list[str] = []
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            size = -1
        parts.append(f'{path.name}({size}B)')
    return ', '.join(parts)


def tail_runtime_logs(*, max_lines: int = 400, file_name: str | None = None) -> dict:
    data_root = _data_root()
    logs_dir = _logs_dir(data_root)
    files = _candidate_files(data_root)
    if file_name:
        files = [p for p in files if p.name == file_name]

    if not files:
        return {
            'files': [],
            'content': '',
            'message': f'暂无日志文件。请确认服务已启动，目录: {logs_dir}',
            'log_dir': str(logs_dir),
        }

    chunks: list[str] = []
    per_file = max(50, max_lines // max(len(files), 1))
    for path in files:
        lines = _tail_file(path, max_lines=per_file)
        if not lines:
            try:
                size = path.stat().st_size
            except OSError:
                size = -1
            chunks.append(f'===== {path.name} (empty, {size}B) =====')
            continue
        header = f'===== {path.name} ====='
        chunks.append(header)
        chunks.extend(line.rstrip('\n') for line in lines)

    content = '\n'.join(chunks)
    if len(content) > 512_000:
        content = content[-512_000:]

    message = ''
    if not content.strip():
        message = f'日志文件存在但尚无内容: {_format_file_summary(files)}。目录: {logs_dir}'

    return {
        'files': [p.name for p in files],
        'content': content,
        'message': message,
        'log_dir': str(logs_dir),
    }
