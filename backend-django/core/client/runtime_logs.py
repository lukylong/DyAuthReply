#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""桌面客户端运行日志读取（tail 本地 log 文件）。"""
from __future__ import annotations

from pathlib import Path


def _data_root() -> Path:
    from env import CLIENT_DATA_DIR

    return Path(CLIENT_DATA_DIR)


def _candidate_files(data_root: Path) -> list[Path]:
    logs_dir = data_root / 'logs'
    names = (
        'server.log',
        'error.log',
        'douyin_worker.log',
        'client.log',
        'launcher.log',
    )
    files: list[Path] = []
    for name in names:
        path = logs_dir / name
        if path.is_file():
            files.append(path)
    migration = data_root / 'migration_error.log'
    if migration.is_file():
        files.append(migration)
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


def tail_runtime_logs(*, max_lines: int = 400, file_name: str | None = None) -> dict:
    data_root = _data_root()
    files = _candidate_files(data_root)
    if file_name:
        files = [p for p in files if p.name == file_name]

    if not files:
        return {
            'files': [],
            'content': '',
            'message': f'暂无日志文件（目录: {data_root / "logs"}）',
        }

    chunks: list[str] = []
    per_file = max(50, max_lines // max(len(files), 1))
    for path in files:
        lines = _tail_file(path, max_lines=per_file)
        if not lines:
            continue
        header = f'===== {path.name} ====='
        chunks.append(header)
        chunks.extend(line.rstrip('\n') for line in lines)

    content = '\n'.join(chunks)
    if len(content) > 512_000:
        content = content[-512_000:]

    return {
        'files': [p.name for p in files],
        'content': content,
        'message': '',
    }
