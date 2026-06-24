#!/usr/bin/env python3
"""Write bundled launcher defaults used by packaged sidecar builds."""

from __future__ import annotations

import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "dyauthreply-client" / "launcher" / "generated_defaults.json"
TAURI_CONF = ROOT / "dyauthreply-client" / "desktop" / "src-tauri" / "tauri.conf.json"


def _read_tauri_version() -> str:
    if not TAURI_CONF.is_file():
        return ""
    try:
        data = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return str(data.get("version") or "").strip()


def main() -> None:
    payload = {
        "client_license_server_url": (
            os.environ.get("CLIENT_LICENSE_SERVER_URL") or "http://127.0.0.1:8000"
        ).strip(),
        "license_lease_public_key_b64": (os.environ.get("LICENSE_LEASE_PUBLIC_KEY_B64") or "").strip(),
        "license_lease_public_key": (os.environ.get("LICENSE_LEASE_PUBLIC_KEY") or "").strip(),
        "client_app_version": _read_tauri_version(),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote launcher defaults -> {OUTPUT} (client_app_version={payload['client_app_version'] or '?'})")


if __name__ == "__main__":
    main()
