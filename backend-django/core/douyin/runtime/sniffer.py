#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: sniffer.py
@Desc: Douyin IM 协议 Sniffer

旁路监听 Playwright BrowserContext 的 WebSocket 帧 + HTTP 请求/响应，
把抖音创作者中心私信相关的流量落到 JSONL，便于后续协议化改造分析。

设计目标：
  - 对生产无侵入，默认关闭，由 settings.DOUYIN_SNIFFER_ENABLED 显式打开
  - 不修改任何业务请求；只 hook 监听器
  - 大 body 自动截断，避免磁盘爆炸
  - 每账号独立 JSONL 文件，分析脚本可按账号过滤

输出布局：
    {DOUYIN_DATA_DIR}/sniff/
        account_{id}/
            session_{epoch_seconds}.jsonl

事件类型（每行一条 JSON）：
    session_open     抓包会话开始（含 url_keywords / max_body 配置）
    session_close    抓包会话结束（含计数）
    cookies          context 创建后的 cookie 快照（仅记录抖音域）
    ws_open          WebSocket 建立
    ws_send          客户端发出帧
    ws_recv          服务端推下帧
    ws_close         WebSocket 关闭
    http_request     HTTP 请求（含 headers / post_data）
    http_response    HTTP 响应（含 status / headers / body 截断）

二进制载荷统一 base64；text 帧直接 utf-8。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from django.conf import settings

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page, Request, Response, WebSocket

logger = logging.getLogger(__name__)


# 默认匹配关键词（命中即记录）。覆盖：
#   - 创作者中心私信前端调用的 HTTP API（/aweme/v1/im/、/aweme/im/v1/ 等）
#   - 字节家族 IM 长连接（frontier-msns、imapi、snssdk 等）
#   - 通用兜底（im / message / conversation / send_message）
_DEFAULT_URL_KEYWORDS: tuple[str, ...] = (
    "imapi",
    "im.douyin",
    "im_api",
    "frontier",
    "snssdk",
    "/aweme/im/",
    "/aweme/v1/im/",
    "/im/",
    "/message",
    "/conversation",
    "send_message",
    "fetch_message",
    "msg_data",
    "im_chat",
    "im_user",
    "wss-aweme",
    "/v1/message",
    "im-api",
)


def is_enabled() -> bool:
    """是否开启 sniffer（settings.DOUYIN_SNIFFER_ENABLED）。"""
    return bool(getattr(settings, "DOUYIN_SNIFFER_ENABLED", False))


def _sniffer_root() -> Path:
    explicit = getattr(settings, "DOUYIN_SNIFFER_DIR", "") or ""
    if explicit:
        root = Path(explicit)
    else:
        data_dir = Path(
            getattr(settings, "DOUYIN_DATA_DIR", "/var/lib/zq-platform/douyin")
        )
        root = data_dir / "sniff"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _max_body_bytes() -> int:
    try:
        return int(getattr(settings, "DOUYIN_SNIFFER_MAX_BODY", 32768))
    except Exception:
        return 32768


def _url_keywords() -> tuple[str, ...]:
    raw = getattr(settings, "DOUYIN_SNIFFER_URL_KEYWORDS", "")
    if isinstance(raw, str) and raw.strip():
        items = tuple(s.strip().lower() for s in raw.split(",") if s.strip())
        if items:
            return items
    return _DEFAULT_URL_KEYWORDS


def _matches(url: str, keywords: tuple[str, ...]) -> bool:
    if not url:
        return False
    u = url.lower()
    return any(k in u for k in keywords)


def _payload_to_b64(payload) -> tuple[Optional[str], int, bool, str]:
    """
    把 WS 帧 / HTTP body 统一编码成 (b64, original_len, truncated, encoding)。

    encoding:
        'binary'  bytes / bytearray
        'text'    str（保留原始 utf-8 转 base64）
        'none'    payload 为 None
    """
    max_body = _max_body_bytes()
    if payload is None:
        return None, 0, False, "none"
    if isinstance(payload, str):
        raw = payload.encode("utf-8", errors="replace")
        encoding = "text"
    elif isinstance(payload, (bytes, bytearray)):
        raw = bytes(payload)
        encoding = "binary"
    else:
        raw = str(payload).encode("utf-8", errors="replace")
        encoding = "text"
    n = len(raw)
    truncated = False
    if n > max_body:
        raw = raw[:max_body]
        truncated = True
    return base64.b64encode(raw).decode("ascii"), n, truncated, encoding


class _AccountSniffer:
    """单账号 sniffer 实例，绑定一个 BrowserContext 的生命周期。"""

    def __init__(self, account_id: str, context: "BrowserContext") -> None:
        self.account_id = str(account_id)
        self._context = context
        self._loop = asyncio.get_event_loop()
        self._fp = None
        self._lock = asyncio.Lock()
        self._keywords = _url_keywords()
        self._stats = {
            "ws_open": 0,
            "ws_send": 0,
            "ws_recv": 0,
            "ws_close": 0,
            "http_request": 0,
            "http_response": 0,
            "filtered_out": 0,
        }
        ts = int(time.time())
        out_dir = _sniffer_root() / f"account_{self.account_id}"
        out_dir.mkdir(parents=True, exist_ok=True)
        self._path = out_dir / f"session_{ts}.jsonl"

        # 保存事件回调引用，detach 时移除
        self._handlers: list[tuple[str, callable]] = []  # type: ignore[type-arg]
        self._page_ws_attached: set[int] = set()
        # 第一次抓到 IM 流量时再补一次 cookies 快照，
        # 因为 launch_persistent_context 返回时持久化 cookie 通常还没加载到内存上下文。
        self._warm_cookies_done = False

    # ------------- 文件 -------------
    def _open(self) -> None:
        if self._fp is not None:
            return
        self._fp = open(self._path, "a", encoding="utf-8", buffering=1)
        self._write_event(
            "session_open",
            {
                "account_id": self.account_id,
                "pid": os.getpid(),
                "url_keywords": list(self._keywords),
                "max_body": _max_body_bytes(),
                "path": str(self._path),
            },
        )
        logger.info(
            f"[sniffer] 已开始抓取 account={self.account_id} path={self._path}"
        )

    def _write_event(self, evt_type: str, payload: dict) -> None:
        if self._fp is None:
            return
        line = {
            "ts": time.time(),
            "type": evt_type,
            "account_id": self.account_id,
            **payload,
        }
        try:
            self._fp.write(json.dumps(line, ensure_ascii=False) + "\n")
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[sniffer] 写入失败 account={self.account_id} err={e}"
            )

    # ------------- 公开生命周期 -------------
    async def attach(self) -> None:
        self._open()

        # 抖音域 cookie 快照（用于后续协议层鉴权对照）
        with suppress(Exception):
            cookies = await self._context.cookies()
            douyin_cookies = [
                c
                for c in cookies
                if "douyin" in (c.get("domain") or "")
                or "snssdk" in (c.get("domain") or "")
                or "bytedance" in (c.get("domain") or "")
            ]
            self._write_event(
                "cookies",
                {
                    "phase": "attach",
                    "count_total": len(cookies),
                    "count_douyin": len(douyin_cookies),
                    "cookies": douyin_cookies,
                },
            )

        # 已有页面（持久化 context 启动后第一个空白 tab 等）
        for page in list(self._context.pages):
            self._attach_page(page)

        page_handler = self._on_new_page
        req_handler = self._on_request
        finished_handler = self._on_request_finished
        failed_handler = self._on_request_failed

        self._context.on("page", page_handler)
        self._context.on("request", req_handler)
        self._context.on("requestfinished", finished_handler)
        self._context.on("requestfailed", failed_handler)

        self._handlers = [
            ("page", page_handler),
            ("request", req_handler),
            ("requestfinished", finished_handler),
            ("requestfailed", failed_handler),
        ]

    async def detach(self) -> None:
        for evt, handler in self._handlers:
            with suppress(Exception):
                self._context.remove_listener(evt, handler)
        self._handlers.clear()

        with suppress(Exception):
            cookies = await self._context.cookies()
            douyin_cookies = [
                c
                for c in cookies
                if "douyin" in (c.get("domain") or "")
                or "snssdk" in (c.get("domain") or "")
                or "bytedance" in (c.get("domain") or "")
            ]
            self._write_event(
                "cookies",
                {
                    "phase": "detach",
                    "count_total": len(cookies),
                    "count_douyin": len(douyin_cookies),
                    "cookies": douyin_cookies,
                },
            )

        self._write_event("session_close", {"stats": dict(self._stats)})
        if self._fp is not None:
            with suppress(Exception):
                self._fp.flush()
                self._fp.close()
            self._fp = None
        logger.info(
            f"[sniffer] 已停止抓取 account={self.account_id} stats={self._stats} path={self._path}"
        )

    # ------------- 内部事件 -------------
    def _on_new_page(self, page: "Page") -> None:
        self._attach_page(page)

    def _attach_page(self, page: "Page") -> None:
        pid = id(page)
        if pid in self._page_ws_attached:
            return
        self._page_ws_attached.add(pid)
        with suppress(Exception):
            page.on("websocket", self._on_websocket)

    def _on_websocket(self, ws: "WebSocket") -> None:
        url = ws.url or ""
        # WS 全量记录（数量不会很多，且这就是我们最关心的目标）
        self._stats["ws_open"] += 1
        self._write_event("ws_open", {"url": url})

        def on_send(payload):
            self._stats["ws_send"] += 1
            b64, raw_len, truncated, enc = _payload_to_b64(payload)
            self._write_event(
                "ws_send",
                {
                    "url": url,
                    "len": raw_len,
                    "truncated": truncated,
                    "encoding": enc,
                    "b64": b64,
                },
            )

        def on_recv(payload):
            self._stats["ws_recv"] += 1
            b64, raw_len, truncated, enc = _payload_to_b64(payload)
            self._write_event(
                "ws_recv",
                {
                    "url": url,
                    "len": raw_len,
                    "truncated": truncated,
                    "encoding": enc,
                    "b64": b64,
                },
            )

        def on_close():
            self._stats["ws_close"] += 1
            self._write_event("ws_close", {"url": url})

        with suppress(Exception):
            ws.on("framesent", on_send)
            ws.on("framereceived", on_recv)
            ws.on("close", on_close)

    def _on_request(self, request: "Request") -> None:
        url = request.url or ""
        if not _matches(url, self._keywords):
            self._stats["filtered_out"] += 1
            return
        self._stats["http_request"] += 1
        post_b64 = None
        post_len = 0
        post_trunc = False
        post_enc = "none"
        with suppress(Exception):
            buf = request.post_data_buffer
            if buf is not None:
                post_b64, post_len, post_trunc, post_enc = _payload_to_b64(buf)
            else:
                pd = request.post_data
                if pd is not None:
                    post_b64, post_len, post_trunc, post_enc = _payload_to_b64(pd)
        try:
            headers = dict(request.headers)
        except Exception:
            headers = {}
        self._write_event(
            "http_request",
            {
                "method": request.method,
                "url": url,
                "resource_type": request.resource_type,
                "headers": headers,
                "post_b64": post_b64,
                "post_len": post_len,
                "post_truncated": post_trunc,
                "post_encoding": post_enc,
            },
        )

    def _on_request_finished(self, request: "Request") -> None:
        url = request.url or ""
        if not _matches(url, self._keywords):
            return
        # 调度后台读取响应 body，避免阻塞事件循环
        try:
            self._loop.create_task(self._record_response(request))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[sniffer] 调度响应读取失败 err={e}")

    def _on_request_failed(self, request: "Request") -> None:
        url = request.url or ""
        if not _matches(url, self._keywords):
            return
        failure_text = ""
        with suppress(Exception):
            failure_text = (request.failure or "")
        self._write_event(
            "http_response",
            {
                "url": url,
                "status": 0,
                "ok": False,
                "failure": failure_text,
                "method": request.method,
            },
        )

    async def _record_response(self, request: "Request") -> None:
        url = request.url or ""
        try:
            response: Optional[Response] = await request.response()
        except Exception:
            response = None
        if response is None:
            return
        self._stats["http_response"] += 1
        # 首次抓到响应时补一次 cookies 快照（此时持久化 cookie 必然已加载）
        if not self._warm_cookies_done:
            self._warm_cookies_done = True
            with suppress(Exception):
                cookies = await self._context.cookies()
                douyin_cookies = [
                    c
                    for c in cookies
                    if "douyin" in (c.get("domain") or "")
                    or "snssdk" in (c.get("domain") or "")
                    or "bytedance" in (c.get("domain") or "")
                ]
                self._write_event(
                    "cookies",
                    {
                        "phase": "warm",
                        "count_total": len(cookies),
                        "count_douyin": len(douyin_cookies),
                        "cookies": douyin_cookies,
                    },
                )
        body_b64 = None
        body_len = 0
        body_trunc = False
        body_enc = "none"
        with suppress(Exception):
            body = await response.body()
            body_b64, body_len, body_trunc, body_enc = _payload_to_b64(body)
        try:
            resp_headers = dict(response.headers)
        except Exception:
            resp_headers = {}
        self._write_event(
            "http_response",
            {
                "url": url,
                "method": request.method,
                "status": response.status,
                "ok": response.ok,
                "headers": resp_headers,
                "body_b64": body_b64,
                "body_len": body_len,
                "body_truncated": body_trunc,
                "body_encoding": body_enc,
            },
        )


class SnifferManager:
    """进程级 sniffer 注册中心。"""

    _instances: dict[str, _AccountSniffer] = {}
    _lock = asyncio.Lock()

    @classmethod
    async def attach(cls, account_id: str, context: "BrowserContext") -> None:
        if not is_enabled():
            return
        async with cls._lock:
            key = str(account_id)
            if key in cls._instances:
                return
            sniffer = _AccountSniffer(key, context)
            cls._instances[key] = sniffer
        try:
            await sniffer.attach()
        except Exception as e:  # noqa: BLE001
            logger.error(f"[sniffer] attach 失败 account={account_id} err={e}")
            async with cls._lock:
                cls._instances.pop(str(account_id), None)

    @classmethod
    async def detach(cls, account_id: str) -> None:
        async with cls._lock:
            sniffer = cls._instances.pop(str(account_id), None)
        if sniffer is None:
            return
        with suppress(Exception):
            await sniffer.detach()

    @classmethod
    async def detach_all(cls) -> None:
        async with cls._lock:
            sniffers = list(cls._instances.values())
            cls._instances.clear()
        for s in sniffers:
            with suppress(Exception):
                await s.detach()

    @classmethod
    def active_account_ids(cls) -> list[str]:
        return list(cls._instances.keys())
