#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: transport/local_sign_provider.py
@Desc: LocalSignProvider —— 无浏览器的本地签名 + httpx 直发

与 SignProvider 同接口（start/stop/is_ready/signed_fetch/get_cookies），可被
HttpProtocolTransport 无缝替换。区别：

    SignProvider      : 浏览器内 fetch，签名头由抖音前端拦截器自动注入（重、每账号一个浏览器）
    LocalSignProvider : 纯 Python —— 本地算 a_bogus + msToken，httpx 带 cookie/代理发出（轻、共享进程）

signed_fetch 收到的 url 是**裸 endpoint**（如 https://imapi.douyin.com/v1/message/send，无 query）。
浏览器版靠拦截器补齐 web 公共参数；本地版必须自己补：

    最终 query = 该 host 的 web 公共参数 + msToken + a_bogus

────────────────────────────────────────────────────────────────────────
关键闸门（todo 5，必须用真实 cookie/抓包验证）：
  1. 公共参数清单 `_common_params_for` 目前是 web 经验默认值，需与抓包逐字段对齐。
  2. imapi.douyin.com 的 IM 接口在 web 端**可能还要 X-Argus / X-Gorgon（mssdk）头**，
     这类头不是 a_bogus 能产出的。若验证发现 imapi 拒绝本地签名：
       - 方案 A：imapi 仍走浏览器 signer，仅 creator JSON 等接口走 local；
       - 方案 B：补 mssdk 逆向（成本高）。
  3. protobuf body 是否参与 a_bogus 的 body-hash 未定，当前不参与（signer body="")。
在 .env 用 DOUYIN_SIGN_BACKEND=local 灰度，默认 browser，零回归。
────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import hashlib
import logging
import random
from contextlib import suppress
from typing import TYPE_CHECKING, Optional, Union
from urllib.parse import urlparse

from asgiref.sync import sync_to_async

from core.douyin.runtime.transport.sign_types import SignedResponse, SignerUnavailable
from core.douyin.runtime.transport.sign.signer import sign_params

if TYPE_CHECKING:
    import httpx

    from core.douyin.douyin_account_model import DouyinAccount

logger = logging.getLogger(__name__)

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class LocalSignProvider:
    """每账号一份；纯 Python 签名 + httpx 直发，无浏览器。"""

    def __init__(self, *, request_timeout_s: float = 15.0, verify_tls: bool = True) -> None:
        self._account_id: Optional[str] = None
        self._client: "Optional[httpx.AsyncClient]" = None
        self._cookies: dict[str, str] = {}
        self._user_agent: str = _DEFAULT_UA
        self._fp: str = ""
        self._proxy_url: Optional[str] = None
        self._timeout_s = float(request_timeout_s)
        self._verify_tls = bool(verify_tls)
        self._ready = False

    # ---------------- 生命周期 ----------------
    async def start(self, account: "DouyinAccount") -> None:
        import httpx

        self._account_id = str(account.id)
        self._user_agent = (account.user_agent or "").strip() or _DEFAULT_UA
        self._proxy_url = (account.proxy_url or "").strip() or None
        self._fp = _stable_fp_for(self._account_id, self._user_agent)
        self._cookies = await _load_account_cookies(self._account_id)

        if not self._cookies:
            logger.warning(
                f"[sign.local] 账号无可用 cookie（storage_state 缺失/未导入），"
                f"signed_fetch 将大概率 401/风控。account={self._account_id}"
            )

        try:
            self._client = httpx.AsyncClient(
                timeout=self._timeout_s,
                proxy=self._proxy_url,
                follow_redirects=True,
                verify=self._verify_tls,
                headers={"user-agent": self._user_agent},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[sign.local] httpx 客户端创建失败 account={self._account_id} err={e}"
            )
            self._client = None
            self._ready = False
            return

        self._ready = True
        logger.info(
            f"[sign.local] LocalSignProvider 就绪 account={self._account_id} "
            f"proxy={'Y' if self._proxy_url else 'N'} cookies={len(self._cookies)} "
            f"ua={self._user_agent[:40]!r}"
        )

    async def stop(self, account: "DouyinAccount") -> None:
        client = self._client
        self._client = None
        self._ready = False
        if client is not None:
            with suppress(Exception):
                await client.aclose()
        logger.info(f"[sign.local] LocalSignProvider 停止 account={self._account_id}")

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
    ) -> SignedResponse:
        """
        本地签名 + httpx 直发。

        Raises:
            SignerUnavailable: 客户端未就绪 / httpx 网络异常（上层 fallback）。
        """
        import httpx

        if not self.is_ready or self._client is None:
            raise SignerUnavailable("LocalSignProvider 未就绪")

        parsed = urlparse(url)
        host = parsed.netloc.lower()

        # ① 公共参数（host 相关）→ ② 补 msToken → ③ 算 a_bogus
        base_params = _common_params_for(host, self._user_agent)
        sign = sign_params(
            base_params,
            body="",  # protobuf body 是否参与 a_bogus 待验证（见模块说明）
            user_agent=self._user_agent,
            fp=self._fp,
            cookies=self._cookies,
            method=method,
        )
        final_url = f"{url}?{sign.query}"

        # 组装请求头：默认 + 调用方 + UA + Cookie
        req_headers: dict[str, str] = {
            "user-agent": self._user_agent,
            "cookie": _cookie_header(self._cookies),
        }
        for k, v in (headers or {}).items():
            req_headers[k.lower()] = v

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
                f"[sign.local] httpx 请求失败 account={self._account_id} "
                f"host={host} err={type(e).__name__}: {e}"
            )
            raise SignerUnavailable(f"local signed_fetch http error: {e}") from e

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

    # ---------------- cookie ----------------
    async def get_cookies(self, *, domain_contains: str = "douyin.com") -> dict[str, str]:  # noqa: ARG002
        """返回小写 name → value（与 SignProvider.get_cookies 对齐）。"""
        return {k.lower(): v for k, v in self._cookies.items()}

    def set_cookies(self, cookies: dict[str, str]) -> None:
        """直接注入 cookie（验证/调试用：从抓包复制的 Cookie 头）。覆盖 storage_state 里的。"""
        self._cookies = dict(cookies or {})


# ──────────────────────── helpers ────────────────────────


@sync_to_async
def _load_account_cookies(account_id: str) -> dict[str, str]:
    """从加密 storage_state 里取 cookie（name → value）。"""
    from core.douyin.runtime.storage import load_storage_state

    state = load_storage_state(account_id)
    if not state or not isinstance(state, dict):
        return {}
    out: dict[str, str] = {}
    for c in state.get("cookies") or []:
        name = str(c.get("name") or "")
        if name:
            out[name] = str(c.get("value") or "")
    return out


def _cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items() if k)


def _stable_fp_for(account_id: str, user_agent: str) -> str:
    """
    为账号生成一个**稳定**的浏览器指纹串（ABogus 的 fp）。

    指纹必须按账号固定（同一账号每次签名一致），所以用 account_id 做种子确定性生成，
    而不是每次随机。todo 6 会把它升级成账号身份档案里持久化的真实指纹。
    """
    seed = int(hashlib.sha256(account_id.encode("utf-8")).hexdigest(), 16)
    rnd = random.Random(seed)
    platform = "MacIntel" if "Macintosh" in user_agent else "Win32"
    inner_w = rnd.randint(1280, 1920)
    inner_h = rnd.randint(720, 1080)
    outer_w = inner_w + rnd.randint(24, 32)
    outer_h = inner_h + rnd.randint(75, 90)
    screen_y = rnd.choice([0, 30])
    avail_w = rnd.randint(1280, 1920)
    avail_h = rnd.randint(800, 1080)
    return (
        f"{inner_w}|{inner_h}|{outer_w}|{outer_h}|0|{screen_y}|0|0|"
        f"{outer_w}|{outer_h}|{avail_w}|{avail_h}|{inner_w}|{inner_h}|24|24|{platform}"
    )


# 抖音 web 公共查询参数（经验默认；需用抓包逐字段校准——关键闸门 todo 5）
def _common_params_for(host: str, user_agent: str) -> str:
    """
    返回该 host 的 web 公共查询参数串（不含 msToken / a_bogus）。

    说明：imapi.douyin.com 与 creator.douyin.com 的公共参数大体一致（都走 webapp/aid=6383），
    细节字段（version_code 等）随版本变化，抓包后在此微调即可全链路生效。
    """
    is_mac = "Macintosh" in user_agent
    return "&".join(
        [
            "device_platform=webapp",
            "aid=6383",
            "channel=channel_pc_web",
            "pc_client_type=1",
            "version_code=290100",
            "version_name=29.1.0",
            "cookie_enabled=true",
            "screen_width=1920",
            "screen_height=1080",
            "browser_language=zh-CN",
            f"browser_platform={'MacIntel' if is_mac else 'Win32'}",
            "browser_name=Chrome",
            "browser_version=124.0.0.0",
            "browser_online=true",
            "engine_name=Blink",
            "engine_version=124.0.0.0",
            f"os_name={'Mac+OS' if is_mac else 'Windows'}",
            "os_version=10",
            "cpu_core_num=8",
            "device_memory=8",
            "platform=PC",
            "downlink=10",
            "effective_type=4g",
            "round_trip_time=50",
        ]
    )
