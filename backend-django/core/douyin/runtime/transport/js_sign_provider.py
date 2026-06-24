#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: transport/js_sign_provider.py
@Desc: JsSignProvider —— 无浏览器的「JS 签名 + httpx 直发」

与 SignProvider / LocalSignProvider 同接口（start/stop/is_ready/signed_fetch/get_cookies），
可被 HttpProtocolTransport 无缝替换（DOUYIN_SIGN_BACKEND=js）。三种后端对比：

    SignProvider      : 浏览器内 fetch，签名头由抖音前端拦截器注入（重、每账号一个浏览器）
    LocalSignProvider : 纯 Python abogus，只算 a_bogus（缺 bd-ticket-guard，私信发送签不出）
    JsSignProvider    : PyExecJS 执行 vendored dy_ab.js，a_bogus + bd-ticket-guard 齐全（本类）

为什么需要它（关键）：imapi 私信「发送 / 建会话」走抖音 bd-ticket-guard 机制，需要
用账号 EC 私钥(priK) + ticket/ts_sign 现算 `bd-ticket-guard-client-data` 头——这是
纯 Python abogus 给不了的，而 dy_ab.js 的 get_req_sign 正好提供。

凭证分层（对照 DouYin_Spider DouyinAuth.perepare_auth 三件套）：
    cookie               —— 监控/接收 + 发送都需要（必填）
    bd_ticket(priK/...)  —— 仅「发送/建会话」需要；监控只读接口可不带

signed_fetch 收到的 url 是**裸 endpoint**（无 query），本类负责补齐：
    最终 query = host 公共参数 + msToken + a_bogus
    imapi 写接口额外注入 bd-ticket-guard 头（有 bd_ticket 凭证时）
"""
from __future__ import annotations

import logging
from contextlib import suppress
from typing import TYPE_CHECKING, Optional, Union
from urllib.parse import urlparse

from asgiref.sync import sync_to_async

from core.douyin.runtime.transport.sign_types import SignedResponse, SignerUnavailable
from core.douyin.runtime.transport.sign import js_signer
from core.douyin.runtime.transport.sign.mstoken import resolve_mstoken
# 复用 LocalSignProvider 已经校准过的 web 公共参数表与小工具，避免重复维护
from core.douyin.runtime.transport.local_sign_provider import (
    _DEFAULT_UA,
    _common_params_for,
    _cookie_header,
    _load_account_cookies,
)

if TYPE_CHECKING:
    import httpx

    from core.douyin.douyin_account_model import DouyinAccount

logger = logging.getLogger(__name__)

# imapi 写接口（需要 bd-ticket-guard 签名）。只读接口（get_by_user 等）不强制注入。
_IMAPI_WRITE_PATHS = (
    "/v1/message/send",
    "/v2/conversation/create",
    "/v1/conversation/create",
)


class JsSignProvider:
    """每账号一份；用 dy_ab.js 做 a_bogus + bd-ticket-guard 签名，httpx 直发，无浏览器。"""

    def __init__(self, *, request_timeout_s: Optional[float] = None, verify_tls: bool = True) -> None:
        self._account_id: Optional[str] = None
        self._client: "Optional[httpx.AsyncClient]" = None
        self._cookies: dict[str, str] = {}
        self._bd_ticket: dict[str, str] = {}  # {private_key, ticket, ts_sign}
        self._user_agent: str = _DEFAULT_UA
        self._proxy_url: Optional[str] = None
        # 超时默认值统一从 settings 读取（DOUYIN_HTTP_TIMEOUT_S），便于规模化调参
        if request_timeout_s is None:
            request_timeout_s = _setting_float("DOUYIN_HTTP_TIMEOUT_S", 15.0)
        self._timeout_s = float(request_timeout_s)
        self._verify_tls = bool(verify_tls)
        self._ready = False

    # ---------------- 生命周期 ----------------
    async def start(self, account: "DouyinAccount") -> None:
        import httpx

        self._account_id = str(account.id)
        self._user_agent = (getattr(account, "user_agent", "") or "").strip() or _DEFAULT_UA
        self._proxy_url = (getattr(account, "proxy_url", "") or "").strip() or None
        self._cookies, self._bd_ticket = await _load_account_credentials(self._account_id)

        # JS 引擎健康预检：dy_ab.js / Node / PyExecJS 任一缺失则不就绪，触发上层 fallback
        if not await sync_to_async(js_signer.is_available, thread_sensitive=False)():
            logger.warning(
                f"[sign.js] JS 签名引擎不可用（dy_ab.js/Node/PyExecJS 缺失），"
                f"account={self._account_id} 将不就绪"
            )
            self._ready = False
            return

        if not self._cookies:
            logger.warning(
                f"[sign.js] 账号无可用 cookie（storage_state 缺失/未导入），"
                f"signed_fetch 将大概率 401/风控。account={self._account_id}"
            )

        try:
            # 统一连接池上限：每账号独立 client，限制单账号并发连接，避免规模化时句柄/连接爆炸
            limits = httpx.Limits(
                max_connections=_setting_int("DOUYIN_HTTP_MAX_CONNECTIONS", 8),
                max_keepalive_connections=_setting_int("DOUYIN_HTTP_MAX_KEEPALIVE", 4),
                keepalive_expiry=_setting_float("DOUYIN_HTTP_KEEPALIVE_EXPIRY_S", 30.0),
            )
            self._client = httpx.AsyncClient(
                timeout=self._timeout_s,
                proxy=self._proxy_url,
                follow_redirects=True,
                verify=self._verify_tls,
                limits=limits,
                headers={"user-agent": self._user_agent},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[sign.js] httpx 客户端创建失败 account={self._account_id} err={e}")
            self._client = None
            self._ready = False
            return

        self._ready = True
        logger.info(
            f"[sign.js] JsSignProvider 就绪 account={self._account_id} "
            f"proxy={'Y' if self._proxy_url else 'N'} cookies={len(self._cookies)} "
            f"bd_ticket={'Y' if self._bd_ticket.get('private_key') else 'N'}"
        )

    async def stop(self, account: "DouyinAccount") -> None:
        client = self._client
        self._client = None
        self._ready = False
        if client is not None:
            with suppress(Exception):
                await client.aclose()
        logger.info(f"[sign.js] JsSignProvider 停止 account={self._account_id}")

    # ---------------- 健康 ----------------
    @property
    def is_ready(self) -> bool:
        return self._ready and self._client is not None

    async def ensure_ready(self, account: "DouyinAccount") -> bool:
        if self.is_ready:
            return True
        await self.start(account)
        return self.is_ready

    # ---------------- 主 verb ----------------
    async def signed_fetch(
        self,
        method: str,
        url: str,
        *,
        body: Optional[Union[str, bytes]] = None,
        headers: Optional[dict[str, str]] = None,
        timeout_ms: Optional[int] = None,
        use_xhr: bool = False,  # noqa: ARG002  本地无浏览器，xhr/fetch 区分无意义
        base_params: Optional[str] = None,
        extra_params: Optional[dict[str, str]] = None,
    ) -> SignedResponse:
        """JS 签名 + httpx 直发。

        Args:
            base_params: 覆盖默认 host 公共参数串（不含 msToken/a_bogus）。bd-ticket 续期端点
                （creator user_token/v2）的公共参数与 webapp/aid=6383 不同（device_platform=web、
                aid=2906、app_name=aweme_creator_platform），需用本参数传入续期专用参数集。
            extra_params: 追加到查询串的额外键值（值会做 URL 编码），如 certificate=<base64(CSR)>。
                这些参数会一并参与 a_bogus 计算，确保与浏览器一致。

        Raises:
            SignerUnavailable: 引擎/客户端未就绪、签名抛错或 httpx 网络异常（上层 fallback）。
        """
        import httpx
        from urllib.parse import quote

        if not self.is_ready or self._client is None:
            raise SignerUnavailable("JsSignProvider 未就绪")

        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path

        # ① host 公共参数（可被 base_params 覆盖）→ ② 补 msToken → ③ 追加 extra_params
        #  → ④ dy_ab.js 对最终查询串算 a_bogus（extra_params 一并入参，与浏览器对齐）
        base = base_params if base_params is not None else _common_params_for(host, self._user_agent)
        token = resolve_mstoken(self._cookies)
        params_with_token = f"{base}&msToken={token}"
        if extra_params:
            extra = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in extra_params.items())
            params_with_token = f"{params_with_token}&{extra}"
        body_str = ""
        if isinstance(body, str):
            body_str = body
        elif isinstance(body, bytes):
            try:
                body_str = body.decode("utf-8")
            except Exception:
                pass
        elif isinstance(body, (bytearray, memoryview)):
            try:
                body_str = bytes(body).decode("utf-8")
            except Exception:
                pass

        try:
            # thread_sensitive=False：签名只与常驻 Node 进程池通信、不触碰 Django ORM，
            # 放到独立线程池并行执行，避免占用 Django 共享线程（默认 thread_sensitive=True
            # 会让所有签名与 DB 操作在同一线程串行，多账号下成为延迟主因）。
            a_bogus = await sync_to_async(js_signer.get_ab, thread_sensitive=False)(
                params_with_token, body_str
            )
        except js_signer.JsSignerUnavailable as e:
            raise SignerUnavailable(f"JS a_bogus 失败: {e}") from e
        final_url = f"{url}?{params_with_token}&a_bogus={a_bogus}"

        # 组装请求头：默认 + UA + Cookie + （imapi 写接口）bd-ticket-guard
        req_headers: dict[str, str] = {
            "user-agent": self._user_agent,
            "cookie": _cookie_header(self._cookies),
        }
        for k, v in (headers or {}).items():
            req_headers[k.lower()] = v
        await self._maybe_inject_bd_ticket(req_headers, host=host, path=path)

        content: Optional[bytes] = None
        if isinstance(body, (bytes, bytearray, memoryview)):
            content = bytes(body)
        elif isinstance(body, str):
            content = body.encode("utf-8")

        timeout_s = (timeout_ms / 1000.0) if timeout_ms else self._timeout_s
        try:
            resp = await self._client.request(
                method.upper(),
                final_url,
                content=content,
                headers=req_headers,
                timeout=timeout_s,
            )
        except httpx.HTTPError as e:
            logger.warning(
                f"[sign.js] httpx 请求失败 account={self._account_id} "
                f"host={host} err={type(e).__name__}: {e}"
            )
            raise SignerUnavailable(f"js signed_fetch http error: {e}") from e

        raw = resp.content or b""
        text = ""
        with suppress(Exception):
            text = raw.decode("utf-8", "replace")

        return SignedResponse(
            status=resp.status_code,
            url=str(resp.url),
            headers={k.lower(): v for k, v in resp.headers.items()},
            text=text,
            content=raw,
        )

    async def _maybe_inject_bd_ticket(
        self, req_headers: dict[str, str], *, host: str, path: str
    ) -> None:
        """对 imapi 写接口注入 bd-ticket-guard 头（仅当持有 priK/ticket/ts_sign）。

        TODO(待 clone 后核对 DouYin_Spider/builder/header.py 与 douyin_api.send_msg)：
          确认完整头集合——除 `bd-ticket-guard-client-data` 外，是否还需要
          `bd-ticket-guard-version` / `bd-ticket-guard-ree-public-key` / `-web-version`。
          当前先注入最核心的 client-data，gate 验证后据抓包补齐。
        """
        is_write = "imapi.douyin.com" in host and any(path.startswith(p) for p in _IMAPI_WRITE_PATHS)
        if not is_write:
            return
        prik = self._bd_ticket.get("private_key") or ""
        ticket = self._bd_ticket.get("ticket") or ""
        ts_sign = self._bd_ticket.get("ts_sign") or ""
        if not (prik and ticket and ts_sign):
            logger.warning(
                f"[sign.js] imapi 写接口缺 bd-ticket 凭证（priK/ticket/ts_sign），"
                f"发送大概率被拒。account={self._account_id} path={path}"
            )
            return
        try:
            client_data = await sync_to_async(
                js_signer.build_bd_ticket_client_data, thread_sensitive=False
            )(path, ticket, ts_sign, prik)
        except js_signer.JsSignerUnavailable as e:
            raise SignerUnavailable(f"bd-ticket-guard 签名失败: {e}") from e
        req_headers["bd-ticket-guard-client-data"] = client_data

    # ---------------- cookie / 凭证 ----------------
    async def get_cookies(self, *, domain_contains: str = "douyin.com") -> dict[str, str]:  # noqa: ARG002
        """返回小写 name → value（与 SignProvider.get_cookies 对齐）。"""
        return {k.lower(): v for k, v in self._cookies.items()}

    def set_cookies(self, cookies: dict[str, str]) -> None:
        """直接注入 cookie（验证/调试用：从抓包复制的 Cookie 头）。"""
        self._cookies = dict(cookies or {})

    def set_bd_ticket(
        self,
        *,
        private_key: str = "",
        ticket: str = "",
        ts_sign: str = "",
        client_cert: str = "",
    ) -> None:
        """直接注入 bd-ticket 凭证（验证/调试用）。"""
        self._bd_ticket = {
            "private_key": private_key,
            "ticket": ticket,
            "ts_sign": ts_sign,
            "client_cert": client_cert,
        }

    def get_bd_ticket(self) -> dict[str, str]:
        """返回完整 bd-ticket 凭证（private_key/ticket/ts_sign/client_cert）。

        供 pb2 发送编码器组装 Request.token/ts_sign/sdk_cert/reuqest_sign 使用。
        """
        return dict(self._bd_ticket)


# ──────────────────────── helpers ────────────────────────


def _setting_float(name: str, default: float) -> float:
    try:
        from django.conf import settings
        return float(getattr(settings, name, default))
    except Exception:  # noqa: BLE001
        return default


def _setting_int(name: str, default: int) -> int:
    try:
        from django.conf import settings
        return int(getattr(settings, name, default))
    except Exception:  # noqa: BLE001
        return default


@sync_to_async
def _load_account_credentials(account_id: str) -> tuple[dict[str, str], dict[str, str]]:
    """从加密 storage_state 取 (cookies, bd_ticket)。

    cookie:    state["cookies"] = [{name, value}, ...]
    bd_ticket: state["_bd_ticket"] = {private_key, ticket, ts_sign}（录入时写入，见阶段 2）

    storage 不可用（目录无权限/密钥未配/文件损坏）时返回空，不让 provider.start 崩——
    验证/调试场景 cookie 可由 set_cookies 外部注入，不依赖 storage。
    """
    try:
        from core.douyin.runtime.storage import load_storage_state

        state = load_storage_state(account_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[sign.js] 读取 storage_state 失败 account={account_id}: {e}")
        return {}, {}
    if not state or not isinstance(state, dict):
        return {}, {}
    cookies: dict[str, str] = {}
    for c in state.get("cookies") or []:
        name = str(c.get("name") or "")
        if name:
            cookies[name] = str(c.get("value") or "")
    bd_raw = state.get("_bd_ticket") or {}
    bd_ticket = {
        "private_key": str(bd_raw.get("private_key") or ""),
        "ticket": str(bd_raw.get("ticket") or ""),
        "ts_sign": str(bd_raw.get("ts_sign") or ""),
        "client_cert": str(bd_raw.get("client_cert") or ""),
    }
    return cookies, bd_ticket
