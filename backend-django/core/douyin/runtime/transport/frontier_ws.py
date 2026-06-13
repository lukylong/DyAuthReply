#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: transport/frontier_ws.py
@Desc: FrontierWsDecorator —— 主动连 frontier-im WebSocket 的实时入站信号（无浏览器）

    **主动**连 wss://frontier-im.douyin.com/ws/v2（纯 cookie，无浏览器）。

借鉴 DouYin_Spider/dy_apis/douyin_recv_msg.py 的鉴权（只需 cookie）：
    device_id = 用 cookie + a_bogus 调 query/user 拿
    access_key = md5(fpId + appKey + device_id + 盐)
    WS url = wss://.../ws/v2?aid&device_platform&fpid&device_id&token=cookie[sessionid]&access_key

策略：WS 帧只作"有新消息"信号，正文落库仍走 inner.scan_inbox（已用 JS 签名 + HTTP
验证过 status_code=0），避免 protobuf 解错入错库。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Any, List, Optional
from urllib.parse import urlencode

from core.douyin.runtime.transport.base import AccountTransport, InboundEvent
from core.douyin.runtime.transport.frontier import decode_frontier_frame

if TYPE_CHECKING:
    from core.douyin.douyin_account_model import DouyinAccount
    from core.douyin.douyin_rule_model import DouyinRule
    from core.douyin.runtime.message_store import ScannedMessage

logger = logging.getLogger(__name__)

# 鉴权常量（来自 DouYin_Spider/douyin_recv_msg.py，公开常量，非账号秘密）
_APP_KEY = "e1bd35ec9db7b8d846de66ed140b1ad9"
_FP_ID = "9"
_ACCESS_SALT = "f8a69f1719916z"
_WS_BASE = "wss://frontier-im.douyin.com/ws/v2"

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _access_key(device_id: str) -> str:
    """md5(fpId + appKey + deviceId + 盐) —— 全公开常量，不需私钥。"""
    raw = f"{_FP_ID}{_APP_KEY}{device_id}{_ACCESS_SALT}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items() if k)


class FrontierImWsClient:
    """主动连 frontier-im WS 的异步客户端；收到入向帧调 on_inbound(hint)。"""

    def __init__(
        self,
        account_id: str,
        cookies: dict[str, str],
        user_agent: str,
        on_inbound,
        *,
        proxy: Optional[str] = None,
    ) -> None:
        self._account_id = account_id
        self._cookies = cookies
        self._ua = user_agent or _DEFAULT_UA
        self._on_inbound = on_inbound
        self._proxy = proxy
        self._stopped = False
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def _get_device_id(self) -> str:
        """用 cookie + a_bogus 调 query/user 拿 device_id（对照 DouYin_Spider get_device_id）。"""
        import httpx

        from core.douyin.runtime.transport.sign import js_signer
        from core.douyin.runtime.transport.sign.mstoken import resolve_mstoken

        s_v_web_id = self._cookies.get("s_v_web_id", "")
        ms = resolve_mstoken(self._cookies)
        is_mac = "Macintosh" in self._ua
        params = {
            "device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web",
            "publish_video_strategy_type": "2", "pc_client_type": "1",
            "version_code": "290100", "version_name": "29.1.0",
            "cookie_enabled": "true", "screen_width": "1920", "screen_height": "1080",
            "browser_language": "zh-CN",
            "browser_platform": "MacIntel" if is_mac else "Win32",
            "browser_name": "Chrome", "browser_version": "124.0.0.0",
            "browser_online": "true", "engine_name": "Blink", "engine_version": "124.0.0.0",
            "os_name": "Mac OS" if is_mac else "Windows", "os_version": "10",
            "cpu_core_num": "8", "device_memory": "8", "platform": "PC",
            "verifyFp": s_v_web_id, "fp": s_v_web_id, "msToken": ms,
        }
        query = urlencode(params)
        a_bogus = await asyncio.to_thread(js_signer.get_ab, query, "")
        url = f"https://www.douyin.com/aweme/v1/web/query/user?{query}&a_bogus={a_bogus}"
        headers = {
            "user-agent": self._ua,
            "referer": "https://www.douyin.com/discover",
            "cookie": _cookie_header(self._cookies),
        }
        async with httpx.AsyncClient(timeout=15.0, proxy=self._proxy, verify=True) as c:
            r = await c.get(url, headers=headers)
            data = r.json()
            return str(data.get("id") or "")

    async def _build_url(self) -> tuple[str, str]:
        device_id = await self._get_device_id()
        if not device_id:
            raise RuntimeError("拿不到 device_id（query/user 返回空，cookie 可能缺 s_v_web_id）")
        params = {
            "aid": "6383", "device_platform": "douyin_pc", "fpid": _FP_ID,
            "device_id": device_id, "token": self._cookies.get("sessionid", ""),
            "access_key": _access_key(device_id),
        }
        return f"{_WS_BASE}?{urlencode(params)}", device_id

    async def run(self) -> None:
        """连接 + 接收循环 + 指数退避自动重连，直到 stop()。"""
        try:
            import websockets
        except ImportError:
            logger.error("[frontier.ws] websockets 未安装，frontier-im 实时监控不可用（pip install websockets）")
            return

        backoff = 2.0
        while not self._stopped:
            try:
                url, device_id = await self._build_url()
                headers = {
                    "User-Agent": self._ua,
                    "Cookie": _cookie_header(self._cookies),
                }
                logger.info(
                    f"[frontier.ws] 连接 frontier-im account={self._account_id} device_id={device_id}"
                )
                # subprotocol 必须用 subprotocols 参数声明（不能塞 header，否则 websockets 校验失败）；
                # 版本兼容：>=13 用 additional_headers，旧版用 extra_headers
                _subprotocols = ["binary", "base64", "pbbp2"]
                try:
                    conn = websockets.connect(
                        url, additional_headers=headers, subprotocols=_subprotocols,
                        origin="https://www.douyin.com",
                        max_size=None, ping_interval=20, ping_timeout=20,
                    )
                except TypeError:
                    conn = websockets.connect(
                        url, extra_headers=headers, subprotocols=_subprotocols,
                        origin="https://www.douyin.com",
                        max_size=None, ping_interval=20, ping_timeout=20,
                    )
                async with conn as ws:
                    self._connected = True
                    backoff = 2.0
                    logger.info(f"[frontier.ws] 已连接 account={self._account_id}")
                    async for message in ws:
                        if self._stopped:
                            break
                        self._handle_frame(message)
            except Exception as e:  # noqa: BLE001
                if self._stopped:
                    break
                logger.warning(
                    f"[frontier.ws] 断开/失败 account={self._account_id} "
                    f"err={type(e).__name__}: {e}；{backoff:.0f}s 后重连"
                )
            finally:
                self._connected = False
            if self._stopped:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
        logger.info(f"[frontier.ws] 接收循环退出 account={self._account_id}")

    def _handle_frame(self, message: Any) -> None:
        if isinstance(message, str):
            with suppress(Exception):
                message = message.encode("latin1")
        if not isinstance(message, (bytes, bytearray, memoryview)):
            return
        hint = decode_frontier_frame(bytes(message), url=_WS_BASE)
        if hint is None or hint.direction == "outbound":
            return
        with suppress(Exception):
            self._on_inbound(hint)

    async def stop(self) -> None:
        self._stopped = True


class FrontierWsDecorator(AccountTransport):
    """把 inner（HttpProtocolTransport）装饰成"frontier-im WS 实时唤醒 + inner 落库"。

    与 WsInboundDecorator 同契约（name="ws_inbound"，worker 已支持），但 WS 来源是
    主动连接的 frontier-im，不依赖浏览器。
    """

    name = "ws_inbound"

    def __init__(self, inner: AccountTransport) -> None:
        self._inner = inner
        self._signal: asyncio.Event = asyncio.Event()
        self._queue: "asyncio.Queue[InboundEvent]" = asyncio.Queue(maxsize=64)
        self._account_id: Optional[str] = None
        self._client: Optional[FrontierImWsClient] = None
        self._task: Optional[asyncio.Task] = None
        self._last_signal_ts: float = 0.0
        self._signal_min_gap_sec: float = 0.5

    async def start(self, account: "DouyinAccount") -> None:
        self._account_id = str(account.id)
        try:
            await self._inner.start(account)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[frontier.ws] inner.start 异常 account={self._account_id} "
                f"err={type(e).__name__}: {e}"
            )

        cookies = await _load_cookies(self._account_id)
        if not cookies.get("sessionid"):
            logger.warning(
                f"[frontier.ws] 账号无 sessionid，跳过 WS 实时监控（仍走 inner 轮询） "
                f"account={self._account_id}"
            )
            return

        self._client = FrontierImWsClient(
            account_id=self._account_id,
            cookies=cookies,
            user_agent=(getattr(account, "user_agent", "") or "").strip() or _DEFAULT_UA,
            on_inbound=self._on_inbound,
            proxy=(getattr(account, "proxy_url", "") or "").strip() or None,
        )
        self._task = asyncio.create_task(
            self._client.run(), name=f"frontier-ws-{self._account_id[:8]}"
        )
        logger.info(f"[frontier.ws] 启动实时监控 account={self._account_id}")

    async def stop(self, account: "DouyinAccount") -> None:
        if self._client is not None:
            await self._client.stop()
        if self._task is not None:
            self._task.cancel()
            with suppress(Exception, asyncio.CancelledError):
                await self._task
        self._signal.set()
        with suppress(Exception):
            await self._inner.stop(account)
        logger.info(f"[frontier.ws] 停止实时监控 account={self._account_id}")

    # ---------------- 主 verbs（委派 inner） ----------------
    async def scan_inbox(
        self, account: "DouyinAccount", *, max_conversations: int = 15,
        include_recent_without_unread: bool = False, conversation_hint: str | None = None,
    ) -> List["ScannedMessage"]:
        return await self._inner.scan_inbox(
            account, max_conversations=max_conversations,
            include_recent_without_unread=include_recent_without_unread,
            conversation_hint=conversation_hint,
        )

    async def send_reply(
        self, account: "DouyinAccount", page: Any, *, conversation_id: str,
        trigger_message_id: str, rule: "DouyinRule", peer_nickname: str = "",
    ) -> str:
        return await self._inner.send_reply(
            account, page, conversation_id=conversation_id,
            trigger_message_id=trigger_message_id, rule=rule, peer_nickname=peer_nickname,
        )

    async def send_text(
        self, account: "DouyinAccount", page: Any, *, conversation_id: str, text: str,
    ) -> str:
        return await self._inner.send_text(
            account, page, conversation_id=conversation_id, text=text,
        )

    # ---------------- 信号 ----------------
    async def wait_for_inbound_signal(self, *, timeout: float) -> Optional[InboundEvent]:
        try:
            await asyncio.wait_for(self._signal.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        self._signal.clear()
        evt: Optional[InboundEvent] = None
        with suppress(asyncio.QueueEmpty):
            evt = self._queue.get_nowait()
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        return evt

    def _on_inbound(self, hint) -> None:
        now = time.time()
        if now - self._last_signal_ts < self._signal_min_gap_sec:
            return
        self._last_signal_ts = now
        evt = InboundEvent(
            account_id=self._account_id or "", source="ws", ts=now,
            conversation_hint=hint.conversation_hint,
            raw={"text_preview": (hint.text_candidate or "")[:120],
                 "keywords": hint.keywords_matched},
        )
        try:
            self._queue.put_nowait(evt)
        except asyncio.QueueFull:
            with suppress(Exception):
                self._queue.get_nowait()
            with suppress(Exception):
                self._queue.put_nowait(evt)
        self._signal.set()
        logger.info(
            f"[frontier.ws] 收到 IM 信号 account={self._account_id} "
            f"text={evt.raw['text_preview']!r}"
        )


# ──────────────────────── helpers ────────────────────────


async def _load_cookies(account_id: str) -> dict[str, str]:
    from asgiref.sync import sync_to_async

    @sync_to_async
    def _load() -> dict[str, str]:
        try:
            from core.douyin.runtime.storage import load_storage_state
            state = load_storage_state(account_id)
        except Exception:  # noqa: BLE001
            return {}
        out: dict[str, str] = {}
        for c in (state or {}).get("cookies", []) if isinstance(state, dict) else []:
            name = str(c.get("name") or "")
            if name:
                out[name] = str(c.get("value") or "")
        return out

    return await _load()
