#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Self-hosted installer sync that publishes a version only after its files.

The public download page and the in-app update check used to read the version
from settings while the installers were copied by hand, so a deploy that ran
before CI finished advertised a new version and served the old installer.

Now the web process polls the public dist release (via China mirrors, direct
fallback), downloads every installer, checks sizes, and only then writes
``release.json`` in ``DOWNLOAD_LOCAL_DIR``. ``app-version`` reads that marker, so
the advertised version can never run ahead of the files.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

MARKER = "release.json"
LOCK = ".sync.lock"
DIST_RELEASE_TAG = "client-latest"
DEFAULT_MIRRORS = ("https://ghfast.top", "https://gh-proxy.com", "https://ghproxy.net")
POLL_SECONDS = 600
FIRST_DELAY_SECONDS = 30

_started = False
_started_lock = threading.Lock()


def source_bases(override: str = "") -> list[str]:
    explicit = override or os.environ.get("DOWNLOAD_SYNC_SOURCE_BASE", "")
    if explicit.strip():
        return [b.strip().rstrip("/") for b in explicit.split(",") if b.strip()]
    repo = settings.DOWNLOAD_DIST_REPO
    release = f"https://github.com/{repo}/releases/download/{DIST_RELEASE_TAG}"
    return [f"{m.rstrip('/')}/{release}" for m in DEFAULT_MIRRORS] + [
        f"https://github.com/{repo}/releases/latest/download"
    ]


def targets(only: str = "") -> list[str]:
    mapping = {
        "macos": settings.DOWNLOAD_MACOS_FILE,
        "windows": settings.DOWNLOAD_WINDOWS_FILE,
        "extension": settings.DOWNLOAD_EXTENSION_FILE,
    }
    keys = [k.strip() for k in only.split(",") if k.strip()] if only else list(mapping)
    return [mapping[k] for k in keys if k in mapping]


def local_release(local_dir: Optional[Path] = None) -> Optional[dict]:
    path = Path(local_dir or settings.DOWNLOAD_LOCAL_DIR) / MARKER
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("version") else None


def _fetch_json(client: httpx.Client, bases: list[str], name: str) -> Optional[dict]:
    for base in bases:
        try:
            resp = client.get(f"{base}/{name}")
            if resp.status_code < 400:
                data = resp.json()
                if isinstance(data, dict):
                    return data
        except (httpx.HTTPError, ValueError):
            continue
    return None


def _remote_size(client: httpx.Client, url: str) -> Optional[int]:
    try:
        resp = client.head(url)
        if resp.status_code >= 400:
            resp = client.get(url, headers={"Range": "bytes=0-0"})
        if resp.status_code >= 400:
            return None
        crange = resp.headers.get("Content-Range")
        if crange and "/" in crange:
            return int(crange.split("/")[-1])
        length = resp.headers.get("Content-Length")
        return int(length) if length else None
    except (httpx.HTTPError, ValueError):
        return None


def _download(client: httpx.Client, url: str, dest: Path, expected: Optional[int]) -> int:
    tmp = dest.with_suffix(dest.suffix + ".part")
    written = 0
    with client.stream("GET", url) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                fh.write(chunk)
                written += len(chunk)
    if written <= 0 or (expected is not None and written != expected):
        tmp.unlink(missing_ok=True)
        raise httpx.HTTPError(f"size mismatch: got {written}, expected {expected}")
    tmp.replace(dest)
    return written


def _sync_file(client: httpx.Client, bases: list[str], local_dir: Path, filename: str, force: bool) -> str:
    dest = local_dir / filename
    for base in bases:
        url = f"{base}/{filename}"
        size = _remote_size(client, url)
        if size is None:
            continue
        if not force and dest.is_file() and dest.stat().st_size == size:
            return "skipped"
        try:
            _download(client, url, dest, size)
            return "synced"
        except httpx.HTTPError as exc:
            logger.warning(f"[download_sync] {filename} 从 {base} 下载失败：{exc}")
    raise RuntimeError(f"{filename}：所有下载源均不可用")


def sync_release(force: bool = False, only: str = "", source_base: str = "") -> dict:
    """Downloads the current dist release, then publishes its version marker.

    Returns a summary; never raises for "already current" or "another sync running".
    """
    local_dir = Path(settings.DOWNLOAD_LOCAL_DIR)
    local_dir.mkdir(parents=True, exist_ok=True)
    with open(local_dir / LOCK, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy"}
        bases = source_bases(source_base)
        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            remote = _fetch_json(client, bases, "latest.json")
            if not remote or not remote.get("version"):
                return {"status": "unreachable"}
            version = str(remote["version"])
            current = local_release(local_dir)
            files = targets(only)
            if (
                not force
                and current
                and current.get("version") == version
                and all((local_dir / f).is_file() for f in files)
            ):
                return {"status": "current", "version": version}
            results = {f: _sync_file(client, bases, local_dir, f, force) for f in files}
            details = _fetch_json(client, bases, "client-release.json") or {}
        notes = details.get("notes") if details.get("version") == version else None
        marker = {
            "version": version,
            "pub_date": remote.get("pub_date"),
            "notes": notes,
            "synced_at": datetime.now(timezone.utc).isoformat(),
        }
        if not only:
            tmp = local_dir / (MARKER + ".part")
            tmp.write_text(json.dumps(marker, ensure_ascii=False), encoding="utf-8")
            tmp.replace(local_dir / MARKER)
        logger.info(f"[download_sync] 已同步客户端 {version}：{results}")
        return {"status": "synced", "version": version, "files": results}


def _loop() -> None:
    time.sleep(FIRST_DELAY_SECONDS)
    while True:
        try:
            sync_release()
        except Exception as exc:  # noqa: BLE001 - a background poller must survive any failure
            logger.warning(f"[download_sync] 自动同步失败，稍后重试：{type(exc).__name__}: {exc}")
        time.sleep(POLL_SECONDS)


def start_background() -> None:
    """Starts the poller once per process (web role only; see CoreConfig.ready)."""
    global _started
    with _started_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, name="download-sync", daemon=True).start()
