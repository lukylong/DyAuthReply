#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: transport/frontier_ws.py
@Desc: FrontierWsDecorator —— 主动连 frontier-im WebSocket 的实时收信与落库通道（无浏览器）
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Any, List, Optional
from urllib.parse import urlencode

from asgiref.sync import sync_to_async

from core.douyin.runtime.transport.base import AccountTransport, InboundEvent
from core.douyin.runtime.transport.frontier_ws_decoder import decode_frontier_ws_messages, encode_ws_ack
from core.douyin.runtime.message_store import ScannedMessage

if TYPE_CHECKING:
    from core.douyin.douyin_account_model import DouyinAccount
    from core.douyin.douyin_rule_model import DouyinRule

logger = logging.getLogger(__name__)

# 鉴权常量（来自 DouYin_Spider/douyin_recv_msg.py，公开常量，非账号秘密）
_APP_KEY = "e1bd35ec9db7b8d846de66ed140b1ad9"
_FP_ID = "9"
_ACCESS_SALT = "f8a69f1719916z"
_WS_BASE = "wss://frontier-im.douyin.com/ws/v2"

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _access_key(device_id: str) -> str:
    """md5(fpId + appKey + deviceId + 盐) —— 全公开常量，不需私钥。"""
    raw = f"{_FP_ID}{_APP_KEY}{device_id}{_ACCESS_SALT}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items() if k)


class FrontierImWsClient:
    """主动连 frontier-im WS 的异步客户端；收到消息反序列化为 IMMessage 并通过 on_inbound(msg) 派发。"""

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
        self._ws = None
        self._frames_total = 0
        self._reconnect_count = 0

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
                    self._ws = ws
                    self._connected = True
                    backoff = 2.0
                    is_reconnect = self._reconnect_count > 0
                    logger.info(
                        f"[frontier.ws] 已连接 account={self._account_id} "
                        f"{'(重连)' if is_reconnect else ''}".rstrip()
                    )
                    await _log_ws_event(
                        self._account_id,
                        "ws_connected",
                        "info",
                        "WS已连接（重连）" if is_reconnect else "WS已连接",
                        f"device_id={device_id} reconnect_count={self._reconnect_count}",
                        {
                            "device_id": device_id,
                            "reconnect": is_reconnect,
                            "reconnect_count": self._reconnect_count,
                            "frames_total": self._frames_total,
                        },
                    )
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
                await _log_ws_event(
                    self._account_id,
                    "ws_disconnected",
                    "warning",
                    "WS断开，准备重连",
                    f"err={type(e).__name__}: {e}; backoff={backoff:.0f}s",
                    {
                        "error": f"{type(e).__name__}: {e}",
                        "backoff_s": backoff,
                        "frames_total": self._frames_total,
                        "reconnect_count": self._reconnect_count,
                    },
                )
            finally:
                self._connected = False
                self._ws = None
            if self._stopped:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
            self._reconnect_count += 1
        logger.info(f"[frontier.ws] 接收循环退出 account={self._account_id}")

    def _handle_frame(self, message: Any) -> None:
        if isinstance(message, str):
            with suppress(Exception):
                message = message.encode("latin1")
        if not isinstance(message, (bytes, bytearray, memoryview)):
            return

        self._frames_total += 1
        logger.debug(f"[frontier.ws] 收到原始二进制帧 account={self._account_id} len={len(message)}")

        # 深度解包二进制帧
        msgs, log_id, ack_ext = decode_frontier_ws_messages(bytes(message))
        logger.debug(
            f"[frontier.ws] 解包结果 account={self._account_id}: "
            f"msgs_len={len(msgs)} log_id={log_id} ack_ext={ack_ext!r}"
        )

        # 若需要且支持 ACK，进行 ACK 帧回传
        if log_id and ack_ext:
            asyncio.create_task(self._send_ack(log_id, ack_ext))

        # 派发解出的每一条 IM 消息
        if msgs:
            for m in msgs:
                with suppress(Exception):
                    self._on_inbound(m)

    async def _send_ack(self, log_id: int, ack_ext: str) -> None:
        if self._ws is None or not self._connected:
            return
        try:
            ack_bytes = encode_ws_ack(log_id, ack_ext)
            if ack_bytes:
                await self._ws.send(ack_bytes)
                logger.debug(f"[frontier.ws] 成功回发确认帧 log_id={log_id}")
        except Exception as e:
            logger.warning(f"[frontier.ws] 发送确认帧失败 log_id={log_id}: {e}")

    async def stop(self) -> None:
        self._stopped = True


class FrontierWsDecorator(AccountTransport):
    """把 inner（HttpProtocolTransport）装饰成 "完全走 WebSocket 直接消息解析并实时落库"。

    与 WsInboundDecorator 同契约（name="ws_inbound"，worker 已支持）。
    """

    name = "ws_inbound"

    def __init__(self, inner: AccountTransport) -> None:
        self._inner = inner
        self._signal: asyncio.Event = asyncio.Event()
        self._queue: "asyncio.Queue[InboundEvent]" = asyncio.Queue(maxsize=64)
        self._scanned_messages_queue: "asyncio.Queue[ScannedMessage]" = asyncio.Queue(maxsize=64)
        self._account_id: Optional[str] = None
        self._account_sec_uid: Optional[str] = None
        self._self_uid: int = 0
        self._client: Optional[FrontierImWsClient] = None
        self._task: Optional[asyncio.Task] = None
        self._last_http_fallback_at: float = 0.0

    async def start(self, account: "DouyinAccount") -> None:
        self._account_id = str(account.id)
        self._account_sec_uid = str(account.sec_uid or "").strip()
        try:
            await self._inner.start(account)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[frontier.ws] inner.start 异常 account={self._account_id} "
                f"err={type(e).__name__}: {e}"
            )

        cookies = await _load_cookies(self._account_id)
        missing = [k for k in ("sessionid", "s_v_web_id") if not cookies.get(k)]
        if missing:
            logger.warning(
                f"[frontier.ws] 账号缺少 cookie 字段 {missing}，跳过 WS 实时监控"
                f"（仍走 inner 轮询）account={self._account_id}"
            )
            await _log_ws_event(
                self._account_id,
                "ws_skipped",
                "warning",
                "WS未启用：cookie 缺关键字段",
                f"缺少 {missing}，已退回 HTTP 轮询收信",
                {"missing_cookies": missing},
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

    # ---------------- 主 verbs ----------------
    async def scan_inbox(
        self, account: "DouyinAccount", *, max_conversations: int = 15,
        include_recent_without_unread: bool = False, conversation_hint: str | None = None,
    ) -> List["ScannedMessage"]:
        if include_recent_without_unread:
            # 首轮历史补扫（Baseline），走 HTTP 路径
            logger.info(f"[frontier.ws] 执行首次历史补扫（Baseline）account={self._account_id}")
            return await self._inner.scan_inbox(
                account, max_conversations=max_conversations,
                include_recent_without_unread=include_recent_without_unread,
                conversation_hint=conversation_hint,
            )

        # 优先消费 WS 实时队列
        msgs: list[ScannedMessage] = []
        while not self._scanned_messages_queue.empty():
            msgs.append(self._scanned_messages_queue.get_nowait())

        if msgs:
            logger.info(f"[frontier.ws] 增量扫描消费 WS 实时消息 count={len(msgs)}")
            return msgs

        # WS 离线或定期兜底：走 HTTP 增量，避免纯 WS 解码失败时完全收不到消息
        from django.conf import settings

        ws_ok = self._client is not None and self._client.connected
        fallback_iv = float(getattr(settings, 'DOUYIN_WS_HTTP_FALLBACK_INTERVAL', 25) or 25)
        now = time.monotonic()
        if not ws_ok or (now - self._last_http_fallback_at >= fallback_iv):
            self._last_http_fallback_at = now
            reason = 'ws_offline' if not ws_ok else 'periodic_fallback'
            logger.debug(
                f"[frontier.ws] HTTP 兜底扫描 account={self._account_id} reason={reason}"
            )
            return await self._inner.scan_inbox(
                account,
                max_conversations=max_conversations,
                include_recent_without_unread=False,
                conversation_hint=conversation_hint,
            )
        return []

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

    def _on_inbound(self, m: Any) -> None:
        """WS 线程接收消息的回调入口。"""
        asyncio.create_task(self._process_message(m))

    def _infer_message_direction(self, m: Any) -> str:
        """判定 WS 帧方向：优先 sec_uid，再用 conversation_id 中的 numeric uid。"""
        sender_sec = str(getattr(m, "sender_sec_uid", "") or "").strip()
        sender_uid = int(getattr(m, "sender_uid", 0) or 0)
        if self._account_sec_uid and sender_sec and sender_sec == self._account_sec_uid:
            if sender_uid > 0:
                self._self_uid = sender_uid
            return "out"
        if self._self_uid > 0 and sender_uid == self._self_uid:
            return "out"
        conv = str(getattr(m, "conversation_id", "") or "").strip()
        parts = conv.split(":")
        if len(parts) == 4 and sender_uid > 0:
            try:
                uid_a = int(parts[2])
                uid_b = int(parts[3])
            except ValueError:
                uid_a = uid_b = 0
            if self._self_uid > 0 and sender_uid == self._self_uid:
                return "out"
            if self._self_uid > 0 and sender_uid in (uid_a, uid_b) and sender_uid != self._self_uid:
                return "in"
        return "in"

    async def _process_message(self, m: Any) -> None:
        """处理消息落库，若为新消息且是入向，放入 scanned 队列并唤醒 worker。"""
        from datetime import datetime, timezone
        from core.douyin.runtime.message_store import _upsert_conversation_and_message

        # 0. 过滤非用户/系统消息
        if not m.conversation_id or m.server_message_id <= 0:
            return
        if m.msg_type != 1 or not m.text:
            logger.debug(f"[frontier.ws] 跳过非用户/系统消息 server_msg_id={m.server_message_id} msg_type={m.msg_type}")
            return

        # 1. 确定方向
        direction = self._infer_message_direction(m)
        if direction == "out":
            logger.info(
                f"[frontier.ws] 跳过己方出站消息 account={self._account_id} "
                f"sender_uid={getattr(m, 'sender_uid', 0)} text={(m.text or '')[:40]!r}"
            )
            return

        # 2. 获取接收时间
        received_at = (
            datetime.fromtimestamp(m.create_time_us / 1_000_000, tz=timezone.utc)
            if m.create_time_us > 1577836800000000
            else datetime.now(tz=timezone.utc)
        )

        # 3. 解析或缓存用户信息
        peer_nickname = None
        peer_avatar = None
        if direction == "in":
            peer_nickname, peer_avatar = await self._get_existing_peer_info(m.sender_sec_uid)
            if not peer_nickname:
                try:
                    account_orm = await self._get_account_orm()
                    if account_orm:
                        details = await self._inner._resolve_user_details_by_sec_uids(
                            account_orm, [m.sender_sec_uid]
                        )
                        if m.sender_sec_uid in details:
                            peer_nickname = details[m.sender_sec_uid].get("nickname")
                            peer_avatar = details[m.sender_sec_uid].get("avatar")
                except Exception as ex:
                    logger.warning(
                        f"[frontier.ws] 无法在线解析用户详情 sec_uid={m.sender_sec_uid}: {ex}"
                    )

        # 4. 落库
        try:
            res = await _upsert_conversation_and_message(
                account_id=self._account_id,
                peer_sec_uid=m.sender_sec_uid,
                peer_nickname=peer_nickname,
                peer_avatar=peer_avatar,
                text=m.text,
                received_at=received_at,
                raw={
                    "source": "frontier_ws.message",
                    "conversation_id": m.conversation_id,
                    "msg_type": m.msg_type,
                    "server_message_id": m.server_message_id,
                    "client_message_id": m.client_message_id,
                    "sender_uid": m.sender_uid,
                    "content_json": m.content_json,
                },
                external_msg_id=f"srv_{m.server_message_id}",
                platform_conversation_id=m.conversation_id,
                direction=direction,
            )
        except Exception as ex:
            logger.exception(f"[frontier.ws] 消息落库异常 server_msg_id={m.server_message_id}: {ex}")
            return

        # 5. 新入向消息入库后唤醒 worker；重复入库但尚未处理的消息由 worker 补跑队列
        if res is not None and direction == "in":
            conv_id, msg_id = res
            scanned = ScannedMessage(
                message_id=msg_id,
                conversation_id=conv_id,
                peer_sec_uid=m.sender_sec_uid,
                peer_nickname=peer_nickname,
                text=m.text,
                received_at=received_at.isoformat(),
                raw={"conversation_id": m.conversation_id},
            )
            try:
                self._scanned_messages_queue.put_nowait(scanned)

                # 发送 InboundEvent 唤醒 worker 扫描
                evt = InboundEvent(
                    account_id=self._account_id or "",
                    source="ws",
                    ts=time.time(),
                    conversation_hint=m.conversation_id,
                    raw={"text_preview": (m.text or "")[:120]},
                )
                with suppress(asyncio.QueueFull):
                    self._queue.put_nowait(evt)

                self._signal.set()
                logger.info(
                    f"[frontier.ws] 收到新私信入库并唤醒: peer={peer_nickname or m.sender_sec_uid[:12]} "
                    f"text={m.text!r}"
                )
            except Exception as ex:
                logger.error(f"[frontier.ws] 队列操作异常: {ex}")

    @sync_to_async
    def _get_existing_peer_info(self, peer_sec_uid: str) -> tuple[Optional[str], Optional[str]]:
        from core.douyin.douyin_conversation_model import DouyinConversation
        conv = DouyinConversation.objects.filter(
            account_id=self._account_id,
            peer_sec_uid=peer_sec_uid
        ).first()
        if conv:
            return conv.peer_nickname or None, conv.peer_avatar or None
        return None, None

    @sync_to_async
    def _get_account_orm(self):
        from core.douyin.douyin_account_model import DouyinAccount
        return DouyinAccount.objects.filter(id=self._account_id).first()


# ──────────────────────── helpers ────────────────────────


async def _log_ws_event(
    account_id: Optional[str],
    event_type: str,
    level: str,
    title: str,
    detail: str = "",
    payload: Optional[dict] = None,
) -> None:
    """写一条 WS 运行事件到 DouyinEvent（带结构化 payload）。失败不影响 WS 主流程。"""

    @sync_to_async
    def _write() -> None:
        try:
            from django.utils import timezone

            from core.douyin.douyin_event_model import DouyinEvent

            DouyinEvent.objects.create(
                account_id=account_id,
                event_type=event_type,
                level=level,
                title=title,
                detail=detail,
                payload=payload or {},
                occurred_at=timezone.now(),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[frontier.ws] 写 DouyinEvent 失败: {e}")

    with suppress(Exception):
        await _write()


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
