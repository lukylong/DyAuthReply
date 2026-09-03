"""Helpers that keep HTTP query/header fingerprints aligned with the imported UA."""
from __future__ import annotations

import re
from typing import Any


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def browser_fingerprint(user_agent: str = "") -> dict[str, Any]:
    """Return browser fields that stay consistent with ``user_agent``.

    Account credentials are imported from a real browser. Reusing that UA while
    advertising a different Chrome version or OS in query/protobuf fields creates
    a cross-layer fingerprint mismatch that Argus can reject.
    """

    ua = (user_agent or "").strip() or DEFAULT_USER_AGENT
    match = re.search(r"(?:Chrome|Chromium)/([0-9]+(?:\.[0-9]+){0,3})", ua)
    browser_version = match.group(1) if match else "124.0.0.0"
    major = browser_version.split(".", 1)[0]

    if "Macintosh" in ua:
        browser_platform = "MacIntel"
        sec_ch_ua_platform = '"macOS"'
        os_name = "Mac OS"
        pc_libra_divert = "Mac"
    elif "Linux" in ua and "Android" not in ua:
        browser_platform = "Linux x86_64"
        sec_ch_ua_platform = '"Linux"'
        os_name = "Linux"
        pc_libra_divert = "Linux"
    else:
        browser_platform = "Win32"
        sec_ch_ua_platform = '"Windows"'
        os_name = "Windows"
        pc_libra_divert = "Windows"

    return {
        "user_agent": ua,
        "browser_version": browser_version,
        "engine_version": browser_version,
        "browser_platform": browser_platform,
        "sec_ch_ua_platform": sec_ch_ua_platform,
        "sec_ch_ua": (
            f'"Not=A?Brand";v="99", "Google Chrome";v="{major}", '
            f'"Chromium";v="{major}"'
        ),
        "os_name": os_name,
        "os_version": "10",
        "pc_libra_divert": pc_libra_divert,
        "app_version": ua.removeprefix("Mozilla/"),
    }
