"""Pure request planning for the reference PC IM send endpoint.

This module deliberately stops before cryptography and network I/O.  It freezes
the bytes handed to the A-Bogus and ticket-guard signers, then binds their
opaque outputs to the prepared plan with a deterministic digest.
"""
from __future__ import annotations

import hashlib
import re
import struct
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence
from urllib.parse import quote, urlparse

from core.douyin.runtime.transport.browser_fingerprint import browser_fingerprint


REFERENCE_SEND_METHOD = "POST"
REFERENCE_SEND_URL = "https://imapi.douyin.com/v1/message/send"
REFERENCE_SEND_PATH = "/v1/message/send"
REFERENCE_SIGNING_HOST = "www.douyin.com"
REFERENCE_COOKIE_HOST = "www.douyin.com"
PLAN_DIGEST_ALGORITHM = "sha256-u64be-length-framed-v1"

MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_FINAL_URL_BYTES = 16 * 1024
MAX_COOKIE_BYTES = 64 * 1024
MAX_HEADERS = 64
MAX_HEADER_BYTES = 64 * 1024
MAX_HEADER_NAME_BYTES = 64
MAX_USER_AGENT_BYTES = 2048
MAX_FIELD_BYTES = 8192
MIN_TIMEOUT_MS = 1
MAX_TIMEOUT_MS = 120_000

_DIGEST_PREFIX = b"DY_HTTP_PLAN_V1\0"
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_TICKET_HEADERS = (
    "bd-ticket-guard-client-data",
    "bd-ticket-guard-ree-public-key",
    "bd-ticket-guard-version",
    "bd-ticket-guard-web-version",
    "bd-ticket-guard-web-sign-type",
)


class RequestPlanError(ValueError):
    """A deterministic request-plan rejection with a cross-language code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, repr=False)
class PreparedRequestPlan:
    method: str
    endpoint: str
    path: str
    signing_host: str
    cookie_host: str
    raw_cookie_header: str
    ms_token: str
    verify_fp: str
    fp: str
    user_agent: str
    headers: tuple[tuple[str, str], ...]
    body: bytes
    body_sha256: str
    timeout_ms: int
    ticket: str
    ts_sign: str
    private_key: str
    timestamp: int
    ecdh_key: Optional[bytes]
    t_trust: Optional[int]
    a_bogus_query: str
    a_bogus_body: str
    plan_digest: str

    @property
    def ecdh_present(self) -> bool:
        return bool(self.ecdh_key)


@dataclass(frozen=True, repr=False)
class SignerOutputs:
    plan_digest: str
    a_bogus: str
    client_data: str
    ree_public_key: str


@dataclass(frozen=True, repr=False)
class FinalRequestPlan:
    method: str
    url: str
    headers: tuple[tuple[str, str], ...]
    body: bytes
    timeout_ms: int
    plan_digest: str


def percent_encode_query_value(value: str) -> str:
    """Encode one raw query value using RFC3986 unreserved bytes only."""

    return quote(str(value), safe="-._~", encoding="utf-8", errors="strict")


def parse_cookie_lookup(raw_cookie_header: str) -> dict[str, str]:
    """Return only request-plan lookups without rebuilding the raw header.

    Cookie names are case-sensitive, matching the reference ``dict`` lookups.
    When a raw browser snapshot contains duplicate exact names, the final value
    wins for lookup only; the outbound header itself is never rebuilt.
    """

    ms_token = ""
    ticket_sign_id = ""
    trust_cookie = ""
    for item in str(raw_cookie_header or "").split(";"):
        if "=" not in item:
            continue
        name, value = item.strip().split("=", 1)
        if name == "msToken":
            ms_token = value
        if name == "bd_ticket_guard_ts_sign_id":
            ticket_sign_id = value
        elif name == "_bd_ticket_crypt_cookie":
            trust_cookie = value
    return {
        "msToken": ms_token,
        "bd_ticket_guard_ts_sign_id": ticket_sign_id,
        "_bd_ticket_crypt_cookie": trust_cookie,
    }


def prepare_reference_send_request(
    *,
    method: str,
    url: str,
    raw_cookie_header: str,
    user_agent: str,
    caller_headers: Sequence[tuple[str, str]] | dict[str, str],
    body: bytes,
    timeout_ms: int,
    query_ms_token: str,
    verify_fp: str,
    fp: str,
    private_key: str,
    ticket: str,
    ts_sign: str,
    timestamp: int,
    ecdh_key: Optional[bytes],
) -> PreparedRequestPlan:
    """Validate and freeze all unsigned/signing inputs for reference send."""

    normalized_method = str(method or "")
    _reject_controls(normalized_method)
    if normalized_method != REFERENCE_SEND_METHOD:
        raise RequestPlanError("unsupported_method", "reference send requires POST")
    raw_url = str(url or "")
    _reject_controls(raw_url)
    # Compare the frozen endpoint before parsing.  ``urlparse`` can raise a
    # bare ValueError for malformed bracketed hosts; those inputs must retain
    # the same typed ``unsupported_endpoint`` result as the Rust boundary.
    if raw_url != REFERENCE_SEND_URL:
        raise RequestPlanError("unsupported_endpoint", "unsupported reference send endpoint")
    parsed = urlparse(raw_url)
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "imapi.douyin.com"
        or parsed.path != REFERENCE_SEND_PATH
        or parsed.query
        or parsed.fragment
    ):
        raise RequestPlanError("unsupported_endpoint", "unsupported reference send endpoint")

    raw_cookie_header = str(raw_cookie_header or "")
    if not raw_cookie_header:
        raise RequestPlanError("missing_cookie_header", "www Cookie header is required")
    _bounded_text(raw_cookie_header, MAX_COOKIE_BYTES)
    lookup = parse_cookie_lookup(raw_cookie_header)
    # The reference Auth deliberately removes Cookie msToken.  Its cached
    # query token is a separate value and may differ from any stale Cookie
    # token still present in a captured raw header.
    ms_token = str(query_ms_token or "")
    if not ms_token:
        raise RequestPlanError("missing_ms_token", "query msToken is required")

    verify_fp = str(verify_fp or "")
    fp = str(fp or "")
    if not verify_fp or not fp:
        raise RequestPlanError("missing_verify_fp", "verifyFp and fp are required")
    private_key = str(private_key or "")
    ticket = str(ticket or "")
    ts_sign = str(ts_sign or "")
    if not private_key:
        raise RequestPlanError("missing_private_key", "ticket private key is required")
    if not ticket:
        raise RequestPlanError("missing_ticket", "ticket is required")
    if not ts_sign:
        raise RequestPlanError("missing_ts_sign", "ts_sign is required")
    sign_id = lookup["bd_ticket_guard_ts_sign_id"]
    if sign_id and not ts_sign.startswith(sign_id):
        raise RequestPlanError(
            "ticket_session_mismatch",
            "ticket credentials do not match the current Cookie session",
        )

    user_agent = str(user_agent or "")
    if not user_agent:
        raise RequestPlanError("field_too_large", "user-agent must not be empty")
    _bounded_text(user_agent, MAX_USER_AGENT_BYTES)
    for value in (ms_token, verify_fp, fp, private_key, ticket, ts_sign):
        _bounded_text(value, MAX_FIELD_BYTES)

    try:
        timeout_ms = int(timeout_ms)
    except (TypeError, ValueError) as exc:
        raise RequestPlanError("invalid_timeout", "timeout_ms must be an integer") from exc
    if not MIN_TIMEOUT_MS <= timeout_ms <= MAX_TIMEOUT_MS:
        raise RequestPlanError("invalid_timeout", "timeout_ms is outside the supported range")
    try:
        timestamp = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise RequestPlanError("invalid_timestamp", "timestamp must be an integer") from exc
    if timestamp <= 0 or timestamp > 0x7FFF_FFFF_FFFF_FFFF:
        raise RequestPlanError("invalid_timestamp", "timestamp is outside the supported range")

    try:
        body = bytes(body)
    except (TypeError, ValueError) as exc:
        raise RequestPlanError("invalid_body_reference", "body is not a byte sequence") from exc
    if len(body) > MAX_BODY_BYTES:
        raise RequestPlanError("body_too_large", "request body exceeds the supported limit")

    if len(caller_headers) > MAX_HEADERS - len(_TICKET_HEADERS):
        raise RequestPlanError("too_many_headers", "too many caller request headers")
    pairs: Iterable[tuple[str, str]]
    pairs = caller_headers.items() if isinstance(caller_headers, dict) else caller_headers
    headers: list[tuple[str, str]] = [
        ("user-agent", user_agent),
        ("cookie", raw_cookie_header),
    ]
    positions = {name: index for index, (name, _value) in enumerate(headers)}
    caller_seen: set[str] = set()
    for raw_name, raw_value in pairs:
        raw_name_text = str(raw_name or "")
        value = str(raw_value or "")
        # Validate before normalization.  Unicode case folding can turn a
        # non-ASCII character such as U+212A KELVIN SIGN into plain ``k``;
        # accepting that in Python would diverge from Rust's ASCII-token gate.
        _validate_header(raw_name_text, value)
        name = raw_name_text.lower()
        if name in caller_seen or name in _TICKET_HEADERS:
            raise RequestPlanError("duplicate_header", f"duplicate or reserved header: {name}")
        caller_seen.add(name)
        if name == "cookie" and value != raw_cookie_header:
            raise RequestPlanError(
                "duplicate_header", "caller Cookie differs from the frozen raw header"
            )
        if name in positions:
            headers[positions[name]] = (name, value)
        else:
            positions[name] = len(headers)
            headers.append((name, value))

    fingerprint = browser_fingerprint(user_agent)
    for name, value in (
        ("sec-ch-ua", str(fingerprint["sec_ch_ua"])),
        ("sec-ch-ua-mobile", "?0"),
        ("sec-ch-ua-platform", str(fingerprint["sec_ch_ua_platform"])),
    ):
        if name not in positions:
            positions[name] = len(headers)
            headers.append((name, value))

    if len(headers) + len(_TICKET_HEADERS) > MAX_HEADERS:
        raise RequestPlanError("too_many_headers", "too many logical request headers")
    aggregate_header_bytes = sum(
        len(name.encode("ascii")) + len(value.encode("utf-8")) for name, value in headers
    )
    if aggregate_header_bytes > MAX_HEADER_BYTES:
        raise RequestPlanError("field_too_large", "logical request headers are too large")

    ecdh_key = bytes(ecdh_key) if ecdh_key else None
    if ecdh_key and len(ecdh_key) > MAX_FIELD_BYTES:
        raise RequestPlanError("field_too_large", "ECDH key exceeds the supported limit")
    t_trust = 1 if lookup["_bd_ticket_crypt_cookie"] else None
    a_bogus_query = f"msToken={percent_encode_query_value(ms_token)}"
    body_sha256 = hashlib.sha256(body).hexdigest()
    plan_digest = _plan_digest(
        method=normalized_method,
        endpoint=REFERENCE_SEND_URL,
        path=REFERENCE_SEND_PATH,
        signing_host=REFERENCE_SIGNING_HOST,
        cookie_host=REFERENCE_COOKIE_HOST,
        raw_cookie_header=raw_cookie_header,
        query_ms_token=ms_token,
        verify_fp=verify_fp,
        fp=fp,
        user_agent=user_agent,
        headers=headers,
        body=body,
        timeout_ms=timeout_ms,
        ticket=ticket,
        ts_sign=ts_sign,
        private_key=private_key,
        timestamp=timestamp,
        ecdh_present=bool(ecdh_key),
        ecdh_key=ecdh_key or b"",
        t_trust=t_trust,
    )
    return PreparedRequestPlan(
        method=normalized_method,
        endpoint=REFERENCE_SEND_URL,
        path=REFERENCE_SEND_PATH,
        signing_host=REFERENCE_SIGNING_HOST,
        cookie_host=REFERENCE_COOKIE_HOST,
        raw_cookie_header=raw_cookie_header,
        ms_token=ms_token,
        verify_fp=verify_fp,
        fp=fp,
        user_agent=user_agent,
        headers=tuple(headers),
        body=body,
        body_sha256=body_sha256,
        timeout_ms=timeout_ms,
        ticket=ticket,
        ts_sign=ts_sign,
        private_key=private_key,
        timestamp=timestamp,
        ecdh_key=ecdh_key,
        t_trust=t_trust,
        a_bogus_query=a_bogus_query,
        a_bogus_body="",
        plan_digest=plan_digest,
    )


def finalize_reference_send_request(
    prepared: PreparedRequestPlan,
    outputs: SignerOutputs,
) -> FinalRequestPlan:
    """Bind canned/real signer outputs to a prepared plan and form the request."""

    if outputs.plan_digest != prepared.plan_digest:
        raise RequestPlanError(
            "plan_digest_mismatch", "signer outputs belong to another prepared plan"
        )
    for value in (outputs.a_bogus, outputs.client_data, outputs.ree_public_key):
        if not value:
            raise RequestPlanError("invalid_signer_output", "signer outputs must not be empty")
        _bounded_text(value, MAX_FIELD_BYTES)

    final_url = (
        f"{prepared.endpoint}?msToken={percent_encode_query_value(prepared.ms_token)}"
        f"&a_bogus={percent_encode_query_value(outputs.a_bogus)}"
        f"&verifyFp={percent_encode_query_value(prepared.verify_fp)}"
        f"&fp={percent_encode_query_value(prepared.fp)}"
    )
    if len(final_url.encode("utf-8")) > MAX_FINAL_URL_BYTES:
        raise RequestPlanError("url_too_large", "final URL exceeds the supported limit")
    final_headers = prepared.headers + (
        ("bd-ticket-guard-client-data", outputs.client_data),
        ("bd-ticket-guard-ree-public-key", outputs.ree_public_key),
        ("bd-ticket-guard-version", "2"),
        ("bd-ticket-guard-web-version", "1" if prepared.ts_sign.startswith("ts.1") else "2"),
        ("bd-ticket-guard-web-sign-type", "1" if prepared.ecdh_present else "0"),
    )
    if len(final_headers) > MAX_HEADERS:
        raise RequestPlanError("too_many_headers", "too many final request headers")
    final_header_bytes = sum(
        len(name.encode("ascii")) + len(value.encode("utf-8"))
        for name, value in final_headers
    )
    if final_header_bytes > MAX_HEADER_BYTES:
        raise RequestPlanError("field_too_large", "final request headers are too large")
    return FinalRequestPlan(
        method=prepared.method,
        url=final_url,
        headers=final_headers,
        body=prepared.body,
        timeout_ms=prepared.timeout_ms,
        plan_digest=prepared.plan_digest,
    )


def _plan_digest(**values: object) -> str:
    frames: list[bytes] = []
    for key in (
        "method",
        "endpoint",
        "path",
        "signing_host",
        "cookie_host",
        "raw_cookie_header",
        "query_ms_token",
        "verify_fp",
        "fp",
        "user_agent",
    ):
        frames.append(str(values[key]).encode("utf-8"))
    headers = list(values["headers"])  # type: ignore[arg-type]
    frames.append(str(len(headers)).encode("ascii"))
    for name, value in headers:
        frames.extend((str(name).encode("ascii"), str(value).encode("utf-8")))
    frames.extend(
        (
            bytes(values["body"]),
            str(values["timeout_ms"]).encode("ascii"),
            str(values["ticket"]).encode("utf-8"),
            str(values["ts_sign"]).encode("utf-8"),
            str(values["private_key"]).encode("utf-8"),
            str(values["timestamp"]).encode("ascii"),
            b"1" if values["ecdh_present"] else b"0",
            bytes(values["ecdh_key"]),
            b"" if values["t_trust"] is None else str(values["t_trust"]).encode("ascii"),
        )
    )
    digest = hashlib.sha256()
    digest.update(_DIGEST_PREFIX)
    for frame in frames:
        digest.update(struct.pack(">Q", len(frame)))
        digest.update(frame)
    return digest.hexdigest()


def _reject_controls(value: str) -> None:
    if any(unicodedata.category(ch) == "Cc" for ch in str(value)):
        raise RequestPlanError("invalid_control_character", "control character is not allowed")


def _bounded_text(value: str, limit: int) -> None:
    _reject_controls(value)
    if len(str(value).encode("utf-8")) > limit:
        raise RequestPlanError("field_too_large", "request-plan field exceeds its byte limit")


def _validate_header(name: str, value: str) -> None:
    if (
        len(name.encode("ascii", "ignore")) > MAX_HEADER_NAME_BYTES
        or not _HEADER_NAME.fullmatch(name)
    ):
        raise RequestPlanError("invalid_header_name", "invalid header name")
    _bounded_text(value, MAX_FIELD_BYTES)


__all__ = [
    "FinalRequestPlan",
    "PLAN_DIGEST_ALGORITHM",
    "PreparedRequestPlan",
    "RequestPlanError",
    "SignerOutputs",
    "finalize_reference_send_request",
    "parse_cookie_lookup",
    "percent_encode_query_value",
    "prepare_reference_send_request",
]
