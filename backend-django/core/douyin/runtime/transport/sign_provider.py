#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: transport/sign_provider.py
@Desc: SignProvider —— 浏览器为签名服务，HTTP 主路径在 Python 内

Phase 3 hybrid 路线核心抽象：

抖音前端调 IM 接口时，会经过 mssdk / x-bogus / a_bogus / msToken / X-Argus 等
一系列动态签名注入。这些签名算法是混淆 JS，逆向出来后平台一改就废。

我们的策略：
  - 每个账号的 BrowserContext 仍然存活，但**不再做 DOM 业务**
  - 在该 context 内常驻一个 "signer page"，只用来跑 JS、读 cookie
  - 真正的 HTTP 收发由 Python httpx 在主进程发出（高吞吐）
  - 等价于把浏览器降级成"账号专属签名服务器"

阶段化路线：
  - 3.1.0 (本文件)     : signed_fetch —— 浏览器内 fetch 把请求/响应都过一遍 JS 沙箱，
                         好处是抖音的 fetch 拦截器自动注入所有签名头，几乎无须知道签名算法
  - 3.1.1 (后续)       : 仅签名走浏览器（page.evaluate 跑 sign 函数），
                         请求体由 httpx 发，吞吐再翻一档
  - 3.1.2 (可选)       : 完全协议化（脱浏览器），需要 reverse a_bogus —— 不在 hybrid 路径上

签名失败的容错：浏览器侧崩溃 / page.evaluate 抛 → SignerUnavailable，
上层 (HttpProtocolTransport) 应该退化到 BrowserTransport (DOM) 兜底。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from contextlib import suppress
from dataclasses import dataclass, field
from urllib.parse import urlparse
from typing import TYPE_CHECKING, Any, Optional, Union

from core.douyin.runtime.browser import BrowserManager
from core.douyin.runtime import selectors as S
# 共享类型已下沉到中性模块 sign_types（不依赖浏览器）；此处 re-export 保持兼容。
from core.douyin.runtime.transport.sign_types import SignedResponse, SignerUnavailable

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page

    from core.douyin.douyin_account_model import DouyinAccount

logger = logging.getLogger(__name__)


# 浏览器侧 fetch 包装：
#   - body_b64: 可选 base64 字符串，存在则解码成 Uint8Array 当 binary body 发
#   - body:     可选 string，没有 body_b64 时按文本 body 发
#   - 响应永远拿 ArrayBuffer，再 base64 回传（便于 protobuf / 二进制接口）
#   - 同时尝试 utf-8 decode 成 text（JSON 接口时上层 .json() 仍可用）
# 用 try/catch 把所有错误归一到结构化返回，避免 page.evaluate 抛超时
_SIGNED_FETCH_JS = r"""
async ({ method, url, body, body_b64, headers, timeoutMs }) => {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs || 15000);
  // base64 → ArrayBuffer
  const b64ToBytes = (s) => {
    const bin = atob(s);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  };
  // ArrayBuffer → base64
  const bytesToB64 = (buf) => {
    const bytes = new Uint8Array(buf);
    let s = '';
    const CHUNK = 0x8000;
    for (let i = 0; i < bytes.length; i += CHUNK) {
      s += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
    }
    return btoa(s);
  };
  try {
    const init = {
      method: method || 'GET',
      headers: headers || {},
      credentials: 'include',
      signal: ctrl.signal,
    };
    if (method !== 'GET' && method !== 'HEAD') {
      if (body_b64) {
        init.body = b64ToBytes(body_b64);
      } else if (body !== null && body !== undefined) {
        init.body = body;
      }
    }
    const r = await fetch(url, init);
    const buf = await r.arrayBuffer();
    const respHeaders = {};
    r.headers.forEach((v, k) => { respHeaders[k] = v; });
    let text = '';
    try { text = new TextDecoder('utf-8', { fatal: false }).decode(buf); } catch (e) { text = ''; }
    return {
      ok: true,
      status: r.status,
      url: r.url,
      headers: respHeaders,
      text: text,
      content_b64: bytesToB64(buf),
    };
  } catch (e) {
    let pageUrl = '';
    let readyState = '';
    let online = null;
    try { pageUrl = String(location && location.href || ''); } catch (_) {}
    try { readyState = String(document && document.readyState || ''); } catch (_) {}
    try { online = !!(navigator && navigator.onLine); } catch (_) {}
    return {
      ok: false,
      error: String(e && e.message || e),
      error_name: String(e && e.name || ''),
      error_stack: String(e && e.stack || ''),
      page_url: pageUrl,
      ready_state: readyState,
      online: online,
    };
  } finally {
    clearTimeout(t);
  }
}
"""

_SIGNED_XHR_JS = r"""
async ({ method, url, body, body_b64, headers, timeoutMs }) => {
  const b64ToBytes = (s) => {
    const bin = atob(s);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  };
  const bytesToB64 = (buf) => {
    const bytes = new Uint8Array(buf || new ArrayBuffer(0));
    let s = '';
    const CHUNK = 0x8000;
    for (let i = 0; i < bytes.length; i += CHUNK) {
      s += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
    }
    return btoa(s);
  };
  return await new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    let timer = null;
    try {
      xhr.open(method || 'GET', url, true);
      xhr.withCredentials = true;
      xhr.responseType = 'arraybuffer';
      const reqHeaders = headers || {};
      Object.keys(reqHeaders).forEach((k) => {
        try { xhr.setRequestHeader(k, reqHeaders[k]); } catch (_) {}
      });
      xhr.onload = () => {
        clearTimeout(timer);
        const respHeaders = {};
        try {
          const raw = xhr.getAllResponseHeaders() || '';
          raw.trim().split(/[\r\n]+/).forEach((line) => {
            const idx = line.indexOf(':');
            if (idx > 0) {
              const key = line.slice(0, idx).trim().toLowerCase();
              const val = line.slice(idx + 1).trim();
              if (key) respHeaders[key] = val;
            }
          });
        } catch (_) {}
        const buf = xhr.response || new ArrayBuffer(0);
        let text = '';
        try { text = new TextDecoder('utf-8', { fatal: false }).decode(buf); } catch (_) { text = ''; }
        resolve({
          ok: true,
          status: xhr.status,
          url: xhr.responseURL || url,
          headers: respHeaders,
          text,
          content_b64: bytesToB64(buf),
        });
      };
      xhr.onerror = (e) => {
        clearTimeout(timer);
        let pageUrl = '';
        let readyState = '';
        let online = null;
        try { pageUrl = String(location && location.href || ''); } catch (_) {}
        try { readyState = String(document && document.readyState || ''); } catch (_) {}
        try { online = !!(navigator && navigator.onLine); } catch (_) {}
        resolve({
          ok: false,
          error: String((e && e.message) || 'XMLHttpRequest failed'),
          error_name: 'XHRNetworkError',
          error_stack: '',
          page_url: pageUrl,
          ready_state: readyState,
          online,
        });
      };
      xhr.ontimeout = () => {
        clearTimeout(timer);
        resolve({
          ok: false,
          error: 'XMLHttpRequest timeout',
          error_name: 'XHRTimeout',
          error_stack: '',
          page_url: String(location && location.href || ''),
          ready_state: String(document && document.readyState || ''),
          online: !!(navigator && navigator.onLine),
        });
      };
      xhr.onabort = () => {
        clearTimeout(timer);
        resolve({
          ok: false,
          error: 'XMLHttpRequest aborted',
          error_name: 'XHRAbort',
          error_stack: '',
          page_url: String(location && location.href || ''),
          ready_state: String(document && document.readyState || ''),
          online: !!(navigator && navigator.onLine),
        });
      };
      xhr.timeout = timeoutMs || 15000;
      timer = setTimeout(() => {
        try { xhr.abort(); } catch (_) {}
      }, (timeoutMs || 15000) + 50);
      if (method !== 'GET' && method !== 'HEAD') {
        if (body_b64) {
          const bytes = b64ToBytes(body_b64);
          xhr.send(bytes.buffer);
        } else if (body !== null && body !== undefined) {
          xhr.send(body);
        } else {
          xhr.send();
        }
      } else {
        xhr.send();
      }
    } catch (e) {
      clearTimeout(timer);
      resolve({
        ok: false,
        error: String(e && e.message || e),
        error_name: String(e && e.name || 'XHREvalError'),
        error_stack: String(e && e.stack || ''),
        page_url: String(location && location.href || ''),
        ready_state: String(document && document.readyState || ''),
        online: !!(navigator && navigator.onLine),
      });
    }
  });
}
"""


# 将抖音前端常用的"宿主页面"放在这里；signer page 必须停在能调用 fetch 的同源页面，
# 否则浏览器拦截器（自动注入签名）不会生效。
_SIGNER_HOSTS = (
    "https://www.douyin.com/",
    "https://creator.douyin.com/",
)

_SIGNER_HOME_BY_HOST = {
    "www.douyin.com": "https://www.douyin.com/",
    "creator.douyin.com": S.CREATOR_HOME,
}


class SignProvider:
    """
    每账号一份。生命周期：

        provider = SignProvider()
        await provider.start(account)        # 准备 signer page
        ...
        resp = await provider.signed_fetch('POST', url, body=...)
        ...
        await provider.stop(account)         # 关闭 signer page

    线程模型：所有方法在 worker asyncio 事件循环里调用；内部用 asyncio.Lock 保护
    page.evaluate 串行（Playwright 的 page 不是并发安全的）。
    """

    def __init__(
        self,
        *,
        signer_url: str = S.CREATOR_HOME,
        evaluate_timeout_ms: int = 15000,
    ) -> None:
        self._signer_url = signer_url
        self._evaluate_timeout_ms = int(evaluate_timeout_ms)
        self._page: Optional["Page"] = None
        self._account_id: Optional[str] = None
        self._lock = asyncio.Lock()
        self._fail_streak = 0
        self._last_health_at: float = 0.0

    # ---------------- 生命周期 ----------------
    async def start(self, account: "DouyinAccount") -> None:
        """
        在账号 BrowserContext 内开一个常驻 signer page 并 navigate 到同源宿主页。

        失败不会抛——transport 上层应该容错。
        """
        self._account_id = str(account.id)
        try:
            ctx: "BrowserContext" = await BrowserManager.get_or_create_context(account)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[sign] 启动失败：拿不到 BrowserContext account={self._account_id} "
                f"err={type(e).__name__}: {e}"
            )
            self._page = None
            return

        try:
            page = await ctx.new_page()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[sign] 创建 signer page 失败 account={self._account_id} err={e}")
            return

        try:
            await page.goto(self._signer_url, wait_until="domcontentloaded", timeout=20000)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[sign] signer page 跳转失败 account={self._account_id} url={self._signer_url} err={e}"
            )
            with suppress(Exception):
                await page.close()
            return

        self._page = page
        logger.info(
            f"[sign] SignProvider 就绪 account={self._account_id} signer_url={self._signer_url}"
        )

    async def stop(self, account: "DouyinAccount") -> None:
        page = self._page
        self._page = None
        if page is None:
            return
        with suppress(Exception):
            await page.close()
        logger.info(f"[sign] SignProvider 停止 account={self._account_id}")

    # ---------------- 健康/容错 ----------------
    @property
    def is_ready(self) -> bool:
        """signer page 是否处于可用状态。"""
        page = self._page
        return page is not None and not page.is_closed()

    async def ensure_ready(self, account: "DouyinAccount") -> bool:
        """如果 signer page 已经死掉，尝试一次性恢复。返回是否最终可用。"""
        if self.is_ready:
            return True
        logger.warning(f"[sign] signer page 不可用，尝试恢复 account={self._account_id}")
        await self.start(account)
        return self.is_ready

    @staticmethod
    def _preferred_signer_host(url: str, headers: Optional[dict[str, str]] = None) -> str:
        candidates = [
            str((headers or {}).get("origin") or ""),
            str((headers or {}).get("referer") or ""),
            str(url or ""),
        ]
        for raw in candidates:
            try:
                host = urlparse(raw).netloc.lower()
            except Exception:
                host = ""
            if host.endswith("creator.douyin.com"):
                return "creator.douyin.com"
            if host.endswith("www.douyin.com"):
                return "www.douyin.com"
        return "creator.douyin.com"

    async def _ensure_signer_host(
        self,
        preferred_host: str,
    ) -> None:
        page = self._page
        if page is None or page.is_closed():
            raise SignerUnavailable("signer page 未就绪")
        current_host = ""
        try:
            current_host = urlparse(page.url or "").netloc.lower()
        except Exception:
            current_host = ""
        if current_host == preferred_host:
            return

        target_url = _SIGNER_HOME_BY_HOST.get(preferred_host) or self._signer_url
        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(0.8)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[sign] signer page 切宿主失败 account={self._account_id} "
                f"from={current_host or 'unknown'} to={preferred_host} err={e}"
            )
            raise SignerUnavailable(
                f"signer host switch failed: {current_host or 'unknown'} -> {preferred_host}: {e}"
            ) from e
        logger.info(
            f"[sign] signer page 已切换宿主 account={self._account_id} "
            f"host={preferred_host} url={page.url}"
        )

    # ---------------- 主 verbs ----------------
    async def signed_fetch(
        self,
        method: str,
        url: str,
        *,
        body: Optional[Union[str, bytes]] = None,
        headers: Optional[dict[str, str]] = None,
        timeout_ms: Optional[int] = None,
        use_xhr: bool = False,
    ) -> SignedResponse:
        """
        在 signer page 内做一次 fetch。浏览器侧自带的 axios/fetch 拦截器会
        自动给请求注入抖音需要的签名头（X-Bogus / a_bogus / msToken / X-Argus 等）。

        Args:
            body: bytes（如 protobuf）会被 base64 传到 JS 还原成 Uint8Array；
                  str 直接当文本 body；None 则不带 body（GET/HEAD）。
            headers: 请求头；调用方负责设 content-type=application/x-protobuf 等。

        Raises:
            SignerUnavailable: signer page 不可用、evaluate 抛异常、或浏览器侧 fetch 失败。
        """
        if not self.is_ready:
            raise SignerUnavailable("signer page 未就绪")

        payload: dict[str, Any] = {
            "method": method.upper(),
            "url": url,
            "body": None,
            "body_b64": None,
            "headers": headers or {},
            "timeoutMs": timeout_ms or self._evaluate_timeout_ms,
        }
        if isinstance(body, (bytes, bytearray, memoryview)):
            payload["body_b64"] = base64.b64encode(bytes(body)).decode("ascii")
        elif isinstance(body, str):
            payload["body"] = body

        page = self._page
        assert page is not None  # type narrow（is_ready 已确保）

        async with self._lock:
            preferred_host = self._preferred_signer_host(url, headers)
            await self._ensure_signer_host(preferred_host)
            try:
                runner = _SIGNED_XHR_JS if use_xhr else _SIGNED_FETCH_JS
                result = await page.evaluate(runner, payload)
            except Exception as e:  # noqa: BLE001
                self._fail_streak += 1
                logger.warning(
                    f"[sign] page.evaluate 抛错 account={self._account_id} "
                    f"streak={self._fail_streak} transport={'xhr' if use_xhr else 'fetch'} "
                    f"err={type(e).__name__}: {e}"
                )
                raise SignerUnavailable(f"signed_fetch evaluate failed: {e}") from e

        if not result or not result.get("ok"):
            self._fail_streak += 1
            err = result.get("error") if isinstance(result, dict) else "no result"
            err_name = result.get("error_name") if isinstance(result, dict) else ""
            page_url = result.get("page_url") if isinstance(result, dict) else ""
            ready_state = result.get("ready_state") if isinstance(result, dict) else ""
            online = result.get("online") if isinstance(result, dict) else None
            logger.warning(
                f"[sign] signed_fetch 浏览器侧失败 account={self._account_id} "
                f"streak={self._fail_streak} err={err} "
                f"err_name={err_name or 'unknown'} page={page_url or 'unknown'} "
                f"ready_state={ready_state or 'unknown'} online={online}"
            )
            raise SignerUnavailable(
                "signed_fetch failed in browser: "
                f"{err} (name={err_name or 'unknown'}, page={page_url or 'unknown'}, "
                f"ready_state={ready_state or 'unknown'}, online={online})"
            )

        # 解 content base64；解失败不致命，仅 content 为空
        content_b64 = result.get("content_b64") or ""
        try:
            content_bytes = base64.b64decode(content_b64) if content_b64 else b""
        except Exception:  # noqa: BLE001
            content_bytes = b""

        self._fail_streak = 0
        self._last_health_at = time.time()
        return SignedResponse(
            status=int(result.get("status") or 0),
            url=str(result.get("url") or url),
            headers={str(k).lower(): str(v) for k, v in (result.get("headers") or {}).items()},
            text=str(result.get("text") or ""),
            content=content_bytes,
        )

    async def get_cookies(self, *, domain_contains: str = "douyin.com") -> dict[str, str]:
        """
        从 signer page 所属 BrowserContext 读 cookie 字典（小写 name → value）。

        用于：a) 提取 msToken / sessionid 给 httpx 直发；b) 调试日志。
        """
        page = self._page
        if page is None:
            return {}
        ctx = page.context
        try:
            cookies = await ctx.cookies()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[sign] get_cookies 失败 account={self._account_id} err={e}")
            return {}
        out: dict[str, str] = {}
        for c in cookies or []:
            d = (c.get("domain") or "").lstrip(".")
            if domain_contains and domain_contains not in d:
                continue
            name = str(c.get("name") or "")
            if name:
                out[name.lower()] = str(c.get("value") or "")
        return out

    async def evaluate(self, js: str, arg: Any = None) -> Any:
        """
        逃生口：让上层在签名页直接跑任意 JS（调试 / 提取 webid / 调签名函数等）。

        请避免在主路径用，page.evaluate 是单线程瓶颈。
        """
        if not self.is_ready:
            raise SignerUnavailable("signer page 未就绪")
        page = self._page
        assert page is not None
        async with self._lock:
            try:
                return await page.evaluate(js, arg)
            except Exception as e:  # noqa: BLE001
                self._fail_streak += 1
                raise SignerUnavailable(f"evaluate failed: {e}") from e
