#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: core/client/card_sync.py
@Desc: 客户端卡片同步 —— 把本地（真源）卡片元数据 + 封面图经 license 通道推送到公网，
       供公网托管落地页 /c/<id> 与封面（抖音爬虫抓 og 渲染卡片）。

客户端为真源：本地 DouyinCard 是权威数据，worker 读本地判启用/拼落地页 URL。
本模块只负责"上行同步"，公网不反向写客户端。鉴权复用 license 的 activation_id + activation_token。
失败不静默：抛出/返回错误并由调用方在卡片上打 sync_state=failed。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from core.client.license_auth import (
    _build_client_auth_endpoint,
    load_license_state,
)

logger = logging.getLogger("core.client.card_sync")

_TIMEOUT = 15.0


class CardSyncError(Exception):
    """卡片同步失败（网络/鉴权/服务端错误）。调用方据此标记 sync_state=failed。"""


def _activation_credentials() -> tuple[str, str, str]:
    """从本地 license-state 取 (server_url, activation_id, activation_token)。

    未激活或缺凭证时抛 CardSyncError —— 客户端可先本地建卡（sync_state=pending），激活后补推。
    """
    state = load_license_state()
    server_url = (state.get("server_url") or "").strip()
    activation_id = (state.get("activation_id") or "").strip()
    activation_token = (state.get("activation_token") or "").strip()
    if not activation_id or not activation_token:
        raise CardSyncError("客户端尚未激活或缺少激活凭证，无法同步卡片到公网")
    return server_url, activation_id, activation_token


def _post(server_url: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    endpoint = _build_client_auth_endpoint(server_url, path)
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(endpoint, json=payload)
    except httpx.RequestError as exc:
        raise CardSyncError(f"公网卡片服务不可达：{exc}") from exc
    if resp.status_code >= 400:
        raise CardSyncError(f"卡片同步失败 HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        return resp.json()
    except ValueError as exc:
        raise CardSyncError("公网卡片服务返回了无效响应") from exc


def push_card_upsert(card: Any) -> dict[str, Any]:
    """把一张本地卡片（DouyinCard 实例或等价字段对象）upsert 到公网。返回 {id, landing_url, ok}。"""
    server_url, activation_id, activation_token = _activation_credentials()
    payload = {
        "activation_id": activation_id,
        "activation_token": activation_token,
        "id": str(card.id),
        "title": card.title or "",
        "description": getattr(card, "description", "") or "",
        "cover_file_id": getattr(card, "cover_file_id", None) or None,
        "target_url": card.target_url or "",
        "remark": getattr(card, "remark", None),
        "status": bool(getattr(card, "status", True)),
    }
    return _post(server_url, "/cards/upsert", payload)


def push_card_delete(card_id: str) -> dict[str, Any]:
    """通知公网删除（停用/软删）对应卡片。"""
    server_url, activation_id, activation_token = _activation_credentials()
    return _post(server_url, "/cards/delete", {
        "activation_id": activation_id,
        "activation_token": activation_token,
        "id": str(card_id),
    })


def push_cover(file_bytes: bytes, filename: str, content_type: str = "application/octet-stream") -> dict[str, Any]:
    """把封面图经 license 通道 multipart 转发到公网 file_manager，返回 {cover_file_id, cover_url}。"""
    server_url, activation_id, activation_token = _activation_credentials()
    endpoint = _build_client_auth_endpoint(server_url, "/cards/cover")
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(
                endpoint,
                data={"activation_id": activation_id, "activation_token": activation_token},
                files={"file": (filename, file_bytes, content_type)},
            )
    except httpx.RequestError as exc:
        raise CardSyncError(f"公网封面服务不可达：{exc}") from exc
    if resp.status_code >= 400:
        raise CardSyncError(f"封面上传失败 HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        return resp.json()
    except ValueError as exc:
        raise CardSyncError("公网封面服务返回了无效响应") from exc


def try_sync_card(card: Any) -> Optional[str]:
    """尽力同步一张卡片，返回 sync_state（'synced'/'failed'）。不抛异常，供 CRUD 钩子调用。"""
    try:
        push_card_upsert(card)
        return "synced"
    except CardSyncError as exc:
        logger.warning("[card_sync] upsert 失败 card=%s err=%s", getattr(card, "id", "?"), exc)
        return "failed"


def try_sync_delete(card_id: str) -> bool:
    """尽力同步删除，返回是否成功。不抛异常。"""
    try:
        push_card_delete(card_id)
        return True
    except CardSyncError as exc:
        logger.warning("[card_sync] delete 失败 card=%s err=%s", card_id, exc)
        return False
