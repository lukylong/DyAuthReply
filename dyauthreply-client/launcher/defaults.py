#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Load build-time launcher defaults bundled with the sidecar."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_launcher_defaults(search_root: Path) -> dict[str, Any]:
    candidates = (
        search_root / "launcher" / "generated_defaults.json",
        search_root / "dyauthreply-client" / "launcher" / "generated_defaults.json",
        search_root / "generated_defaults.json",
    )
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def read_tauri_app_version(search_root: Path) -> str:
    """从 tauri.conf.json 读取客户端版本（开发态兜底）。"""
    candidates = (
        search_root / "dyauthreply-client" / "desktop" / "src-tauri" / "tauri.conf.json",
        search_root / "desktop" / "src-tauri" / "tauri.conf.json",
    )
    for path in candidates:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        version = str(data.get("version") or "").strip()
        if version:
            return version
    return ""
