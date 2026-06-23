#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Desktop client self-update channel: query hosted server for latest version."""
from __future__ import annotations

import logging
import platform
import re
from typing import Any

import httpx
from ninja.errors import HttpError

from core.client.license_auth import (
    _build_client_auth_endpoint,
    _client_app_version,
    load_license_state,
)

logger = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"\d+")


def _parse_version(value: str) -> tuple[int, ...]:
    """把 'v0.1.10' / '0.1.10-beta' 解析成可比较的整数元组。"""
    text = (value or "").strip().lstrip("vV")
    if not text:
        return (0,)
    head = text.split("-")[0].split("+")[0]
    parts = [int(m) for m in _VERSION_RE.findall(head)]
    return tuple(parts) if parts else (0,)


def _is_newer(latest: str, current: str) -> bool:
    a = _parse_version(latest)
    b = _parse_version(current)
    length = max(len(a), len(b))
    a += (0,) * (length - len(a))
    b += (0,) * (length - len(b))
    return a > b


def _resolve_download_url(remote: dict[str, Any]) -> str:
    system = platform.system().lower()
    if system == "darwin":
        return (remote.get("macos_url") or "").strip()
    if system == "windows":
        return (remote.get("windows_url") or "").strip()
    return (remote.get("release_page") or "").strip()


def _fetch_remote_version() -> dict[str, Any]:
    state = load_license_state()
    server_url = (state.get("server_url") or "").strip()
    endpoint = _build_client_auth_endpoint(server_url, "/app-version")
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(endpoint)
    except httpx.RequestError as exc:
        raise HttpError(503, f"升级服务不可达：{exc}") from exc
    if response.status_code >= 400:
        raise HttpError(response.status_code, "升级服务返回异常")
    try:
        return response.json()
    except ValueError as exc:
        raise HttpError(502, "升级服务返回了无效响应") from exc


def check_app_update(current_version: str = "") -> dict[str, Any]:
    """检查客户端是否有新版本。current_version 缺省时回退到本地记录的版本。"""
    current = (current_version or "").strip() or _client_app_version()
    remote = _fetch_remote_version()
    latest = (remote.get("version") or "").strip()
    download_url = _resolve_download_url(remote)
    has_update = bool(latest) and _is_newer(latest, current)
    return {
        "current_version": current.lstrip("vV"),
        "latest_version": latest,
        "has_update": has_update,
        "mandatory": bool(remote.get("mandatory")) and has_update,
        "notes": (remote.get("notes") or "").strip(),
        "download_url": download_url,
        "release_page": (remote.get("release_page") or "").strip(),
    }
