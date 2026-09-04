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

signed_fetch 收到的 url 是**裸 endpoint**（无 query）。参考项目的
`/v1/message/send` 补齐签名查询串；其它 creator IM 读取接口保持裸 URL：
    最终 query = 签名前参数 + msToken + a_bogus + 签名后参数
    imapi 写接口额外注入 bd-ticket-guard 头（有 bd_ticket 凭证时）
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Optional, Union
from urllib.parse import urlparse

from asgiref.sync import sync_to_async

from core.douyin.runtime.transport.sign_types import SignedResponse, SignerUnavailable
from core.douyin.runtime.transport.sign import js_signer
from core.douyin.runtime.transport.sign.bd_ticket import derive_ecdh_key
from core.douyin.runtime.transport.sign.dtrait import build_session_dtrait
from core.douyin.runtime.transport.chrome_http_client import (
    AsyncChromeHttpClient,
    ChromeHttpError,
)
from core.douyin.runtime.transport.sign.mstoken import resolve_mstoken
from core.douyin.runtime.transport.browser_fingerprint import browser_fingerprint
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
_IDENTITY_SECURITY_PATH = "/passport/safe/get_identity_security_token/"
_GET_CLIENT_CERT_PATH = "/passport/ticket_guard/get_client_cert/"
_CSRF_BOOTSTRAP_PATH = "/service/2/abtest_config/"


class JsSignProvider:
    """每账号一份；用 dy_ab.js 做 a_bogus + bd-ticket-guard 签名，httpx 直发，无浏览器。"""

    def __init__(self, *, request_timeout_s: Optional[float] = None, verify_tls: bool = True) -> None:
        self._account_id: Optional[str] = None
        self._client: Optional[AsyncChromeHttpClient] = None
        self._cookies: dict[str, str] = {}
        self._cookie_headers: dict[str, str] = {}
        self._bd_ticket: dict[str, str] = {}  # {private_key, ticket, ts_sign}
        self._dtrait: dict[str, str] = {}
        self._dtrait_material: Optional[tuple[str, bytes]] = None
        self._user_agent: str = _DEFAULT_UA
        self._proxy_url: Optional[str] = None
        self._ecdh_key: Optional[bytes] = None
        self._ecdh_retry_at = 0.0
        # 超时默认值统一从 settings 读取（DOUYIN_HTTP_TIMEOUT_S），便于规模化调参
        if request_timeout_s is None:
            request_timeout_s = _setting_float("DOUYIN_HTTP_TIMEOUT_S", 15.0)
        self._timeout_s = float(request_timeout_s)
        self._verify_tls = bool(verify_tls)
        self._ready = False

    # ---------------- 生命周期 ----------------
    async def start(self, account: "DouyinAccount") -> None:
        self._account_id = str(account.id)
        self._user_agent = (getattr(account, "user_agent", "") or "").strip() or _DEFAULT_UA
        self._proxy_url = (getattr(account, "proxy_url", "") or "").strip() or None
        (
            self._cookies,
            self._bd_ticket,
            self._cookie_headers,
            self._dtrait,
        ) = await _load_account_credentials(self._account_id)
        self._ecdh_key = None
        self._ecdh_retry_at = 0.0
        self._dtrait_material = None

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
            self._client = AsyncChromeHttpClient(
                user_agent=self._user_agent,
                timeout=self._timeout_s,
                proxy=self._proxy_url,
                verify=self._verify_tls,
                max_connections=_setting_int("DOUYIN_HTTP_MAX_CONNECTIONS", 8),
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
            f"bd_ticket={'Y' if self._bd_ticket.get('private_key') else 'N'} "
            f"tls_profile={getattr(self._client, 'profile', 'unknown')}"
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
        post_sign_params: Optional[dict[str, str]] = None,
    ) -> SignedResponse:
        """JS 签名 + httpx 直发。

        Args:
            base_params: 覆盖默认 host 公共参数串（不含 msToken/a_bogus）。
                creator JSON 接口的公共参数与 webapp/aid=6383 不同，需由调用方传入。
            extra_params: 追加到查询串的额外键值（值会做 URL 编码）。
                这些参数会一并参与 a_bogus 计算，确保与浏览器一致。
            post_sign_params: 在 a_bogus 后追加、不参与签名的键值。

        Raises:
            SignerUnavailable: 引擎/客户端未就绪、签名抛错或 httpx 网络异常（上层 fallback）。
        """
        from urllib.parse import quote

        if not self.is_ready or self._client is None:
            raise SignerUnavailable("JsSignProvider 未就绪")

        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path

        # DouYin_Spider master 的 send 路径携带
        # msToken -> a_bogus -> verifyFp -> fp；其它 imapi 读取路径保持裸 URL。
        is_reference_send = (
            host == "imapi.douyin.com" and path == "/v1/message/send"
        )
        # 参考项目用 auth.cookie（主站会话）向 imapi 发信，不使用 creator
        # 页面按域抓到的 imapi Cookie 子集。
        cookie_host = "www.douyin.com" if is_reference_send else host
        request_cookies = self._cookies_for_host(cookie_host)
        skip_query_sign = host == "imapi.douyin.com" and not is_reference_send
        final_url = url
        params_with_token = ""
        if not skip_query_sign:
            base = base_params if base_params is not None else _common_params_for(host, self._user_agent)
            token = resolve_mstoken(request_cookies)
            params_with_token = f"{base}&msToken={token}" if base else f"msToken={token}"
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
            a_bogus = ""
            if not skip_query_sign:
                a_bogus = await sync_to_async(js_signer.get_ab, thread_sensitive=False)(
                    params_with_token, body_str
                )
        except js_signer.JsSignerUnavailable as e:
            raise SignerUnavailable(f"JS a_bogus 失败: {e}") from e
        if not skip_query_sign:
            final_url = f"{url}?{params_with_token}&a_bogus={a_bogus}"
        if post_sign_params and not skip_query_sign:
            post = "&".join(
                f"{k}={quote(str(v), safe='')}" for k, v in post_sign_params.items()
            )
            final_url = f"{final_url}&{post}"

        # 组装请求头：默认 + UA + Cookie + （imapi 写接口）bd-ticket-guard
        req_headers: dict[str, str] = {
            "user-agent": self._user_agent,
            "cookie": self._cookie_header_for_host(cookie_host),
        }
        for k, v in (headers or {}).items():
            req_headers[k.lower()] = v
        fingerprint = browser_fingerprint(self._user_agent)
        req_headers.setdefault("sec-ch-ua", fingerprint["sec_ch_ua"])
        req_headers.setdefault("sec-ch-ua-mobile", "?0")
        req_headers.setdefault("sec-ch-ua-platform", fingerprint["sec_ch_ua_platform"])
        await self._maybe_inject_session_dtrait(req_headers, host=host, path=path)
        await self._maybe_inject_bd_ticket(
            req_headers,
            host=host,
            path=path,
            cookies=request_cookies,
        )

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
        except ChromeHttpError as e:
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

    def _cookies_for_host(self, host: str) -> dict[str, str]:
        raw = self._cookie_headers.get((host or "").lower(), "")
        return _parse_cookie_header(raw) if raw else dict(self._cookies)

    def _cookie_header_for_host(self, host: str) -> str:
        return self._cookie_headers.get((host or "").lower(), "") or _cookie_header(
            self._cookies
        )

    async def _maybe_inject_session_dtrait(
        self, req_headers: dict[str, str], *, host: str, path: str
    ) -> None:
        """Attach a fresh passport dtrait header when captured material exists."""

        if req_headers.get("x-tt-session-dtrait"):
            return
        is_identity = host in {"www.douyin.com", "creator.douyin.com"} and path == _IDENTITY_SECURITY_PATH
        if not is_identity:
            return
        blob = str(self._dtrait.get("blob") or "").strip()
        if blob:
            try:
                built = await sync_to_async(build_session_dtrait, thread_sensitive=False)(
                    path,
                    blob,
                    session_material=self._dtrait_material,
                    return_material=True,
                )
                header, self._dtrait_material = built
                req_headers["x-tt-session-dtrait"] = header
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"[sign.js] dtrait 动态构造失败 account={self._account_id} "
                    f"err={type(exc).__name__}: {exc}"
                )
        captured_header = str(self._dtrait.get("header") or "").strip()
        captured_path = str(self._dtrait.get("path") or "").strip()
        if captured_header and captured_path == path:
            req_headers["x-tt-session-dtrait"] = captured_header

    async def _maybe_inject_bd_ticket(
        self,
        req_headers: dict[str, str],
        *,
        host: str,
        path: str,
        cookies: Optional[dict[str, str]] = None,
    ) -> None:
        """对 PC IM 强校验接口注入当前 bd-ticket-guard 头。"""
        is_write = "imapi.douyin.com" in host and any(path.startswith(p) for p in _IMAPI_WRITE_PATHS)
        is_identity = host in {"www.douyin.com", "creator.douyin.com"} and path == _IDENTITY_SECURITY_PATH
        if not (is_write or is_identity):
            return
        prik = self._bd_ticket.get("private_key") or ""
        ticket = self._bd_ticket.get("ticket") or ""
        ts_sign = self._bd_ticket.get("ts_sign") or ""
        if not (prik and ticket and ts_sign):
            logger.warning(
                f"[sign.js] 强校验接口缺 bd-ticket 凭证（priK/ticket/ts_sign），"
                f"account={self._account_id} path={path}"
            )
            return
        effective_cookies = cookies or self._cookies
        sign_id = _cookie_value(effective_cookies, "bd_ticket_guard_ts_sign_id")
        if sign_id and not ts_sign.startswith(sign_id):
            raise SignerUnavailable(
                "bd-ticket-guard 凭证与当前 Cookie 不属于同一次登录，请重新导入"
            )
        ecdh_key = await self._resolve_ecdh_key(prik)
        t_trust = 1 if _cookie_value(effective_cookies, "_bd_ticket_crypt_cookie") else None
        try:
            client_data, ree_key = await asyncio.gather(
                sync_to_async(js_signer.build_bd_ticket_client_data, thread_sensitive=False)(
                    path,
                    ticket,
                    ts_sign,
                    prik,
                    ecdh_key=ecdh_key,
                    t_trust=t_trust,
                ),
                sync_to_async(js_signer.get_ree_key, thread_sensitive=False)(prik),
            )
        except js_signer.JsSignerUnavailable as e:
            raise SignerUnavailable(f"bd-ticket-guard 签名失败: {e}") from e
        req_headers["bd-ticket-guard-client-data"] = client_data
        req_headers["bd-ticket-guard-ree-public-key"] = ree_key
        req_headers["bd-ticket-guard-version"] = "2"
        req_headers["bd-ticket-guard-web-version"] = (
            "1" if ts_sign.startswith("ts.1") else "2"
        )
        req_headers["bd-ticket-guard-web-sign-type"] = "1" if ecdh_key else "0"

    async def _resolve_ecdh_key(self, private_key: str) -> Optional[bytes]:
        """Fetch/cache the server certificate and derive the current HMAC key."""

        if self._ecdh_key is not None:
            return self._ecdh_key
        if not (self._bd_ticket.get("client_cert") or "").startswith("pub."):
            return None
        if time.monotonic() < self._ecdh_retry_at:
            return None
        try:
            server_cert = await self._fetch_server_cert()
            self._ecdh_key = await sync_to_async(derive_ecdh_key, thread_sensitive=False)(
                private_key, server_cert
            )
            return self._ecdh_key
        except Exception as e:  # noqa: BLE001
            self._ecdh_retry_at = time.monotonic() + 60.0
            logger.warning(
                f"[sign.js] ECDH 换证失败，60 秒内回退 ECDSA "
                f"account={self._account_id} err={type(e).__name__}: {e}"
            )
            return None

    async def _fetch_server_cert(self) -> str:
        """Fetch the ticket-guard ECIES server certificate using current cookies."""

        if self._client is None:
            raise SignerUnavailable("JsSignProvider HTTP 客户端未就绪")
        from urllib.parse import urlencode

        origin = "https://www.douyin.com"
        request_cookies = self._cookies_for_host("www.douyin.com")
        query = urlencode(
            {
                "aid": "6383",
                "is_from_ttaccountsdk": "1",
                "msToken": resolve_mstoken(request_cookies),
            }
        )
        a_bogus = await sync_to_async(js_signer.get_ab, thread_sensitive=False)(query, "")
        csrf_token = await self._fetch_secsdk_csrf_token(origin)
        headers = {
            "x-tt-session-dtrait": "",
            "referer": f"{origin}/",
            "user-agent": self._user_agent,
            "accept": "application/json",
        }
        if csrf_token:
            headers["x-secsdk-csrf-token"] = csrf_token
        headers.update(
            {
                "content-type": "application/x-www-form-urlencoded",
                "cookie": self._cookie_header_for_host("www.douyin.com"),
                "accept-language": "zh-CN,zh;q=0.9",
                "origin": origin,
                "priority": "u=1, i",
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
            }
        )
        response = await self._client.request(
            "POST",
            f"{origin}{_GET_CLIENT_CERT_PATH}?{query}&a_bogus={a_bogus}",
            content=b"server_data=1,aid=6383",
            headers=headers,
            timeout=self._timeout_s,
        )
        if response.status_code // 100 != 2:
            raise RuntimeError(f"get_client_cert HTTP {response.status_code}")
        payload = json.loads((response.content or b"{}").decode("utf-8", "replace"))
        if payload.get("message") != "success":
            raise RuntimeError("get_client_cert business response was not success")
        server_cert = str((payload.get("data") or {}).get("server_cert") or "")
        if not server_cert:
            raise RuntimeError("get_client_cert returned an empty certificate")
        return server_cert

    async def _fetch_secsdk_csrf_token(self, origin: str) -> str:
        if self._client is None:
            return ""
        host = urlparse(origin).netloc.lower()
        headers = {
            "x-secsdk-csrf-request": "1",
            "referer": f"{origin}/",
            "user-agent": self._user_agent,
            "x-secsdk-csrf-version": "1.2.22",
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9",
            "cookie": self._cookie_header_for_host(host),
        }
        try:
            response = await self._client.request(
                "HEAD",
                f"{origin}{_CSRF_BOOTSTRAP_PATH}",
                headers=headers,
                timeout=self._timeout_s,
            )
            raw = response.headers.get("x-ware-csrf-token", "")
            parts = raw.split(",")
            return parts[1].strip() if len(parts) > 1 else ""
        except Exception:  # noqa: BLE001
            return ""

    # ---------------- cookie / 凭证 ----------------
    async def get_cookies(self, *, domain_contains: str = "douyin.com") -> dict[str, str]:  # noqa: ARG002
        """返回小写 name → value（与 SignProvider.get_cookies 对齐）。"""
        host = ""
        marker = str(domain_contains or "").lower()
        if "creator" in marker:
            host = "creator.douyin.com"
        elif "imapi" in marker:
            host = "imapi.douyin.com"
        elif marker.startswith("www"):
            host = "www.douyin.com"
        cookies = self._cookies_for_host(host) if host else self._cookies
        return {k.lower(): v for k, v in cookies.items()}

    def set_cookies(self, cookies: dict[str, str]) -> None:
        """直接注入 cookie（验证/调试用：从抓包复制的 Cookie 头）。"""
        self._cookies = dict(cookies or {})
        self._cookie_headers = {}
        self._ecdh_key = None
        self._ecdh_retry_at = 0.0

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
        self._ecdh_key = None
        self._ecdh_retry_at = 0.0

    def get_bd_ticket(self) -> dict[str, str]:
        """返回完整 bd-ticket 凭证（private_key/ticket/ts_sign/client_cert）。

        供 HTTP bd-ticket-guard 头签名使用；新 protobuf envelope 不再嵌入它们。
        """
        return dict(self._bd_ticket)


# ──────────────────────── helpers ────────────────────────


def _cookie_value(cookies: dict[str, str], name: str) -> str:
    if name in cookies:
        return str(cookies.get(name) or "")
    wanted = name.casefold()
    for key, value in cookies.items():
        if str(key).casefold() == wanted:
            return str(value or "")
    return ""


def _parse_cookie_header(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in str(raw or "").split(";"):
        if "=" not in item:
            continue
        name, value = item.strip().split("=", 1)
        if name:
            result[name] = value
    return result


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
def _load_account_credentials(
    account_id: str,
) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str]]:
    """从加密 storage_state 取 cookie、ticket、域名快照和 dtrait。

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
        return {}, {}, {}, {}
    if not state or not isinstance(state, dict):
        return {}, {}, {}, {}
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
    cookie_headers = {
        str(host).lower(): str(value or "")
        for host, value in (state.get("_cookie_headers") or {}).items()
        if host and value
    }
    dtrait_raw = state.get("_dtrait") or {}
    dtrait = {
        "blob": str(dtrait_raw.get("blob") or ""),
        "header": str(dtrait_raw.get("header") or ""),
        "path": str(dtrait_raw.get("path") or ""),
    }
    return cookies, bd_ticket, cookie_headers, dtrait
