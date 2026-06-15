#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: transport/http_protocol.py
@Desc: HttpProtocolTransport —— 纯 HTTP 协议 transport（无浏览器）

设计：
  - 每账号一份独立实例；持有自己的签名后端（JsSignProvider / LocalSignProvider），
    后端内部为该账号创建专属 httpx 客户端（带该账号 cookie/代理/UA，每请求注入 cookie，
    非 client 级 cookie jar），账号之间不共享连接，杜绝 cookie 串号
  - scan_inbox / send_reply / send_text 三个 verb 全部走 HTTP 协议路径
  - 协议路径不可用 / 签名机不健康时直接抛错（STRICT 模式，无任何浏览器兜底）

发送编码走 protobuf（im_send_pb2），入向扫描解析 imapi JSON 落库。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional

from asgiref.sync import sync_to_async

from core.douyin.runtime.reply_helpers import (
    _build_segments,
    _record_auto_outbound_message,
    _write_reply_log,
    write_manual_out_message,
)
from core.douyin.runtime.send_template_cache import load_cached_send_template
from core.douyin.runtime.transport.base import AccountTransport
from core.douyin.runtime.transport.wire.codec import (
    get_first_bytes,
    get_first_str,
    iter_fields,
)
from core.douyin.runtime.transport.sign_types import (
    LoginExpiredError,
    SignedResponse,
    SignerUnavailable,
)
from core.douyin.runtime.transport.wire import (
    SendMessageResult,
    decode_get_by_conversation_response,
    decode_get_by_user_response,
    decode_list_conversations_response,
    decode_send_message_response,
    encode_get_by_conversation_request,
    encode_get_by_user_request,
    encode_list_conversations_request,
    encode_send_message_request,
    encode_send_message_request_from_template,
)
from core.douyin.runtime.transport.wire.codec import get_first_int

if TYPE_CHECKING:
    from core.douyin.douyin_account_model import DouyinAccount
    from core.douyin.douyin_rule_model import DouyinRule
    from core.douyin.runtime.message_store import ScannedMessage

logger = logging.getLogger(__name__)


@sync_to_async
def _resolve_conversation_transport_keys(
    account_id: str,
    conversation_id: str,
) -> tuple[Optional[str], str]:
    """
    把上层传入的 conversation_id 统一解析成：

    - db_conversation_id: 本地 DouyinConversation.id（用于落 DouyinMessage / ReplyLog）
    - platform_conversation_id: 抖音平台侧 "0:1:..."（用于 HTTP protobuf）

    兼容两种输入：
    - 传本地会话 ID：从 DB 反查 platform_conversation_id
    - 直接传平台会话 ID：允许发协议请求；若 DB 中已有映射则顺便补回本地 ID
    """
    from core.douyin.douyin_conversation_model import DouyinConversation

    conv_token = str(conversation_id or "").strip()
    if not conv_token:
        raise ValueError("conversation_id 不能为空")

    if ":" in conv_token:
        conv = DouyinConversation.objects.filter(
            account_id=account_id,
            platform_conversation_id=conv_token,
        ).first()
        return (str(conv.id) if conv else None, conv_token)

    conv = DouyinConversation.objects.filter(id=conv_token, account_id=account_id).first()
    if conv is None:
        raise ValueError(f"会话不存在: {conv_token}")
    platform_conv_id = str(conv.platform_conversation_id or "").strip()
    if not platform_conv_id:
        raise ValueError(
            f"会话缺少平台 conversation_id，无法走 HTTP 协议发送: {conv_token}"
        )
    return str(conv.id), platform_conv_id


@sync_to_async
def _load_latest_successful_send_template(
    account_id: str,
    conversation_id: str,
) -> Optional[bytes]:
    from core.douyin.runtime.storage import _data_dir

    cached = load_cached_send_template(account_id, conversation_id)
    if cached:
        return cached

    account_dir = _data_dir() / "sniff" / f"account_{account_id}"
    if not account_dir.exists():
        return None

    def _decode_send_business(raw: bytes) -> tuple[int, str]:
        envelope: dict[int, list] = {}
        for fnum, _wt, val in iter_fields(raw):
            envelope.setdefault(fnum, []).append(val)
        inner = get_first_bytes(envelope, 6)
        if not inner:
            return 0, ""
        for fnum, _wt, val in iter_fields(inner):
            if fnum != 100 or not isinstance(val, (bytes, bytearray)):
                continue
            inner2: dict[int, list] = {}
            for f2, _wt2, v2 in iter_fields(bytes(val)):
                inner2.setdefault(f2, []).append(v2)
            biz_raw = get_first_bytes(inner2, 6)
            if not biz_raw:
                return 0, ""
            try:
                obj = json.loads(biz_raw.decode("utf-8", "ignore"))
            except Exception:
                return 0, ""
            if not isinstance(obj, dict):
                return 0, ""
            status_code = int(obj.get("status_code") or 0)
            status_msg = obj.get("status_msg") or {}
            tips = ""
            if isinstance(status_msg, dict):
                tips = ((status_msg.get("msg_content") or {}).get("tips") or "").strip()
            elif isinstance(status_msg, str):
                tips = status_msg.strip()
            return status_code, tips
        return 0, ""

    def _decode_send_conversation(raw: bytes) -> str:
        envelope: dict[int, list] = {}
        for fnum, _wt, val in iter_fields(raw):
            envelope.setdefault(fnum, []).append(val)
        inner = get_first_bytes(envelope, 8)
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

    sessions = sorted(account_dir.glob("session_*.jsonl"), reverse=True)
    fallback_template: Optional[bytes] = None
    for session in sessions:
        pending_requests: dict[str, tuple[bytes, str]] = {}
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
                        and int(row.get("post_len") or 0) >= 700
                    ):
                        post_b64 = row.get("post_b64")
                        if not post_b64:
                            continue
                        try:
                            req_raw = base64.b64decode(post_b64)
                        except Exception:
                            continue
                        conv = _decode_send_conversation(req_raw)
                        pending_requests[str(row.get("ts"))] = (req_raw, conv)
                        continue
                    if (
                        row.get("type") == "http_response"
                        and row.get("url") == "https://imapi.douyin.com/v1/message/send"
                        and row.get("method") == "POST"
                        and int(row.get("status") or 0) == 200
                        and bool(row.get("ok")) is True
                    ):
                        body_b64 = row.get("body_b64")
                        if not body_b64:
                            continue
                        try:
                            resp_raw = base64.b64decode(body_b64)
                        except Exception:
                            continue
                        biz_code, tips = _decode_send_business(resp_raw)
                        req_info = pending_requests.get(str(row.get("ts")))
                        if req_info is None and pending_requests:
                            req_info = next(reversed(pending_requests.values()))
                        if req_info is None:
                            continue
                        req_raw, req_conv = req_info
                        # 只把“浏览器端真实可发”的模板当作成功模板。
                        if biz_code == 8101 and not tips:
                            if req_conv == conversation_id:
                                return req_raw
                            if fallback_template is None:
                                fallback_template = req_raw
        except OSError:
            continue
    return fallback_template


_ENDPOINTS: dict[str, dict[str, str]] = {
    "send_message": {
        "method": "POST",
        "url": "https://imapi.douyin.com/v1/message/send",
        "content_type": "application/x-protobuf",
    },
    "get_by_conversation": {
        "method": "POST",
        "url": "https://imapi.douyin.com/v1/message/get_by_conversation",
        "content_type": "application/x-protobuf",
    },
    "list_conversations": {
        "method": "POST",
        "url": "https://imapi.douyin.com/v1/stranger/get_conversation_list",
        "content_type": "application/x-protobuf",
    },
    "get_by_user": {
        # web 端真正的"扫描收件箱"入口：跨 tab 增量拉所有新 message
        # （朋友/已关注/陌生人/全部），比 stranger 接口覆盖面大得多
        "method": "POST",
        "url": "https://imapi.douyin.com/v1/message/get_by_user",
        "content_type": "application/x-protobuf",
    },
    "user_detail": {
        # 批量补 sender_uid → 昵称/头像/sec_uid（JSON 接口，不是 protobuf）
        # 用于 scan 之后给 _upsert 喂 peer_nickname / peer_avatar，避免前端
        # 列表里只看到 hash sec_uid
        "method": "POST",
        "url": "https://creator.douyin.com/aweme/v1/creator/im/user_detail/",
        "content_type": "application/json",
    },
}


# 抖音 IM HTTP 接口共用 headers
# 与抓包对齐：accept 必须显式声明 protobuf，否则部分 imapi 端会返回 JSON 错误页
# （让我们误以为是 protobuf decode 失败 → fallback，难定位）；origin 是抖音风控
# 重点参考字段，缺失会被部分接口直接 451。
_BASE_IM_HEADERS: dict[str, str] = {
    "content-type": "application/x-protobuf",
    "accept": "application/x-protobuf",
    "origin": "https://creator.douyin.com",
    "referer": "https://creator.douyin.com/",
}

# creator.douyin.com 域下的 JSON 业务接口（user_detail 等）共用 headers
_BASE_CREATOR_JSON_HEADERS: dict[str, str] = {
    "content-type": "application/json",
    "accept": "application/json, text/plain, */*",
    "origin": "https://creator.douyin.com",
    "referer": "https://creator.douyin.com/",
}

# bd-ticket 取/续票端点（Proxyman 实测）：GET creator.douyin.com/.../im/user_token/v2/
# 请求需带 certificate=base64(CSR PEM) 与下方 creator 专属公共参数（device_platform=web、
# aid=2906、app_name=aweme_creator_platform），响应返回 token / ts_sign / sdk_cert（status_code=0）。
# 与默认 webapp/aid=6383 公共参数集不同，故单列。
CREATOR_USER_TOKEN_URL = "https://creator.douyin.com/aweme/v1/creator/im/user_token/v2/"
_CREATOR_MEDIA_USER_INFO_URL = (
    "https://creator.douyin.com/web/api/media/user/info/"
)
_CREATOR_TOKEN_REFERER = "https://creator.douyin.com/creator-micro/data/following/chat"


def creator_token_base_params(user_agent: str) -> str:
    """构造 user_token/v2 的 creator 专属公共参数串（不含 msToken / a_bogus / certificate）。

    严格对照 Proxyman 抓到的浏览器真实请求字段；browser_version 取 UA 去掉 "Mozilla/" 前缀。
    """
    from urllib.parse import quote

    ua = user_agent or (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    bver = ua.split("Mozilla/", 1)[-1]  # 抓包里 browser_name=Mozilla、browser_version=UA 去前缀
    is_mac = "Macintosh" in ua
    return "&".join([
        "aid=2906",
        "app_name=aweme_creator_platform",
        "device_platform=web",
        f"referer={quote(_CREATOR_TOKEN_REFERER, safe='')}",
        f"user_agent={quote(ua, safe='')}",
        "cookie_enabled=true",
        "screen_width=1920",
        "screen_height=1080",
        "browser_language=zh-CN",
        f"browser_platform={'MacIntel' if is_mac else 'Win32'}",
        "browser_name=Mozilla",
        f"browser_version={quote(bver, safe='')}",
        "browser_online=true",
        f"timezone_name={quote('Asia/Shanghai', safe='')}",
    ])


def _build_default_sign_provider():
    """
    按 DOUYIN_SIGN_BACKEND 选择签名后端（脱浏览器，默认 js）：

      - 'js'（默认）: JsSignProvider —— PyExecJS 执行 vendored dy_ab.js，
                      a_bogus + bd-ticket-guard 齐全（私信可收可发，推荐）
      - 'local'     : LocalSignProvider —— 纯 Python 算 a_bogus + msToken，
                      但缺 bd-ticket-guard，私信发送大概率签不出

    历史的 'browser' 后端（浏览器内 fetch 注入签名）已随浏览器子系统一并移除。
    """
    backend = "js"
    try:
        from django.conf import settings as _s
        backend = str(getattr(_s, "DOUYIN_SIGN_BACKEND", "js") or "js").lower()
    except Exception:  # noqa: BLE001
        backend = "js"

    if backend == "local":
        from core.douyin.runtime.transport.local_sign_provider import LocalSignProvider
        logger.info("[transport.http] 签名后端 = local（LocalSignProvider，无浏览器）")
        return LocalSignProvider()

    if backend not in ("js", ""):
        logger.warning(
            f"[transport.http] 未知/已废弃签名后端 {backend!r}（browser 已移除），回退 js"
        )
    from core.douyin.runtime.transport.js_sign_provider import JsSignProvider
    logger.info(
        "[transport.http] 签名后端 = js（JsSignProvider：dy_ab.js + bd-ticket-guard，无浏览器）"
    )
    return JsSignProvider()


class HttpProtocolTransport(AccountTransport):
    """
    纯 HTTP 协议 transport：JS 签名（dy_ab.js）+ httpx 业务流量，无浏览器。

    特性：
      - 完全实现 AccountTransport 契约（worker 可以无缝替换）
      - 所有 verb 只走 HTTP 协议路径；失败直接抛错（无 DOM/浏览器兜底）
      - 失败 streak > N 时 signer 进入降级，verb 抛错由 worker 记错重试
    """

    name = "http_protocol"

    def __init__(
        self,
        *,
        sign_provider: Optional[Any] = None,
        max_signer_failures: int = 5,
        send_text_enabled: Optional[bool] = None,
        send_reply_enabled: Optional[bool] = None,
        scan_inbox_enabled: Optional[bool] = None,
    ) -> None:
        self._sign = sign_provider or _build_default_sign_provider()
        self._max_signer_failures = int(max_signer_failures)
        self._signer_failures = 0
        # 半开熔断窗口（monotonic 秒）：失败累计达阈值后进入降级，但只冷却 N 秒；冷却到期放
        # 一次试探，成功即自愈复位，避免「攒够 N 次失败后永久降级、Proxyman/网络恢复也得重启」。
        self._signer_degraded_until = 0.0
        try:
            from django.conf import settings as _s
            self._signer_degrade_cooldown_s = float(
                getattr(_s, "DOUYIN_SIGNER_DEGRADE_COOLDOWN_S", 60) or 60
            )
        except Exception:  # noqa: BLE001
            self._signer_degrade_cooldown_s = 60.0

        # verb 级开关 —— Phase 3.2b 逐个翻 True
        # 优先级：构造参数 > Django setting > 默认 False
        self._http_send_text_enabled = self._resolve_flag(
            send_text_enabled, "DOUYIN_HTTP_PROTOCOL_SEND_TEXT"
        )
        self._http_send_reply_enabled = self._resolve_flag(
            send_reply_enabled, "DOUYIN_HTTP_PROTOCOL_SEND_REPLY"
        )
        self._http_scan_inbox_enabled = self._resolve_flag(
            scan_inbox_enabled, "DOUYIN_HTTP_PROTOCOL_SCAN_INBOX"
        )
        # 严格扫描模式：scan_inbox 只走 HTTP，不降级到 BrowserTransport。
        self._http_scan_strict = self._resolve_flag(
            None, "DOUYIN_HTTP_PROTOCOL_SCAN_STRICT"
        )
        # dual-run 影子模式：SCAN_INBOX=false 但 SCAN_INBOX_DUAL_RUN=true 时
        # 主路径走 fallback browser，HTTP 解析作为旁路 dry_run 输出对账日志。
        # 不影响 worker 行为，纯观察。
        self._http_scan_inbox_dual_run = self._resolve_flag(
            None, "DOUYIN_HTTP_PROTOCOL_SCAN_INBOX_DUAL_RUN"
        )
        # 严格发送模式：send_text / send_reply 只走 HTTP，不降级到 BrowserTransport。
        self._http_send_strict = self._resolve_flag(
            None, "DOUYIN_HTTP_PROTOCOL_SEND_STRICT"
        )
        # scan_inbox 用 get_by_user 增量拉时的 cursor。
        # key=account_id, value=last seen create_time_us（微秒时间戳）
        # 0 = 首次或重置；服务端会返回最新的一批
        # 注：这是进程内状态，worker 重启会丢，重启后第一轮等同于 cursor=0。
        # 真要持久化，可以接 redis 或 DouyinAccount.last_scan_cursor_us 字段。
        self._scan_cursor_us: dict[str, int] = {}
        # 信息隔离防线：start() 绑定该实例服务的账号 id；之后所有 verb 校验传入账号
        # 必须与绑定账号一致，杜绝误用他账号 transport 造成消息穿插。
        self._bound_account_id: Optional[str] = None

    def _assert_account_bound(self, account: "DouyinAccount", verb: str) -> None:
        """校验传入账号与本 transport 绑定账号一致（信息隔离硬约束）。"""
        incoming = str(getattr(account, "id", "") or "")
        if self._bound_account_id and incoming and incoming != self._bound_account_id:
            raise RuntimeError(
                f"transport 账号绑定不一致（疑似账号穿插）：bound={self._bound_account_id} "
                f"incoming={incoming} verb={verb}"
            )

    @staticmethod
    def _resolve_flag(explicit: Optional[bool], setting_name: str) -> bool:
        """构造参数 > Django setting > False"""
        if explicit is not None:
            return bool(explicit)
        try:
            from django.conf import settings as _s
            return bool(getattr(_s, setting_name, False))
        except Exception:  # noqa: BLE001
            return False

    # ---------------- 生命周期 ----------------
    async def start(self, account: "DouyinAccount") -> None:
        self._bound_account_id = str(getattr(account, "id", "") or "") or None
        try:
            await self._sign.start(account)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[transport.http] SignProvider.start 异常 account={account.id} err={e}"
            )
        logger.info(
            f"[transport.http] HttpProtocolTransport 就绪 account={account.id} "
            f"signer_ready={self._sign.is_ready} "
            f"flags=(send_text={self._http_send_text_enabled},"
            f"send_reply={self._http_send_reply_enabled},"
            f"scan={self._http_scan_inbox_enabled},"
            f"scan_strict={self._http_scan_strict},"
            f"scan_dual_run={self._http_scan_inbox_dual_run},"
            f"send_strict={self._http_send_strict})"
        )

    async def stop(self, account: "DouyinAccount") -> None:
        try:
            await self._sign.stop(account)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[transport.http] stop 部分失败 account={account.id} err={type(e).__name__}"
            )

    # ---------------- 主 verbs ----------------
    async def scan_inbox(
        self,
        account: "DouyinAccount",
        *,
        max_conversations: int = 15,
        include_recent_without_unread: bool = False,
        conversation_hint: Optional[str] = None,
    ) -> List["ScannedMessage"]:
        self._assert_account_bound(account, "scan_inbox")
        # 模式 1：HTTP 主路径开（SCAN_INBOX=true）→ 协议路径，落库
        if (
            self._http_scan_inbox_enabled
            and self._http_scan_strict
            and not self._signer_healthy()
        ):
            raise RuntimeError(
                "HTTP scan_inbox 严格模式失败（signer 未就绪或失败次数超阈值）"
            )
        if self._http_scan_inbox_enabled and self._signer_healthy():
            try:
                impl_kwargs = {
                    "max_conversations": max_conversations,
                    "include_recent_without_unread": include_recent_without_unread,
                    "dry_run": False,
                }
                if conversation_hint:
                    impl_kwargs["conversation_hint"] = conversation_hint
                result = await self._impl_scan_inbox_via_http(
                    account,
                    **impl_kwargs,
                )
                self._on_signer_success()  # 成功即复位失败计数 / 解除降级，连接恢复自愈
                return result
            except _NotImplementedYet:
                pass
            except SignerUnavailable as e:
                self._on_signer_failure(f"scan_inbox: {e}")
                if self._http_scan_strict:
                    raise RuntimeError(
                        f"HTTP scan_inbox 严格模式失败（signer 不可用）: {e}"
                    ) from e
            except LoginExpiredError:
                # 登录失效：保留类型透传给 worker 打回账号
                raise
            except Exception as e:  # noqa: BLE001
                if self._http_scan_strict:
                    raise RuntimeError(
                        f"HTTP scan_inbox 严格模式失败: {type(e).__name__}: {e}"
                    ) from e
                logger.warning(
                    f"[transport.http] scan_inbox 协议路径异常，fallback browser "
                    f"account={account.id} err={type(e).__name__}: {e}"
                )

        # 无浏览器兜底：到这里说明 scan 未启用或 signer 不可用，直接抛错让 worker 记录并重试。
        raise RuntimeError(
            f"HTTP scan_inbox 不可用（enabled={self._http_scan_inbox_enabled} "
            f"signer_ready={self._sign.is_ready} failures={self._signer_failures}）"
        )

    async def send_reply(
        self,
        account: "DouyinAccount",
        page: Any,
        *,
        conversation_id: str,
        trigger_message_id: str,
        rule: "DouyinRule",
        peer_nickname: str = "",
    ) -> str:
        self._assert_account_bound(account, "send_reply")
        if self._http_send_reply_enabled and self._http_send_strict and not self._signer_healthy():
            raise RuntimeError(
                "HTTP send_reply 严格模式失败（signer 未就绪或失败次数超阈值）"
            )
        if self._http_send_reply_enabled and self._signer_healthy():
            try:
                _r = await self._impl_send_reply_via_http(
                    account,
                    conversation_id=conversation_id,
                    trigger_message_id=trigger_message_id,
                    rule=rule,
                    peer_nickname=peer_nickname,
                )
                self._on_signer_success()
                return _r
            except _NotImplementedYet:
                pass
            except SignerUnavailable as e:
                self._on_signer_failure(f"send_reply: {e}")
                if self._http_send_strict:
                    raise RuntimeError(
                        f"HTTP send_reply 严格模式失败（signer 不可用）: {e}"
                    ) from e
            except LoginExpiredError:
                # 登录失效：保留类型透传给 worker 打回账号，不要包成通用 RuntimeError
                raise
            except ValueError:
                raise
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(
                    f"HTTP send_reply 失败: {type(e).__name__}: {e}"
                ) from e
        # 无浏览器兜底：send_reply 未启用或 signer 不可用，直接抛错。
        raise RuntimeError(
            f"HTTP send_reply 不可用（enabled={self._http_send_reply_enabled} "
            f"signer_ready={self._sign.is_ready}）"
        )

    async def send_text(
        self,
        account: "DouyinAccount",
        page: Any,
        *,
        conversation_id: str,
        text: str,
    ) -> str:
        self._assert_account_bound(account, "send_text")
        if self._http_send_text_enabled and self._http_send_strict and not self._signer_healthy():
            raise RuntimeError(
                "HTTP send_text 严格模式失败（signer 未就绪或失败次数超阈值）"
            )
        if self._http_send_text_enabled and self._signer_healthy():
            try:
                _r = await self._impl_send_text_via_http(
                    account,
                    conversation_id=conversation_id,
                    text=text,
                )
                self._on_signer_success()
                return _r
            except _NotImplementedYet:
                pass
            except SignerUnavailable as e:
                self._on_signer_failure(f"send_text: {e}")
                if self._http_send_strict:
                    raise RuntimeError(
                        f"HTTP send_text 严格模式失败（signer 不可用）: {e}"
                    ) from e
            except LoginExpiredError:
                # 登录失效：保留类型透传给 worker 打回账号，不要包成通用 RuntimeError
                raise
            except ValueError:
                # 调用方传错参数（空 text / 空 conv），透传
                raise
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(
                    f"HTTP send_text 失败: {type(e).__name__}: {e}"
                ) from e
        # 无浏览器兜底：send_text 未启用或 signer 不可用，直接抛错。
        raise RuntimeError(
            f"HTTP send_text 不可用（enabled={self._http_send_text_enabled} "
            f"signer_ready={self._sign.is_ready}）"
        )

    async def probe_credential(self, account: "DouyinAccount"):
        """轻量只读探活：判定该账号 cookie 是否仍有效（不落库、不动 scan cursor）。

        用 get_by_user(limit=1, cursor=now) 只验证登录态。返回 health.ProbeResult，
        由 scheduler 探活任务据此刷新 credential_state / 打回失效账号。
        """
        from core.douyin.runtime.health import (
            ProbeResult,
            PROBE_INCONCLUSIVE,
            classify_signed_response,
        )

        self._assert_account_bound(account, "probe")
        if not self._signer_healthy():
            return ProbeResult(status=PROBE_INCONCLUSIVE, detail="signer 未就绪")

        cursor_us = int(time.time() * 1_000_000)
        body, _seq_id = encode_get_by_user_request(cursor_us=cursor_us, limit=1)
        try:
            resp: SignedResponse = await self._sign.signed_fetch(
                method="POST",
                url=_ENDPOINTS["get_by_user"]["url"],
                body=body,
                headers=_BASE_IM_HEADERS,
            )
        except SignerUnavailable as e:
            return ProbeResult(status=PROBE_INCONCLUSIVE, detail=f"signer 不可用: {e}")
        except Exception as e:  # noqa: BLE001
            return ProbeResult(status=PROBE_INCONCLUSIVE, detail=f"网络异常: {type(e).__name__}: {e}")

        proto_status: Optional[int] = None
        proto_msg: Optional[str] = None
        if resp.ok and resp.content:
            try:
                decoded = decode_get_by_user_response(resp.content)
                proto_status = decoded.status_code
                proto_msg = decoded.status_msg
            except Exception:  # noqa: BLE001
                proto_status = None

        status = classify_signed_response(resp.status, proto_status, proto_msg)
        detail = "" if status == "valid" else (resp.text or "")[:200]
        return ProbeResult(
            status=status,
            http_status=resp.status,
            proto_status_code=proto_status,
            detail=detail,
        )

    async def _fetch_self_profile_via_creator_media(
        self,
        account: "DouyinAccount",
    ) -> Optional[dict]:
        """通过 creator 创作者中心接口拉取当前登录账号资料（GET，实测比 user_detail POST 更稳）。"""
        account_id = str(account.id)
        ua = getattr(self._sign, "_user_agent", "") or ""
        try:
            resp: SignedResponse = await self._sign.signed_fetch(
                method="GET",
                url=_CREATOR_MEDIA_USER_INFO_URL,
                headers=_BASE_CREATOR_JSON_HEADERS,
                # 与 user_detail / user_token 一致：creator 域用 aid=2906，勿用 webapp/6383
                base_params=creator_token_base_params(ua),
                timeout_ms=10_000,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[transport.http] media/user/info 请求失败 "
                f"account={account_id} err={type(e).__name__}: {e}"
            )
            return None

        if not resp.ok:
            logger.warning(
                f"[transport.http] media/user/info HTTP 非 2xx "
                f"account={account_id} status={resp.status}"
            )
            return None

        try:
            payload = json.loads(resp.text or resp.content.decode("utf-8") or "{}")
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[transport.http] media/user/info JSON 解析失败 "
                f"account={account_id} err={type(e).__name__}: {e}"
            )
            return None

        if payload.get("status_code", 0) != 0:
            logger.warning(
                f"[transport.http] media/user/info 业务错误 "
                f"account={account_id} status_code={payload.get('status_code')} "
                f"msg={payload.get('status_msg')!r}"
            )
            return None

        user = payload.get("user") or {}
        nickname = str(user.get("nickname") or "").strip()
        if not nickname:
            return None

        avatar = ""
        for key in ("avatar_thumb", "avatar_larger", "avatar_medium"):
            block = user.get(key) or {}
            urls = block.get("url_list") or []
            if urls:
                avatar = str(urls[0])
                break

        user_id = 0
        try:
            user_id = int(user.get("uid") or 0)
        except (TypeError, ValueError):
            user_id = 0

        return {
            "nickname": nickname,
            "avatar": avatar,
            "sec_uid": str(user.get("sec_uid") or "").strip(),
            "user_id": user_id,
        }

    async def _profile_from_self_uid(
        self,
        account: "DouyinAccount",
        self_uid: int,
        *,
        via: str,
    ) -> Optional[dict]:
        """用数字 user_id 调 user_detail 补全昵称/头像/sec_uid。"""
        if self_uid <= 0:
            return None
        details = await self._resolve_user_details(account, [self_uid])
        row = details.get(self_uid) if details else None
        if not row or not str(row.get("nickname") or "").strip():
            logger.warning(
                f"[transport.http] fetch_self_profile user_detail 返回空 "
                f"account={account.id} self_uid={self_uid} via={via}"
            )
            return None
        row = dict(row)
        row["user_id"] = self_uid
        logger.info(
            f"[transport.http] fetch_self_profile 成功(via {via}) "
            f"account={account.id} nickname={row.get('nickname')!r} "
            f"sec_uid={(row.get('sec_uid') or '')[:20]}..."
        )
        return row

    async def fetch_self_profile(self, account: "DouyinAccount") -> Optional[dict]:
        """获取当前登录账号自己的真实信息（昵称/头像/sec_uid/user_id）。

        1. **www.douyin.com**（对照 DouYin_Spider demo）：query/user → user/self → profile/other
        2. creator ``web/api/media/user/info``（创作者后台 cookie 时可用）
        3. ``list_conversations`` envelope.f13 → ``user_detail``
        4. cookie ``login_uid`` → ``user_detail``

        Returns:
            {"nickname": str, "avatar": str, "sec_uid": str, "user_id": int} 或 None（失败）
        """
        self._assert_account_bound(account, "fetch_self_profile")
        if not self._signer_healthy():
            logger.warning(
                f"[transport.http] fetch_self_profile signer 未就绪 account={account.id}"
            )
            return None

        # 优先主站链路（与 DouYin_Spider 一致；主站 cookie 在 creator 域常报「未登录」）
        try:
            from core.douyin.runtime.transport.douyin_web_profile import (
                fetch_self_profile_via_douyin_web,
            )

            cookies = await self._sign.get_cookies()
            ua = getattr(self._sign, "_user_agent", "") or ""
            proxy = getattr(self._sign, "_proxy_url", None)
            web_profile = await fetch_self_profile_via_douyin_web(
                cookies,
                ua,
                proxy_url=proxy,
                account_id=str(account.id),
            )
            if web_profile and str(web_profile.get("nickname") or "").strip():
                logger.info(
                    f"[transport.http] fetch_self_profile 成功(via douyin_web) "
                    f"account={account.id} nickname={web_profile.get('nickname')!r}"
                )
                return web_profile
            if web_profile:
                uid = int(web_profile.get("user_id") or 0)
                if uid > 0:
                    row = await self._profile_from_self_uid(
                        account, uid, via="douyin_web+user_detail",
                    )
                    if row:
                        if web_profile.get("sec_uid") and not row.get("sec_uid"):
                            row["sec_uid"] = web_profile["sec_uid"]
                        return row
                if str(web_profile.get("sec_uid") or "").strip():
                    return web_profile
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[transport.http] fetch_self_profile douyin_web 异常 "
                f"account={account.id} err={type(e).__name__}: {e}"
            )

        profile = await self._fetch_self_profile_via_creator_media(account)
        if profile:
            logger.info(
                f"[transport.http] fetch_self_profile 成功(via media/user/info) "
                f"account={account.id} nickname={profile.get('nickname')!r} "
                f"sec_uid={(profile.get('sec_uid') or '')[:20]}..."
            )
            return profile

        # 新号/空 inbox：list_conversations 的 envelope.f13 即 self_uid，不必先扫 get_by_user
        self_uid = await self._resolve_self_uid(account)
        if self_uid > 0:
            row = await self._profile_from_self_uid(
                account, self_uid, via="list_conversations+user_detail",
            )
            if row:
                return row

        from core.douyin.runtime.credential import extract_self_uid_from_cookies

        cookie_uid = extract_self_uid_from_cookies(await self._sign.get_cookies())
        if cookie_uid > 0 and cookie_uid != self_uid:
            row = await self._profile_from_self_uid(
                account, cookie_uid, via="cookie+user_detail",
            )
            if row:
                return row

        logger.warning(
            f"[transport.http] fetch_self_profile 全部路径失败 account={account.id}"
        )
        return None

    # ---------------- 内部：信号 / 健康 ----------------
    @staticmethod
    def _raise_if_login_expired(
        http_status: Optional[int],
        proto_status_code: Optional[int],
        *,
        context: str,
        proto_status_msg: Optional[str] = None,
    ) -> None:
        """若 HTTP/协议状态被判定为登录失效，抛 LoginExpiredError（worker 据此打回账号）。

        否则什么都不做，由调用方继续抛通用 RuntimeError 走重试/记错。
        """
        from core.douyin.runtime.health import classify_signed_response, PROBE_LOGIN_EXPIRED
        from core.douyin.runtime.transport.sign_types import LoginExpiredError

        if classify_signed_response(
            http_status, proto_status_code, proto_status_msg
        ) == PROBE_LOGIN_EXPIRED:
            raise LoginExpiredError(
                context,
                http_status=http_status,
                proto_status_code=proto_status_code,
            )

    def _signer_healthy(self) -> bool:
        if not self._sign.is_ready:
            return False
        if self._signer_failures < self._max_signer_failures:
            return True
        # 已达阈值：半开熔断——冷却到期放行一次试探（成功则 _on_signer_success 复位自愈）。
        return time.monotonic() >= self._signer_degraded_until

    def _on_signer_failure(self, reason: str) -> None:
        self._signer_failures += 1
        logger.warning(
            f"[transport.http] signer 失败计数 {self._signer_failures}/"
            f"{self._max_signer_failures} reason={reason}"
        )
        if self._signer_failures >= self._max_signer_failures:
            self._signer_degraded_until = time.monotonic() + self._signer_degrade_cooldown_s
            logger.error(
                "[transport.http] signer 失败次数达阈值，进入降级（半开熔断）："
                f"冷却 {self._signer_degrade_cooldown_s:.0f}s 后放一次试探，成功即自愈"
            )

    def _on_signer_success(self) -> None:
        """signer 调用成功：复位失败计数与降级窗口，连接恢复后无需重启即自愈。"""
        if self._signer_failures:
            logger.info(
                f"[transport.http] signer 调用成功，复位失败计数（此前 {self._signer_failures} 次）"
            )
        self._signer_failures = 0
        self._signer_degraded_until = 0.0

    # ---------------- 辅助：批量补 user_detail ----------------
    async def _resolve_user_details(
        self,
        account: "DouyinAccount",
        user_ids: list[int],
    ) -> dict[int, dict[str, str]]:
        """
        批量调 `creator/im/user_detail/` 把 sender_uid 补成昵称/头像/sec_uid。

        这条接口是 JSON（区别于 imapi.* 的 protobuf），走同一套 signed_fetch。
        失败 / 部分失败均不抛 —— 拿到多少回多少；调用方按 dict.get(uid) 兜底。

        Args:
            user_ids: sender_uid 列表（已去重最佳）

        Returns:
            {uid: {
                "nickname": str,
                "avatar": str,    # url
                "sec_uid": str,
                "short_id": str,
            }}
            响应里没有 / 接口失败的 uid 不会出现在 dict 里。
        """
        import json as _json

        valid_ids = sorted({int(u) for u in user_ids if u and int(u) > 0})
        if not valid_ids:
            return {}

        endpoint = _ENDPOINTS["user_detail"]
        body_json = _json.dumps({"user_ids": valid_ids}, ensure_ascii=False)
        account_id = str(account.id)
        ua = getattr(self._sign, "_user_agent", "") or ""
        try:
            resp: SignedResponse = await self._sign.signed_fetch(
                method=endpoint["method"],
                url=endpoint["url"],
                body=body_json,
                headers=_BASE_CREATOR_JSON_HEADERS,
                base_params=creator_token_base_params(ua),
            )
        except SignerUnavailable as e:
            # 不算 signer 重大事件 —— 主路径的 signed_fetch 仍是真正的健康标尺
            logger.warning(
                f"[transport.http] user_detail signer 不可用 account={account_id} "
                f"uids={len(valid_ids)} err={e}"
            )
            return {}
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[transport.http] user_detail 请求异常 account={account_id} "
                f"uids={len(valid_ids)} err={type(e).__name__}: {e}"
            )
            return {}

        if not resp.ok:
            logger.warning(
                f"[transport.http] user_detail HTTP 非 2xx account={account_id} "
                f"status={resp.status} preview={resp.text[:160]!r}"
            )
            return {}

        try:
            payload = _json.loads(resp.text or resp.content.decode("utf-8") or "{}")
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[transport.http] user_detail JSON 解析失败 account={account_id} "
                f"err={type(e).__name__}: {e} preview={resp.text[:160]!r}"
            )
            return {}

        # 抖音 creator 接口标准信封：{ status_code, status_msg, data: {...} }
        # user_detail 经验上 data 里挂 users 列表 / user_infos / 直接 user_id->info dict
        # 兼容三种 shape，宽松解析
        result: dict[int, dict[str, str]] = {}
        users_block = self._extract_user_detail_users(payload)
        for entry in users_block:
            try:
                uid = int(entry.get("user_id") or entry.get("uid") or 0)
            except (TypeError, ValueError):
                uid = 0
            if uid <= 0:
                continue
            result[uid] = {
                "nickname": str(entry.get("nickname") or entry.get("name") or "").strip(),
                "avatar": str(
                    entry.get("avatar")
                    or entry.get("avatar_url")
                    or (entry.get("avatar_thumb") or {}).get("url_list", [""])[0]
                    or ""
                ).strip(),
                "sec_uid": str(
                    entry.get("sec_uid")
                    or entry.get("secret_use_id")
                    or entry.get("sec_user_id")
                    or ""
                ).strip(),
                "short_id": str(entry.get("short_id") or "").strip(),
            }

        miss = len(valid_ids) - len(result)
        logger.info(
            f"[transport.http] user_detail account={account_id} "
            f"req={len(valid_ids)} got={len(result)} miss={miss}"
        )
        return result

    @staticmethod
    def _extract_user_detail_users(payload: dict) -> list[dict]:
        """从 creator user_detail 响应中宽松抽取 user 列表 —— 兼容 3 种已知 shape。

        - shape A：{"data": {"users": [{...}, ...]}}
        - shape B：{"data": {"user_infos": [{...}, ...]}}
        - shape C：{"data": {"<uid_str>": {...}}} —— 老 shape
        """
        if not isinstance(payload, dict):
            return []
        data = payload.get("data") or payload.get("user_data") or {}
        if not isinstance(data, dict):
            return []
        for key in ("users", "user_infos", "user_list"):
            block = data.get(key)
            if isinstance(block, list):
                return [x for x in block if isinstance(x, dict)]
        # shape C：data 本身就是 uid->info 字典
        out: list[dict] = []
        for k, v in data.items():
            if not isinstance(v, dict):
                continue
            if k.isdigit() and "user_id" not in v:
                v = {**v, "user_id": int(k)}
            out.append(v)
        return out

    @staticmethod
    def _looks_like_platform_conversation_id(token: Optional[str]) -> bool:
        raw = str(token or "").strip()
        if not raw:
            return False
        parts = raw.split(":")
        return len(parts) == 4 and all(part.isdigit() for part in parts)

    async def _resolve_self_uid(
        self,
        account: "DouyinAccount",
        *,
        envelope: dict[int, list] | None = None,
        messages: list[Any] | None = None,
    ) -> int:
        """从协议响应中解析当前登录账号的数字 user_id（self_uid）。

        优先级：
        1. envelope.f13（服务端直接返回，空收件箱也可用）
        2. 从消息的 conversation_id 推断（0:1:self_uid:peer_uid）
        3. list_conversations 的 envelope.f13（兜底）
        """
        self_uid = get_first_int(envelope or {}, 13)
        if self_uid > 0:
            return self_uid

        self_uid = self._infer_self_uid_from_conversation_ids(messages or [])
        if self_uid > 0:
            return self_uid

        endpoint = _ENDPOINTS["list_conversations"]
        body, _seq_id = encode_list_conversations_request()
        try:
            list_resp: SignedResponse = await self._sign.signed_fetch(
                method=endpoint["method"],
                url=endpoint["url"],
                body=body,
                headers=_BASE_IM_HEADERS,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[transport.http] _resolve_self_uid list_conversations 失败 "
                f"account={account.id} err={type(e).__name__}: {e}"
            )
            return 0

        if not list_resp.ok or not list_resp.content:
            return 0

        list_result = decode_list_conversations_response(list_resp.content)
        if list_result.status_code != 0:
            return 0
        return list_result.self_uid

    @staticmethod
    def _infer_self_uid_from_conversation_ids(messages: list[Any]) -> int:
        """Best-effort fallback for diagnostics only.

        get_by_user / get_by_conversation do not expose a stable participant
        list, and observed conversation IDs are not reliable enough to decide
        which side is the managed account. Use this value for logging, not for
        dropping messages.
        """
        counts: dict[int, int] = {}
        for m in messages:
            conv_id = str(getattr(m, "conversation_id", "") or "").strip()
            parts = conv_id.split(":")
            if len(parts) != 4:
                continue
            try:
                candidate = int(parts[2])
            except Exception:
                continue
            if candidate > 0:
                counts[candidate] = counts.get(candidate, 0) + 1
        if not counts:
            return 0
        return sorted(counts.items(), key=lambda item: (-item[1], -item[0]))[0][0]

    @sync_to_async
    def _resolve_scan_conversation_id(
        self,
        account_id: str,
        conversation_hint: Optional[str],
    ) -> Optional[str]:
        hint = str(conversation_hint or "").strip()
        if not hint:
            return None
        if self._looks_like_platform_conversation_id(hint):
            return hint[:128]

        from core.douyin.douyin_conversation_model import DouyinConversation

        base_qs = DouyinConversation.objects.filter(account_id=account_id)
        conv = (
            base_qs.filter(platform_conversation_id=hint).only("platform_conversation_id").first()
        )
        if conv and conv.platform_conversation_id:
            return str(conv.platform_conversation_id).strip()

        conv = (
            base_qs.filter(peer_sec_uid=hint)
            .only("platform_conversation_id")
            .order_by("-last_message_at", "-sys_create_datetime")
            .first()
        )
        if conv and conv.platform_conversation_id:
            return str(conv.platform_conversation_id).strip()

        conv = (
            base_qs.filter(peer_nickname=hint)
            .only("platform_conversation_id")
            .order_by("-last_message_at", "-sys_create_datetime")
            .first()
        )
        if conv and conv.platform_conversation_id:
            return str(conv.platform_conversation_id).strip()
        return None

    @sync_to_async
    def _load_latest_server_message_id_from_db(
        self,
        account_id: str,
        conversation_id: str,
    ) -> int:
        from core.douyin.douyin_conversation_model import DouyinConversation
        from core.douyin.douyin_message_model import DouyinMessage

        conv = DouyinConversation.objects.filter(
            account_id=account_id,
            platform_conversation_id=conversation_id,
        ).only("id").first()
        if conv is None:
            conv_id = (
                DouyinMessage.objects.filter(
                    conversation__account_id=account_id,
                    raw_payload__conversation_id=conversation_id,
                )
                .order_by("-received_at", "-sys_create_datetime")
                .values_list("conversation_id", flat=True)
                .first()
            )
            if conv_id:
                conv = DouyinConversation.objects.filter(
                    id=conv_id,
                    account_id=account_id,
                ).only("id").first()
                if conv is not None:
                    DouyinConversation.objects.filter(id=conv.id).update(
                        platform_conversation_id=conversation_id
                    )
            if conv is None:
                return 0

        latest_ids = (
            DouyinMessage.objects.filter(
                conversation_id=conv.id,
                external_msg_id__startswith="srv_",
            )
            .order_by("-received_at", "-sys_create_datetime")
            .values_list("external_msg_id", flat=True)[:20]
        )
        for ext_id in latest_ids:
            raw = str(ext_id or "")
            if not raw.startswith("srv_"):
                continue
            suffix = raw[4:]
            if suffix.isdigit():
                return int(suffix)
        return 0

    # ---------------- 协议路径实现 (Phase 3.2b) ----------------
    async def _post_send_message(
        self,
        account: "DouyinAccount",
        conversation_id: str,
        text: str,
        *,
        log_tag: str = "send",
    ) -> tuple[SendMessageResult, str]:
        """
        协议层"纯发送"：编码 → signed_fetch → 解响应 → 校验。
        **不落库**，由调用方决定写哪种 DouyinMessage / 是否写 DouyinReplyLog。

        Returns:
            (result, client_msg_id)：成功时返回；失败抛异常。

        Raises:
            ValueError: 入参非法（空 text / 空 conv）
            SignerUnavailable: 浏览器签名机不可用（上层 fallback）
            RuntimeError: HTTP 非 2xx / 解码失败 / 协议层 status_code != 0（上层 fallback）
        """
        normalized = (text or "").strip()
        if not normalized:
            raise ValueError(f"{log_tag} 不能发送空文本")
        if not conversation_id:
            raise ValueError(f"{log_tag} 需要 conversation_id")

        endpoint = _ENDPOINTS["send_message"]

        # 优先走 pb2 编码器（带完整 bd-ticket-guard 鉴权）——仅当签名后端持有 bd_ticket（js）。
        # 缺 private_key（如 browser 后端）时回退到旧的手写 codec / 模板路径。
        bd_ticket: dict = {}
        get_bd = getattr(self._sign, "get_bd_ticket", None)
        if callable(get_bd):
            try:
                bd_ticket = get_bd() or {}
            except Exception:  # noqa: BLE001
                bd_ticket = {}

        encoder = "legacy"
        if bd_ticket.get("private_key"):
            from core.douyin.runtime.transport.wire.im_send_pb2 import (
                encode_send_message_request_pb2,
            )

            s_v_web_id = ""
            try:
                cookies = await self._sign.get_cookies()
                s_v_web_id = (cookies or {}).get("s_v_web_id", "")
            except Exception:  # noqa: BLE001
                s_v_web_id = ""

            body, client_msg_id, seq_id = await sync_to_async(
                encode_send_message_request_pb2
            )(
                conversation_id=conversation_id,
                text=normalized,
                bd_ticket=bd_ticket,
                s_v_web_id=s_v_web_id,
            )
            encoder = "pb2"
            template_body = None
        else:
            template_body = await _load_latest_successful_send_template(
                str(account.id),
                conversation_id,
            )
            if template_body:
                body, client_msg_id, seq_id = encode_send_message_request_from_template(
                    template_body=template_body,
                    conversation_id=conversation_id,
                    text=normalized,
                )
            else:
                body, client_msg_id, seq_id = encode_send_message_request(
                    conversation_id=conversation_id,
                    text=normalized,
                )

        logger.info(
            f"[transport.http] {log_tag} → POST {endpoint['url']} "
            f"account={account.id} conv={conversation_id} "
            f"client_msg_id={client_msg_id} seq_id={seq_id} body_len={len(body)} "
            f"encoder={encoder} template={'Y' if template_body else 'N'}"
        )

        resp: SignedResponse = await self._sign.signed_fetch(
            method=endpoint["method"],
            url=endpoint["url"],
            body=body,
            headers=_BASE_IM_HEADERS,
            use_xhr=True,
        )

        if not resp.ok:
            logger.warning(
                f"[transport.http] {log_tag} HTTP 非 2xx account={account.id} "
                f"status={resp.status} text_preview={resp.text[:200]!r}"
            )
            self._raise_if_login_expired(
                resp.status, None,
                context=f"{log_tag} http status={resp.status}",
            )
            raise RuntimeError(f"{log_tag} http status={resp.status}")

        if not resp.content:
            logger.warning(
                f"[transport.http] {log_tag} 响应 body 为空 account={account.id}"
            )
            raise RuntimeError(f"{log_tag} 响应 body 为空")

        try:
            result = decode_send_message_response(resp.content)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[transport.http] {log_tag} 解码响应失败 account={account.id} "
                f"err={type(e).__name__}: {e} content_len={len(resp.content)}"
            )
            raise RuntimeError(f"{log_tag} decode failed: {e}") from e

        if result.status_code != 0:
            logger.warning(
                f"[transport.http] {log_tag} 协议层失败 account={account.id} "
                f"status_code={result.status_code} status_msg={result.status_msg!r} "
                f"server_msg_id={result.server_msg_id} client_msg_id={result.client_msg_id}"
            )
            self._raise_if_login_expired(
                resp.status, result.status_code,
                proto_status_msg=result.status_msg,
                context=(
                    f"{log_tag} protocol status={result.status_code} "
                    f"msg={result.status_msg!r}"
                ),
            )
            raise RuntimeError(
                f"{log_tag} protocol status={result.status_code} "
                f"msg={result.status_msg!r}"
            )

        if result.biz_status_code not in (0, 8101):
            logger.warning(
                f"[transport.http] {log_tag} 业务层失败 account={account.id} "
                f"biz_status_code={result.biz_status_code} "
                f"biz_status_text={result.biz_status_text!r} "
                f"biz_raw_check_code={result.biz_raw_check_code} "
                f"server_msg_id={result.server_msg_id} client_msg_id={result.client_msg_id}"
            )
            raise RuntimeError(
                f"{log_tag} business status={result.biz_status_code} "
                f"msg={result.biz_status_text or result.status_msg or 'unknown'}"
            )
        if result.biz_status_code == 8101 and result.biz_status_text:
            logger.warning(
                f"[transport.http] {log_tag} 业务层提示异常 account={account.id} "
                f"biz_status_code={result.biz_status_code} "
                f"biz_status_text={result.biz_status_text!r} "
                f"biz_raw_check_code={result.biz_raw_check_code} "
                f"server_msg_id={result.server_msg_id} client_msg_id={result.client_msg_id}"
            )
            raise RuntimeError(
                f"{log_tag} business status={result.biz_status_code} "
                f"msg={result.biz_status_text}"
            )

        if result.client_msg_id and result.client_msg_id != client_msg_id:
            logger.warning(
                f"[transport.http] {log_tag} client_msg_id 不一致 "
                f"sent={client_msg_id} echo={result.client_msg_id}"
            )

        logger.info(
            f"[transport.http] {log_tag} 成功 account={account.id} "
            f"server_msg_id={result.server_msg_id} client_msg_id={client_msg_id}"
        )
        return result, client_msg_id

    async def _impl_send_text_via_http(
        self,
        account: "DouyinAccount",
        *,
        conversation_id: str,
        text: str,
    ) -> str:
        """
        手动发送文本（创作者中心 IM 文本框入口）。
        失败时**不**自己落库—— fallback 会让 BrowserTransport.send_text 决定。
        """
        normalized = (text or "").strip()
        db_conversation_id, platform_conversation_id = await _resolve_conversation_transport_keys(
            str(account.id),
            conversation_id,
        )
        result, client_msg_id = await self._post_send_message(
            account,
            platform_conversation_id,
            normalized,
            log_tag="send_text",
        )
        if not db_conversation_id:
            logger.warning(
                f"[transport.http] send_text 未找到本地会话映射，仅协议发送成功 "
                f"account={account.id} platform_conv={platform_conversation_id}"
            )
            return result.client_msg_id or client_msg_id
        # 落 DouyinMessage(direction='out', external_msg_id=manual_out_*)
        msg_id = await write_manual_out_message(
            str(account.id), db_conversation_id, normalized
        )
        return msg_id

    async def _impl_send_reply_via_http(
        self,
        account: "DouyinAccount",
        *,
        conversation_id: str,
        trigger_message_id: str,
        rule: "DouyinRule",
        peer_nickname: str,
    ) -> str:
        """
        自动回复：复用 sender._build_segments / _record_auto_outbound_message /
        _write_reply_log，把"分段发送"由 DOM 改为 HTTP 协议。

        失败语义：
          - 任何段发送失败 → 立刻停止后续段、写 result='failed' 的 reply log，
            然后**抛 RuntimeError**让上层 fallback 走 BrowserTransport
          - 上层 catch 到异常后会调 BrowserTransport.send_reply，BrowserTransport
            会**重新跑一遍** _build_segments + _send_one + _write_reply_log。
            所以这里抛异常前**不**写 'failed' log，否则一次失败会落两条 log
          - "段间已发出"的部分：worker 的 _recent_outbound_texts 90s 去重 +
            DouyinMessage 的 get_or_create(external_msg_id) 会兜住重复
        """
        import asyncio
        import random
        from datetime import datetime

        account_id = str(account.id)
        rule_id = str(rule.id)
        t0 = datetime.utcnow().timestamp()
        db_conversation_id, platform_conversation_id = await _resolve_conversation_transport_keys(
            account_id,
            conversation_id,
        )
        if not db_conversation_id:
            raise ValueError(
                f"send_reply 需要本地会话 ID 映射才能记录 reply log: {conversation_id}"
            )

        segments = _build_segments(rule, peer_nickname)
        logger.info(
            f"[transport.http] send_reply 开始 account={account_id} peer={peer_nickname!r} "
            f"rule={rule.name!r} send_mode={getattr(rule, 'send_mode', '?')} "
            f"segments={len(segments)} conv={conversation_id} "
            f"platform_conv={platform_conversation_id}"
        )

        if not segments:
            logger.warning(
                f"[transport.http] send_reply 渲染结果为空，跳过 account={account_id} "
                f"rule={rule.name!r}"
            )
            return await _write_reply_log(
                account_id=account_id,
                conversation_id=db_conversation_id,
                trigger_message_id=trigger_message_id,
                rule_id=rule_id,
                text='',
                links=[],
                result='skipped',
                error_message='规则渲染结果为空',
            )

        sent_count = 0
        for i, seg in enumerate(segments):
            preview = seg[:40].replace('\n', ' ')
            logger.info(
                f"[transport.http] send_reply 发送段 {i + 1}/{len(segments)} "
                f"account={account_id} len={len(seg)} preview={preview!r}"
            )
            # 协议层发送；任何失败让 _post_send_message 抛 → 上层 fallback
            await self._post_send_message(
                account,
                platform_conversation_id,
                seg,
                log_tag=f"send_reply[{i + 1}]",
            )
            sent_count += 1
            # 每段发送成功后立刻记 outbound message（让 echo_blacklist 命中）
            try:
                await _record_auto_outbound_message(
                    account_id=account_id,
                    conversation_id=db_conversation_id,
                    text=seg,
                    rule_id=rule_id,
                )
            except Exception as e:  # noqa: BLE001
                # 落库失败不影响主流程；下一轮 scan_inbox 还有 _recent_outbound_texts 兜底
                logger.warning(
                    f"[transport.http] send_reply 落 outbound DouyinMessage 失败（不致命） "
                    f"account={account_id} conv={conversation_id} err={e}"
                )
            # 段间随机间隔，与 BrowserTransport 行为对齐
            if i < len(segments) - 1:
                gap = random.uniform(1.0, 3.0)
                logger.debug(f"[transport.http] send_reply 段间等待 {gap:.2f}s")
                await asyncio.sleep(gap)

        duration = int((datetime.utcnow().timestamp() - t0) * 1000)
        tpl = getattr(rule, 'template', None)
        links_payload = (tpl.links if tpl else getattr(rule, 'links', None)) or []

        log_id = await _write_reply_log(
            account_id=account_id,
            conversation_id=db_conversation_id,
            trigger_message_id=trigger_message_id,
            rule_id=rule_id,
            text=segments[0],
            links=links_payload,
            result='success',
            duration_ms=duration,
        )
        logger.info(
            f"[transport.http] send_reply 成功 account={account_id} peer={peer_nickname!r} "
            f"segments={sent_count}/{len(segments)} duration_ms={duration} reply_log={log_id}"
        )
        return log_id

    async def _impl_scan_inbox_via_http(
        self,
        account: "DouyinAccount",
        *,
        max_conversations: int,
        include_recent_without_unread: bool,
        conversation_hint: Optional[str] = None,
        dry_run: bool = False,
    ) -> List["ScannedMessage"]:
        """
        协议路径扫消息：

          1. signed_fetch list_conversations  → ListConversationsResult
          2. envelope.f13 给 self_uid，做 identity sanity check
             （account.sec_uid 字符串和 list 里 self participant 的 sec_uid 必须一致）
          3. 对每个 conversation 的 last_message:
             - sender_uid == self_uid → 自己发的，跳过
             - 在 _recent_outbound_texts/replies 90s 窗口内 → 回声，跳过
             - 否则用 server_message_id 做 stable external_msg_id 落库
                （`srv_<server_message_id>` 与 DOM 路径的 hash id 区分 namespace）

        相比 DOM 路径的优点：
          - 一次 HTTP（约 1KB 上行 / 几十 KB 下行）vs page.goto + 虚拟滚动
          - 不用切 IM 页签、不用扫 DOM、不用解析 React class names
          - 自带服务端时间戳和 server_message_id（幂等键比 hash 更稳）
          - 自带 participant.sec_uid（不再需要爬页面）

        Args:
            dry_run: True 时仅解析，不落库、不返回 ScannedMessage（返回空列表）；
                内部仍把"如果落库会落多少条"通过 [transport.http.dry_run] 日志输出。
                配合 dual-run 模式做协议对账。

        Raises:
            _NotImplementedYet: 这里不会抛；保留是为了让 SignerUnavailable 之类
                走 fallback 时签名感知。
            SignerUnavailable: 签名机器挂了，外层 fallback。
            RuntimeError: HTTP 失败 / 解码失败 / status_code 非 0，外层 fallback。
            （注意：get_by_user 协议下不再抛 AccountIdentityMismatch；
             硬 identity check 由 worker 启动期 sec_uid 比对负责）
        """
        # 延迟 import，规避循环依赖（inbox → transport → inbox）
        from datetime import datetime, timezone

        from core.douyin.runtime.message_store import (
            ScannedMessage,
            _norm_for_compare,
            _recent_outbound_replies_log,
            _recent_outbound_texts,
            _silent_mark_read_in_db,
            _upsert_conversation_and_message,
        )

        # 延迟 import storage cursor helpers，规避循环依赖
        from core.douyin.runtime.storage import (
            load_conversation_scan_cursor,
            load_scan_cursor,
            save_conversation_scan_cursor,
            save_scan_cursor,
        )

        account_id = str(account.id)
        hinted_conversation_id = await self._resolve_scan_conversation_id(
            account_id,
            conversation_hint,
        )
        if hinted_conversation_id:
            conversation_cursor = load_conversation_scan_cursor(
                account_id,
                hinted_conversation_id,
            )
            if conversation_cursor <= 0:
                conversation_cursor = await self._load_latest_server_message_id_from_db(
                    account_id,
                    hinted_conversation_id,
                )
                if conversation_cursor > 0:
                    save_conversation_scan_cursor(
                        account_id,
                        hinted_conversation_id,
                        conversation_cursor,
                    )

            endpoint = _ENDPOINTS["get_by_conversation"]
            body, seq_id = encode_get_by_conversation_request(
                conversation_id=hinted_conversation_id,
                since_message_id=conversation_cursor,
                limit=20,
            )

            logger.info(
                f"[transport.http] scan_inbox(conv) → POST {endpoint['url']} "
                f"account={account_id} conv={hinted_conversation_id} "
                f"hint={conversation_hint!r} seq_id={seq_id} "
                f"since_message_id={conversation_cursor} body_len={len(body)}"
            )

            resp: SignedResponse = await self._sign.signed_fetch(
                method=endpoint["method"],
                url=endpoint["url"],
                body=body,
                headers=_BASE_IM_HEADERS,
            )
            if not resp.ok:
                self._raise_if_login_expired(
                    resp.status, None,
                    context=f"scan_inbox(conv) http status={resp.status} preview={resp.text[:200]!r}",
                )
                raise RuntimeError(
                    f"scan_inbox(conv) http status={resp.status} preview={resp.text[:200]!r}"
                )
            if not resp.content:
                raise RuntimeError("scan_inbox(conv) 响应 body 为空")

            try:
                result = decode_get_by_conversation_response(resp.content)
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(f"scan_inbox(conv) decode failed: {e}") from e

            if result.status_code != 0:
                if result.status_code == 4 and "conversation not found" in (result.status_msg or "").lower():
                    logger.warning(
                        f"[transport.http] scan_inbox(conv) 会话不存在，回退 get_by_user "
                        f"account={account_id} conv={hinted_conversation_id} "
                        f"hint={conversation_hint!r} since_message_id={conversation_cursor}"
                    )
                    hinted_conversation_id = None
                else:
                    raise RuntimeError(
                        f"scan_inbox(conv) 协议层失败 status_code={result.status_code} "
                        f"msg={result.status_msg!r}"
                    )
            if hinted_conversation_id is None:
                # 会话级快路径失效时，回退到跨会话 get_by_user 主路径，避免打断整个 scan loop。
                pass
            else:
                cursor_max = max(
                    [conversation_cursor]
                    + [m.server_message_id for m in result.messages if m.server_message_id > 0]
                )
                filtered = [
                    m
                    for m in result.messages
                    if m.server_message_id > conversation_cursor
                ]

                account_sec_uid = (account.sec_uid or "").strip()
                self_uid_inferred = 0
                self_uid_from_account_sec = False
                if account_sec_uid:
                    for m in filtered or result.messages:
                        if m.sender_sec_uid == account_sec_uid:
                            self_uid_inferred = m.sender_uid
                            self_uid_from_account_sec = True
                            break
                if self_uid_inferred <= 0:
                    self_uid_inferred = self._infer_self_uid_from_conversation_ids(
                        filtered or result.messages
                    )

                new_messages: list["ScannedMessage"] = []
                dry_run_candidates: list[dict[str, Any]] = []
                echo_cache_msg: dict[str, set[str]] = {}
                echo_cache_log: dict[tuple[str, str], set[str]] = {}

                async def _is_echo(peer_sec_uid: str, conv_id: str, text: str) -> bool:
                    norm = _norm_for_compare(text)
                    if not norm:
                        return False
                    if peer_sec_uid not in echo_cache_msg:
                        outs = await _recent_outbound_texts(account_id, peer_sec_uid)
                        echo_cache_msg[peer_sec_uid] = set(outs)
                    if norm in echo_cache_msg[peer_sec_uid]:
                        return True
                    cache_key = (peer_sec_uid, conv_id)
                    if cache_key not in echo_cache_log:
                        outs = await _recent_outbound_replies_log(account_id, conv_id)
                        echo_cache_log[cache_key] = set(outs)
                    return norm in echo_cache_log[cache_key]

                pending: list[tuple[Any, Any, str]] = []
                for m in filtered:
                    if not m.is_text or not m.text or m.sender_uid <= 0:
                        if m.server_message_id > 0:
                            logger.debug(
                                f"[transport.http] 跳过非文本/无发送方消息 conv={hinted_conversation_id} "
                                f"srv_id={m.server_message_id} msg_type={m.msg_type} "
                                f"text={m.text!r} sender_uid={m.sender_uid} "
                                f"content_preview={(m.content_json or '')[:120]!r}"
                            )
                        continue
                    if account_sec_uid and m.sender_sec_uid == account_sec_uid:
                        continue
                    if (
                        self_uid_from_account_sec
                        and self_uid_inferred > 0
                        and m.sender_uid == self_uid_inferred
                    ):
                        continue
                    if not m.sender_sec_uid:
                        logger.debug(
                            f"[transport.http] 跳过 sender_sec 缺失 conv={hinted_conversation_id} "
                            f"srv_id={m.server_message_id}"
                        )
                        continue
                    if await _is_echo(m.sender_sec_uid, m.conversation_id, m.text):
                        continue

                    received_at = (
                        datetime.fromtimestamp(m.create_time_us / 1_000_000, tz=timezone.utc)
                        if m.create_time_us > 0
                        else datetime.now(tz=timezone.utc)
                    )
                    external_msg_id = f"srv_{m.server_message_id}"
                    if dry_run:
                        if len(dry_run_candidates) >= max(max_conversations or 0, 20):
                            continue
                        dry_run_candidates.append({
                            "external_msg_id": external_msg_id,
                            "peer_sec_uid": m.sender_sec_uid,
                            "text": m.text,
                            "server_message_id": m.server_message_id,
                            "received_at": received_at.isoformat(),
                            "conversation_id": m.conversation_id,
                        })
                        continue
                    pending.append((m, received_at, external_msg_id))

                user_details: dict[int, dict[str, str]] = {}
                if pending:
                    sender_uids = sorted({m.sender_uid for m, _, _ in pending})
                    user_details = await self._resolve_user_details(account, sender_uids)

                touched_conv_ids: set[str] = set()
                for m, received_at, external_msg_id in pending:
                    info = user_details.get(m.sender_uid, {})
                    nickname = info.get("nickname") or None
                    avatar = info.get("avatar") or None

                    try:
                        upsert = await _upsert_conversation_and_message(
                            account_id=account_id,
                            peer_sec_uid=m.sender_sec_uid,
                            peer_nickname=nickname,
                            peer_avatar=avatar,
                            text=m.text,
                            received_at=received_at,
                            raw={
                                "source": "http_protocol.scan_inbox.conversation",
                                "conversation_id": m.conversation_id,
                                "msg_type": m.msg_type,
                                "server_message_id": m.server_message_id,
                                "client_message_id": m.client_message_id,
                                "sender_uid": m.sender_uid,
                                "self_uid_inferred": self_uid_inferred,
                            },
                            external_msg_id=external_msg_id,
                            platform_conversation_id=m.conversation_id,
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            f"[transport.http] upsert 失败 account={account_id} "
                            f"conv={m.conversation_id} err={type(e).__name__}: {e}"
                        )
                        continue

                    if upsert is None:
                        continue

                    db_conv_id, db_msg_id = upsert
                    touched_conv_ids.add(db_conv_id)
                    new_messages.append(
                        ScannedMessage(
                            message_id=db_msg_id,
                            conversation_id=db_conv_id,
                            peer_sec_uid=m.sender_sec_uid,
                            peer_nickname=nickname,
                            text=m.text,
                            received_at=received_at.isoformat(),
                            raw={
                                "source": "http_protocol",
                                "server_message_id": m.server_message_id,
                                "conversation_id": m.conversation_id,
                            },
                        )
                    )

                for conv_id in touched_conv_ids:
                    try:
                        await _silent_mark_read_in_db(conv_id)
                    except Exception as e:  # noqa: BLE001
                        logger.debug(
                            f"[transport.http] silent mark_read 失败 account={account_id} "
                            f"conv={conv_id} err={type(e).__name__}: {e}"
                        )

                if not dry_run and cursor_max > conversation_cursor:
                    save_conversation_scan_cursor(
                        account_id,
                        hinted_conversation_id,
                        cursor_max,
                    )

                if dry_run:
                    self._last_dry_run_candidates = dry_run_candidates
                    self._last_dry_run_self_uid = self_uid_inferred
                    logger.info(
                        f"[transport.http.dry_run] scan_inbox(conv) parse 完成 "
                        f"account={account_id} conv={hinted_conversation_id} "
                        f"messages={len(result.messages)} candidates={len(dry_run_candidates)} "
                        f"self_uid={self_uid_inferred} since_message_id={conversation_cursor} "
                        f"next_server_message_id={cursor_max}"
                    )
                    return []

                logger.info(
                    f"[transport.http] scan_inbox(conv) 完成 account={account_id} "
                    f"account={account_id} conv={hinted_conversation_id} "
                    f"messages={len(result.messages)} new_inbound={len(new_messages)} "
                    f"self_uid={self_uid_inferred} since_message_id={conversation_cursor} -> next_server_message_id={cursor_max}"
                )
                return new_messages

        endpoint = _ENDPOINTS["get_by_user"]
        # cursor_us 多级缓存：
        #   1) 进程内 self._scan_cursor_us[account_id]：本进程历史轮次推过的
        #   2) 落盘 {DATA_DIR}/storage/{account_id}.cursor.json：worker 重启后续传
        #   3) 都没有 → 0：进入"基线模式"——本轮只解析 + 记 cursor 不分发
        #      避免 SCAN_INBOX=true 切换瞬间，把服务端返回的"最近 50 条历史 message"
        #      当成新入向消息触发自动回复。
        cursor_us = self._scan_cursor_us.get(account_id, 0)
        if cursor_us <= 0:
            cursor_us = load_scan_cursor(account_id)
            if cursor_us > 0:
                # 命中文件 → 同步到内存，下一次直接走快路径
                self._scan_cursor_us[account_id] = cursor_us

        # 关键标记：是否首轮基线（cursor 完全没有，主路径下不分发）
        is_baseline = cursor_us <= 0
        # limit 跟 sniff 抓到的真实 web 请求一致（50）；max_conversations 仅用于
        # dry_run 旁路日志限流，跟服务端 limit 解耦
        body, seq_id = encode_get_by_user_request(cursor_us=cursor_us, limit=50)

        logger.info(
            f"[transport.http] scan_inbox → POST {endpoint['url']} "
            f"account={account_id} seq_id={seq_id} cursor_us={cursor_us} "
            f"body_len={len(body)}"
        )

        resp: SignedResponse = await self._sign.signed_fetch(
            method=endpoint["method"],
            url=endpoint["url"],
            body=body,
            headers=_BASE_IM_HEADERS,
        )
        if not resp.ok:
            self._raise_if_login_expired(
                resp.status, None,
                context=f"scan_inbox http status={resp.status} preview={resp.text[:200]!r}",
            )
            raise RuntimeError(
                f"scan_inbox http status={resp.status} preview={resp.text[:200]!r}"
            )
        if not resp.content:
            raise RuntimeError("scan_inbox 响应 body 为空")

        try:
            result = decode_get_by_user_response(resp.content)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"scan_inbox decode failed: {e}") from e

        if result.status_code != 0:
            self._raise_if_login_expired(
                resp.status, result.status_code,
                proto_status_msg=result.status_msg,
                context=(
                    f"scan_inbox 协议层失败 status_code={result.status_code} "
                    f"msg={result.status_msg!r}"
                ),
            )
            raise RuntimeError(
                f"scan_inbox 协议层失败 status_code={result.status_code} "
                f"msg={result.status_msg!r}"
            )

        # ---------------- identity sanity check ----------------
        # get_by_user 协议没有 list_conversations 的 participants 结构，做 hard
        # identity 校验（"参与者 self.sec_uid 必须 == db.sec_uid"）已不可行。
        # 这里只做**软推断 + warn-only**：
        #   - 扫一遍 result.messages，看有没有 sender_sec == account.sec_uid 的条目
        #     有 → 推断 self_uid（用于自己消息去重）
        #     没有 + 响应至少有几条 message → log 一条 warning，让运维注意可能换号了
        #     不抛 AccountIdentityMismatch；硬 mismatch 仍交给 worker 启动期 sec_uid 比对
        account_sec_uid = (account.sec_uid or "").strip()
        self_uid_inferred = 0
        self_uid_from_account_sec = False
        if account_sec_uid:
            for m in result.messages:
                if m.sender_sec_uid == account_sec_uid:
                    self_uid_inferred = m.sender_uid
                    self_uid_from_account_sec = True
                    break
        if self_uid_inferred <= 0:
            self_uid_inferred = self._infer_self_uid_from_conversation_ids(result.messages)
        if account_sec_uid:
            if (
                self_uid_inferred == 0
                and len(result.messages) >= 5
            ):
                # 5 条阈值粗略避开"刚开账号 0 历史"的误报
                logger.warning(
                    f"[transport.http] scan_inbox 未在 {len(result.messages)} 条 "
                    f"messages 里看到任何 sender_sec=={account_sec_uid!r} "
                    f"的自发消息；可能浏览器已换号。account={account_id}"
                )

        # ---------------- 收集入向消息 ----------------
        # max_conversations 只控制 dry_run candidate 列表的上限；HTTP 路径下我们
        # 拿到的是跨会话 message 流，不再有"会话级"限流概念
        new_messages: list["ScannedMessage"] = []
        dry_run_candidates: list[dict[str, Any]] = []

        echo_cache_msg: dict[str, set[str]] = {}
        echo_cache_log: dict[tuple[str, str], set[str]] = {}

        async def _is_echo(
            peer_sec_uid: str, conv_id: str, text: str
        ) -> bool:
            norm = _norm_for_compare(text)
            if not norm:
                return False
            if peer_sec_uid not in echo_cache_msg:
                outs = await _recent_outbound_texts(account_id, peer_sec_uid)
                echo_cache_msg[peer_sec_uid] = set(outs)
            if norm in echo_cache_msg[peer_sec_uid]:
                return True
            cache_key = (peer_sec_uid, conv_id)
            if cache_key not in echo_cache_log:
                outs = await _recent_outbound_replies_log(account_id, conv_id)
                echo_cache_log[cache_key] = set(outs)
            return norm in echo_cache_log[cache_key]

        # ---------------- 第一遍：过滤 + 攒 pending（不发任何 HTTP，不落库） ----------------
        # get_by_user 返回**跨会话** message 流（含系统消息 + 文本消息混合）
        # pending: 通过所有过滤、准备落库的入向消息 (m, received_at, external_msg_id)
        pending: list[tuple[Any, Any, str]] = []
        for m in result.messages:
            if not m.is_text:
                # 系统消息（已读回执 / 输入中 / command_type≠空）跳过；正确性由
                # IMMessage.is_text 判定（msg_type==1 + content_json["text"] 非空）
                logger.debug(
                    f"[transport.http] 跳过非文本消息 srv_id={m.server_message_id} "
                    f"msg_type={m.msg_type} text={m.text!r} "
                    f"content_preview={(m.content_json or '')[:120]!r}"
                )
                continue
            if not m.text:
                logger.debug(
                    f"[transport.http] 跳过空文本消息 srv_id={m.server_message_id} "
                    f"content_preview={(m.content_json or '')[:120]!r}"
                )
                continue
            if m.sender_uid <= 0:
                logger.debug(
                    f"[transport.http] 跳过 sender_uid 缺失消息 srv_id={m.server_message_id} "
                    f"text={m.text!r} content_preview={(m.content_json or '')[:120]!r}"
                )
                continue
            # 自己发的：sender_sec == account.sec_uid 直接跳。
            # self_uid 只有在由 account.sec_uid 明确匹配出来时才参与过滤；
            # 单靠 conversation_id 推断容易把对方 UID 当成自己，导致入向消息被吞。
            if account_sec_uid and m.sender_sec_uid == account_sec_uid:
                continue
            if (
                self_uid_from_account_sec
                and self_uid_inferred > 0
                and m.sender_uid == self_uid_inferred
            ):
                continue
            if not m.sender_sec_uid:
                # 没 sender_sec_uid 就没法做 echo / 落库，跳过
                logger.debug(
                    f"[transport.http] 跳过 sender_sec 缺失 srv_id={m.server_message_id}"
                )
                continue

            if await _is_echo(m.sender_sec_uid, m.conversation_id, m.text):
                continue

            received_at = (
                datetime.fromtimestamp(m.create_time_us / 1_000_000, tz=timezone.utc)
                if m.create_time_us > 0
                else datetime.now(tz=timezone.utc)
            )
            external_msg_id = f"srv_{m.server_message_id}"

            if dry_run or is_baseline:
                # dry_run（dual-run 影子模式）和 baseline（首轮 cursor=0）都只攒
                # 候选清单，**不入库、不返回**。baseline 只推进 cursor，避免历史消息触发回复。
                if len(dry_run_candidates) >= max(max_conversations or 0, 50):
                    continue
                dry_run_candidates.append({
                    "external_msg_id": external_msg_id,
                    "peer_sec_uid": m.sender_sec_uid,
                    "text": m.text,
                    "server_message_id": m.server_message_id,
                    "received_at": received_at.isoformat(),
                })
                continue

            pending.append((m, received_at, external_msg_id))

        # ---------------- 第二遍：批量补昵称 + 落库 + 静默 mark_read ----------------
        # 仅在主路径（非 dry_run / 非 baseline）调 user_detail；
        # baseline / dry_run 不落库自然也不需要昵称
        user_details: dict[int, dict[str, str]] = {}
        if pending:
            sender_uids = sorted({m.sender_uid for m, _, _ in pending})
            user_details = await self._resolve_user_details(account, sender_uids)

        # touched_conv_ids 收集本轮 upsert 成功的 conversation_id
        # 用一次性 silent mark_read 把它们的 unread 清零，避免对同一 conv 重复 update
        touched_conv_ids: set[str] = set()

        for m, received_at, external_msg_id in pending:
            info = user_details.get(m.sender_uid, {})
            nickname = info.get("nickname") or None
            avatar = info.get("avatar") or None

            try:
                upsert = await _upsert_conversation_and_message(
                    account_id=account_id,
                    peer_sec_uid=m.sender_sec_uid,
                    peer_nickname=nickname,
                    peer_avatar=avatar,
                    text=m.text,
                    received_at=received_at,
                    raw={
                        "source": "http_protocol.scan_inbox",
                        "conversation_id": m.conversation_id,
                        "msg_type": m.msg_type,
                        "server_message_id": m.server_message_id,
                        "client_message_id": m.client_message_id,
                        "sender_uid": m.sender_uid,
                        "self_uid_inferred": self_uid_inferred,
                    },
                    external_msg_id=external_msg_id,
                    platform_conversation_id=m.conversation_id,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"[transport.http] upsert 失败 account={account_id} "
                    f"conv={m.conversation_id} err={type(e).__name__}: {e}"
                )
                continue

            if upsert is None:
                continue

            db_conv_id, db_msg_id = upsert
            touched_conv_ids.add(db_conv_id)
            new_messages.append(
                ScannedMessage(
                    message_id=db_msg_id,
                    conversation_id=db_conv_id,
                    peer_sec_uid=m.sender_sec_uid,
                    peer_nickname=nickname,
                    text=m.text,
                    received_at=received_at.isoformat(),
                    raw={
                        "source": "http_protocol",
                        "server_message_id": m.server_message_id,
                    },
                )
            )

        # ---------------- silent mark_read：DB 内清 unread ----------------
        # 关键：协议路径已经把入向消息成功落库 + 准备分发，前端 unread 应当立刻清零；
        # 这里只动本地 DB，不打抖音 mark_read 接口（避免额外风控、避免影响 DOM 路径行为）。
        # 注意：dry_run / baseline 不会进入这里，因为 pending 是空 → touched_conv_ids 空。
        for conv_id in touched_conv_ids:
            try:
                await _silent_mark_read_in_db(conv_id)
            except Exception as e:  # noqa: BLE001
                logger.debug(
                    f"[transport.http] silent mark_read 失败 account={account_id} "
                    f"conv={conv_id} err={type(e).__name__}: {e}"
                )

        # 更新 cursor 规则：
        #   - dry_run（dual_run）：不推进，保持纯影子语义
        #   - baseline（主路径首轮）：推进 + 落盘，从下一轮起才分发新 message
        #   - 正常主路径：推进 + 落盘
        if not dry_run and result.next_cursor_us > cursor_us:
            self._scan_cursor_us[account_id] = result.next_cursor_us
            save_scan_cursor(account_id, result.next_cursor_us)

        if dry_run:
            self._last_dry_run_candidates = dry_run_candidates
            self._last_dry_run_self_uid = self_uid_inferred
            logger.info(
                f"[transport.http.dry_run] scan_inbox parse 完成 account={account_id} "
                f"messages={len(result.messages)} candidates={len(dry_run_candidates)} "
                f"self_uid={self_uid_inferred} cursor_us={cursor_us} "
                f"next_cursor_us={result.next_cursor_us}"
            )
            return []

        if is_baseline:
            self._last_dry_run_candidates = dry_run_candidates
            logger.warning(
                f"[transport.http.baseline] scan_inbox 首轮（cursor=0），仅建立基线 "
                f"account={account_id} messages={len(result.messages)} "
                f"baseline_skipped={len(dry_run_candidates)} "
                f"→ next_cursor_us={result.next_cursor_us}（已落盘，下一轮起正常分发）"
            )
            return []

        logger.info(
            f"[transport.http] scan_inbox 完成 account={account_id} "
            f"messages={len(result.messages)} new_inbound={len(new_messages)} "
            f"self_uid={self_uid_inferred} cursor_us={cursor_us} "
            f"→ next_cursor_us={result.next_cursor_us}"
        )
        return new_messages

    def _do_dual_run_compare(
        self,
        account: "DouyinAccount",
        fallback_result: List["ScannedMessage"],
    ) -> None:
        """
        dual-run 集合对账（同步、零副作用）：

          - 复用本对象 self._last_dry_run_candidates（由并行起跑的 dry_run task 写入）
          - 与 fallback (browser DOM) 这一轮的结果做集合差异比较
          - 输出 [transport.http.dual_run] 日志，供人工/grep 看 HTTP 路径覆盖率/漏报/多报

        关键约定：
          - 不抛异常：dual-run 失败绝不能影响 worker 主流程，全 catch
          - 不落库：完全旁路，只读
          - 比较维度：(peer_sec_uid, normalized_text)，避免 server_message_id 不同
            namespace 导致看不出对应关系
        """
        try:
            from core.douyin.runtime.message_store import _norm_for_compare

            account_id = str(account.id)
            http_candidates = getattr(self, "_last_dry_run_candidates", []) or []

            def _key(peer: str, text: str) -> tuple[str, str]:
                return (peer or "", _norm_for_compare(text or ""))

            http_keys = {
                _key(c["peer_sec_uid"], c["text"]) for c in http_candidates
            }
            browser_keys = {
                _key(m.peer_sec_uid, m.text) for m in (fallback_result or [])
            }

            only_http = http_keys - browser_keys
            only_browser = browser_keys - http_keys
            both = http_keys & browser_keys

            logger.info(
                f"[transport.http.dual_run] account={account_id} "
                f"http_candidates={len(http_candidates)} "
                f"browser_new={len(fallback_result or [])} "
                f"both={len(both)} only_http={len(only_http)} "
                f"only_browser={len(only_browser)}"
            )
            if only_http:
                preview = list(only_http)[:5]
                logger.info(
                    f"[transport.http.dual_run] only_http(前 5) account={account_id} "
                    f"keys={preview}"
                )
            if only_browser:
                preview = list(only_browser)[:5]
                logger.info(
                    f"[transport.http.dual_run] only_browser(前 5) account={account_id} "
                    f"keys={preview}"
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[transport.http.dual_run] 内部异常（已吞） "
                f"account={account.id} err={type(e).__name__}: {e}"
            )


class _NotImplementedYet(RuntimeError):
    """显式标记：协议路径还没开启，应该 fallback。"""
