"""Async HTTP adapter backed by curl_cffi's Chrome TLS/HTTP2 impersonation."""
from __future__ import annotations

import re
from inspect import isawaitable
from typing import Any, Optional, Union


class ChromeHttpError(RuntimeError):
    """Network/transport failure from the Chrome-impersonating client."""


def resolve_chrome_profile(user_agent: str = "") -> str:
    """Pick the newest shipped Chrome profile no newer than the imported UA."""

    from curl_cffi.requests.impersonate import BrowserType

    supported = {item.value for item in BrowserType}
    match = re.search(r"(?:Chrome|Chromium)/(\d+)", user_agent or "")
    wanted = int(match.group(1)) if match else 999
    versions = sorted(
        int(value[6:])
        for value in supported
        if re.fullmatch(r"chrome\d+", value) and int(value[6:]) <= wanted
    )
    if versions:
        return f"chrome{versions[-1]}"
    return "chrome" if "chrome" in supported else sorted(supported)[0]


class AsyncChromeHttpClient:
    """Small ``httpx.AsyncClient``-compatible surface used by JsSignProvider."""

    def __init__(
        self,
        *,
        user_agent: str,
        timeout: float,
        proxy: Optional[str],
        verify: bool,
        max_connections: int,
    ) -> None:
        from curl_cffi import requests

        self.profile = resolve_chrome_profile(user_agent)
        self._request_exception = requests.exceptions.RequestException
        self._session = requests.AsyncSession(
            max_clients=max(1, int(max_connections)),
            impersonate=self.profile,
            default_headers=False,
            http_version="v2",
            timeout=float(timeout),
            proxy=proxy,
            verify=bool(verify),
        )

    async def request(
        self,
        method: str,
        url: str,
        *,
        content: Optional[Union[str, bytes]] = None,
        headers: Optional[dict[str, str]] = None,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ):
        try:
            return await self._session.request(
                method,
                url,
                data=content,
                headers=headers,
                timeout=timeout,
                discard_cookies=True,
                **kwargs,
            )
        except self._request_exception as exc:
            raise ChromeHttpError(str(exc)) from exc

    async def aclose(self) -> None:
        result = self._session.close()
        if isawaitable(result):
            await result
