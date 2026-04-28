#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: send_template_cache.py
@Desc: 浏览器成功发送模板缓存

目标：
  - 按 (account_id, platform_conversation_id) 缓存最近一次“浏览器真实发送成功”的请求体
  - 给 HTTP send 重放实验提供同会话模板，避免继续拿错会话模板
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Optional

from asgiref.sync import sync_to_async

from core.douyin.runtime.storage import _data_dir


def _cache_file(account_id: str) -> Path:
    path = _data_dir() / "storage" / f"{account_id}.send_template_cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_cached_send_template(account_id: str, conversation_id: str) -> Optional[bytes]:
    path = _cache_file(account_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    item = data.get(str(conversation_id)) or {}
    if not isinstance(item, dict):
        return None
    body_b64 = item.get("body_b64")
    if not isinstance(body_b64, str) or not body_b64:
        return None
    try:
        return base64.b64decode(body_b64)
    except Exception:
        return None


def save_cached_send_template(
    account_id: str,
    conversation_id: str,
    request_body: bytes,
    *,
    source: str,
    request_len: int,
) -> None:
    path = _cache_file(account_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data[str(conversation_id)] = {
        "body_b64": base64.b64encode(request_body).decode("ascii"),
        "request_len": int(request_len),
        "source": source,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _decode_request_conversation_id(raw: bytes) -> str:
    from core.douyin.runtime.transport.wire.codec import (
        get_first_bytes,
        get_first_str,
        iter_fields,
    )

    top: dict[int, list] = {}
    for fnum, _wt, val in iter_fields(raw):
        top.setdefault(fnum, []).append(val)
    inner = get_first_bytes(top, 8)
    if not inner:
        return ""
    for fnum, _wt, val in iter_fields(inner):
        if fnum != 100 or not isinstance(val, (bytes, bytearray)):
            continue
        fields: dict[int, list] = {}
        for f2, _wt2, v2 in iter_fields(bytes(val)):
            fields.setdefault(f2, []).append(v2)
        return get_first_str(fields, 1)
    return ""


@sync_to_async
def bind_platform_conversation_id(
    account_id: str,
    db_conversation_id: str,
    platform_conversation_id: str,
) -> None:
    if not db_conversation_id or not platform_conversation_id:
        return
    from core.douyin.douyin_conversation_model import DouyinConversation

    DouyinConversation.objects.filter(
        id=db_conversation_id,
        account_id=account_id,
    ).update(platform_conversation_id=platform_conversation_id)


@sync_to_async
def capture_latest_success_template_from_sniff(
    account_id: str,
    conversation_id: str,
) -> bool:
    from core.douyin.runtime.transport.wire.im_protocol import decode_send_message_response

    account_dir = _data_dir() / "sniff" / f"account_{account_id}"
    if not account_dir.exists():
        return False

    sessions = sorted(account_dir.glob("session_*.jsonl"), reverse=True)
    for session in sessions[:4]:
        pending: list[tuple[bytes, str, int]] = []
        try:
            with session.open("r", encoding="utf-8") as fp:
                for line in fp:
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    if (
                        row.get("type") == "http_request"
                        and row.get("url") == "https://imapi.douyin.com/v1/message/send"
                        and row.get("method") == "POST"
                        and isinstance(row.get("post_b64"), str)
                    ):
                        try:
                            req_raw = base64.b64decode(row["post_b64"])
                        except Exception:
                            continue
                        req_conv = _decode_request_conversation_id(req_raw)
                        pending.append((req_raw, req_conv, int(row.get("post_len") or len(req_raw))))
                        continue

                    if (
                        row.get("type") == "http_response"
                        and row.get("url") == "https://imapi.douyin.com/v1/message/send"
                        and row.get("method") == "POST"
                        and int(row.get("status") or 0) == 200
                        and bool(row.get("ok")) is True
                        and isinstance(row.get("body_b64"), str)
                        and pending
                    ):
                        try:
                            resp_raw = base64.b64decode(row["body_b64"])
                        except Exception:
                            continue
                        result = decode_send_message_response(resp_raw)
                        req_raw, req_conv, req_len = pending.pop()
                        if req_conv != conversation_id:
                            continue
                        if result.biz_status_code in (0, 8101) and not result.biz_status_text:
                            save_cached_send_template(
                                account_id,
                                conversation_id,
                                req_raw,
                                source=f"sniff:{session.name}",
                                request_len=req_len,
                            )
                            return True
        except OSError:
            continue
    return False


def dump_cached_send_template_meta(account_id: str, conversation_id: str) -> dict:
    path = _cache_file(account_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    item = data.get(str(conversation_id)) or {}
    if not isinstance(item, dict):
        return {}
    return {
        "source": item.get("source") or "",
        "request_len": int(item.get("request_len") or 0),
        "has_body": isinstance(item.get("body_b64"), str) and bool(item.get("body_b64")),
    }
