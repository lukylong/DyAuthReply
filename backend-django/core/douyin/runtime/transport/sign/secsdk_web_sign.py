"""Pure implementation of Douyin's current x-secsdk-web-signature URL rewrite."""
from __future__ import annotations

import hashlib
import time
from urllib.parse import quote, unquote_plus

WEBSIGN_CONST = "A96D855A08C0A9707F8BEF0D9A527E4E"
PROTECTED_PATHS_GET = (
    "/aweme/v1/web/aweme/detail/",
    "/aweme/v1/web/aweme/post/",
    "/aweme/v1/web/aweme/favorite/",
    "/aweme/v1/web/aweme/listcollection/",
    "/aweme/v1/web/mix/aweme/",
    "/aweme/v1/web/tab/feed/",
    "/aweme/v1/web/mix/list/",
    "/aweme/v1/web/music/aweme/",
    "/aweme/v1/web/music/list/",
    "/aweme/v1/web/mix/detail/",
    "/aweme/v1/web/mix/listcollection/",
    "/aweme/v1/web/music/detail/",
    "/aweme/v1/web/collects/list/",
    "/aweme/v1/web/collects/video/list/",
)

_ENCODE_SAFE = "!*'()"
_SIG_KEY = "x-secsdk-web-signature"
_TS_KEY = "timestamp"


def canonical_query(query: str) -> str:
    """Normalize values like ``encodeURIComponent`` while retaining field order."""

    parts: list[str] = []
    for pair in query.split("&"):
        if not pair:
            continue
        key, separator, value = pair.partition("=")
        if not separator:
            value = ""
        decoded_key = unquote_plus(key, encoding="utf-8", errors="replace")
        decoded_value = unquote_plus(value, encoding="utf-8", errors="replace")
        parts.append(
            f"{decoded_key}={quote(decoded_value, safe=_ENCODE_SAFE, encoding='utf-8')}"
        )
    return "&".join(parts)


def _split(url: str) -> tuple[str, str]:
    base, _, query = url.partition("?")
    kept = []
    for pair in query.split("&"):
        if not pair:
            continue
        name = pair.split("=", 1)[0]
        if name not in {_TS_KEY, _SIG_KEY}:
            kept.append(pair)
    return base, "&".join(kept)


def sign_url(url: str, *, timestamp: int | None = None, uifid: str = "") -> str:
    """Return the exact URL to send, including timestamp and web signature."""

    ts = int(time.time()) if timestamp is None else int(timestamp)
    base, raw_query = _split(url)
    canonical = canonical_query(raw_query)
    names = [part.split("=", 1)[0] for part in canonical.split("&") if part]
    if "uifid" not in names and uifid:
        encoded = quote(uifid, safe=_ENCODE_SAFE, encoding="utf-8")
        canonical = f"{canonical}&uifid={encoded}" if canonical else f"uifid={encoded}"
    signed_query = f"{canonical}&timestamp={ts}" if canonical else f"timestamp={ts}"

    signed_uifid = ""
    for pair in signed_query.split("&"):
        if pair.startswith("uifid="):
            signed_uifid = unquote_plus(pair[6:], encoding="utf-8", errors="replace")
            break
    plain = f"{signed_uifid or uifid}_{ts}_{WEBSIGN_CONST}_{signed_query}"
    signature = hashlib.md5(plain.encode("utf-8")).hexdigest()  # noqa: S324 protocol MD5
    return f"{base}?{signed_query}&{_SIG_KEY}={signature}"


def is_protected(path: str, method: str = "GET") -> bool:
    return str(method).upper() == "GET" and path in PROTECTED_PATHS_GET
