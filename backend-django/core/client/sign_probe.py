#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Probe Douyin JS sign engine readiness for packaged desktop client."""
from __future__ import annotations

from pathlib import Path

_cached: dict | None = None


def probe_sign_engine(*, force: bool = False) -> dict:
    global _cached
    if _cached is not None and not force:
        return _cached

    from core.douyin.runtime.transport.sign import js_signer

    node_bin = js_signer._node_bin()
    js_dir = js_signer._resolve_js_dir()
    node_modules = js_dir / 'node_modules'
    jsrsasign = node_modules / 'jsrsasign'
    dy_ab = js_dir / 'dy_ab.js'

    detail = ''
    ready = False
    try:
        ready = js_signer.is_available()
    except Exception as exc:  # noqa: BLE001
        detail = f'{type(exc).__name__}: {exc}'

    if not ready and not detail:
        if not dy_ab.is_file():
            detail = f'dy_ab.js missing: {dy_ab}'
        elif not jsrsasign.is_dir():
            detail = f'jsrsasign not installed under {node_modules}'
        elif not Path(node_bin).is_file():
            detail = f'Node.js not found: {node_bin}'
        else:
            detail = 'JS sign engine unavailable (see launcher.log / server.log)'

    _cached = {
        'sign_js_ready': ready,
        'node_bin': node_bin,
        'sign_js_dir': str(js_dir),
        'sign_js_modules_ready': jsrsasign.is_dir(),
        'sign_js_detail': detail if not ready else '',
    }
    return _cached
