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
