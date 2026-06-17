#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve Node.js for DyAuthReply client (dev launcher + PyInstaller bundle)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from shutil import which


def bundled_root() -> Path | None:
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    return None


def _runtime_node_names() -> tuple[str, ...]:
    if sys.platform == 'win32':
        return ('node.exe', 'node')
    return ('node',)


def _iter_node_candidates(*, app_root: Path | None = None) -> list[Path]:
    candidates: list[Path] = []

    root = bundled_root()
    if root is not None:
        for name in _runtime_node_names():
            candidates.append(root / 'runtime' / name)

    if app_root is not None:
        runtime = app_root / 'dyauthreply-client' / 'launcher' / 'runtime'
        for name in _runtime_node_names():
            candidates.append(runtime / name)

    if sys.platform == 'darwin':
        candidates.extend(
            [
                Path('/opt/homebrew/bin/node'),
                Path('/usr/local/bin/node'),
            ]
        )
        nvm_dir = os.environ.get('NVM_DIR', str(Path.home() / '.nvm'))
        nvm_base = Path(nvm_dir) / 'versions' / 'node'
        if nvm_base.is_dir():
            for ver_dir in sorted(nvm_base.iterdir(), reverse=True):
                candidates.append(ver_dir / 'bin' / 'node')
    elif sys.platform == 'win32':
        program_files = os.environ.get('ProgramFiles', r'C:\Program Files')
        candidates.extend(
            [
                Path(program_files) / 'nodejs' / 'node.exe',
                Path(r'C:\Program Files\nodejs\node.exe'),
            ]
        )

    return candidates


def resolve_node_bin(*, app_root: Path | None = None) -> str | None:
    configured = os.environ.get('DOUYIN_NODE_BIN', '').strip()
    if configured and Path(configured).is_file():
        return configured

    for path in _iter_node_candidates(app_root=app_root):
        if path.is_file():
            return str(path.resolve())

    found = which('node')
    if found and Path(found).is_file():
        return str(Path(found).resolve())
    return None


def configure_node_env(*, app_root: Path | None = None) -> str | None:
    """Set DOUYIN_NODE_BIN / EXECJS_RUNTIME before Django or worker start."""
    os.environ.setdefault('EXECJS_RUNTIME', 'Node')
    node = resolve_node_bin(app_root=app_root)
    if node:
        os.environ['DOUYIN_NODE_BIN'] = node
    return node
