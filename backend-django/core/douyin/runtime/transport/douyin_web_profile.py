#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
对照 DouYin_Spider（builder/auth.py + dy_apis/douyin_api.py）的 www.douyin.com 侧
「当前登录用户」资料拉取：

  1. GET /aweme/v1/web/query/user/  → user_uid（DouyinAPI.get_my_uid）
  2. GET /user/self                 → HTML 正则 secUid（DouyinAPI.get_my_sec_uid）
  3. GET /aweme/v1/web/user/profile/other/ → nickname/avatar（DouyinAPI.get_user_info）

创作者中心 creator.douyin.com 接口需要创作者后台登录态；从主站复制的 Cookie
往往只能在 www.douyin.com 域验过，因此导入账号时应优先走本模块。
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from typing import Any, Mapping, Optional
from urllib.parse import quote, urlencode

from asgiref.sync import sync_to_async

from core.douyin.runtime.transport.local_sign_provider import _cookie_header
from core.douyin.runtime.transport.sign.mstoken import random_mstoken, resolve_mstoken

logger = logging.getLogger(__name__)

_WEB_QUERY_USER_URL = "https://www.douyin.com/aweme/v1/web/query/user/"
_WEB_USER_SELF_URL = "https://www.douyin.com/user/self"
_WEB_PROFILE_OTHER_URL = "https://www.douyin.com/aweme/v1/web/user/profile/other/"


def gen_verify_fp() -> str:
    """生成 verifyFp(s_v_web_id)；对照 DouYin_Spider/utils/dy_util.gen_verify_fp。"""
    base_str = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    t = len(base_str)
    milliseconds = int(round(time.time() * 1000))
    base36 = ""
    while milliseconds > 0:
        remainder = milliseconds % 36
        if remainder < 10:
            base36 = str(remainder) + base36
        else:
            base36 = chr(ord("a") + remainder - 10) + base36
        milliseconds = (milliseconds - remainder) // 36
    r = base36
    o = [""] * 36
    o[8] = o[13] = o[18] = o[23] = "_"
    o[14] = "4"
    for i in range(36):
        if not o[i]:
            n = int(random.random() * t)
            if i == 19:
                n = 3 & n | 8
            o[i] = base_str[n]
    return "verify_" + r + "_" + "".join(o)


def generate_fake_webid(length: int = 19) -> str:
    """对照 DouYin_Spider generate_fake_webid。"""
    return "".join(random.choice("0123456789") for _ in range(length))


def ensure_web_cookie_fields(cookies: Mapping[str, str]) -> dict[str, str]:
    """对照 DouyinAuth.perepare_auth：缺 msToken / s_v_web_id 时补全。"""
    out = dict(cookies or {})
    if not (out.get("msToken") or out.get("msToken_ss")):
        out["msToken"] = random_mstoken()
    if not out.get("s_v_web_id"):
        out["s_v_web_id"] = gen_verify_fp()
    return out


def _web_platform_params(*, is_mac: bool) -> dict[str, str]:
    return {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "publish_video_strategy_type": "2",
        "pc_client_type": "1",
        "version_code": "170400",
        "version_name": "17.4.0",
        "cookie_enabled": "true",
        "screen_width": "1920",
        "screen_height": "1080",
        "browser_language": "zh-CN",
        "browser_platform": "MacIntel" if is_mac else "Win32",
        "browser_name": "Chrome",
        "browser_version": "124.0.0.0",
        "browser_online": "true",
        "engine_name": "Blink",
        "engine_version": "124.0.0.0",
        "os_name": "Mac OS" if is_mac else "Windows",
        "os_version": "10",
        "cpu_core_num": "8",
        "device_memory": "8",
        "platform": "PC",
        "downlink": "10",
        "effective_type": "4g",
        "round_trip_time": "50",
    }


async def _signed_web_get(
    url_base: str,
    params: dict[str, str],
    *,
    cookies: Mapping[str, str],
    user_agent: str,
    referer: str,
    proxy_url: Optional[str] = None,
    timeout_s: float = 12.0,
) -> Optional[dict[str, Any]]:
    """带 a_bogus 的 www.douyin.com GET；返回 JSON dict 或 None。"""
    import httpx

    from core.douyin.runtime.transport.sign import js_signer

    cookies = ensure_web_cookie_fields(cookies)
    s_v_web_id = cookies.get("s_v_web_id") or ""
    ms = resolve_mstoken(cookies)
    params = dict(params)
    params["webid"] = params.get("webid") or generate_fake_webid()
    params["verifyFp"] = s_v_web_id
    params["fp"] = s_v_web_id
    params["msToken"] = ms

    query = urlencode(params)
    try:
        a_bogus = await sync_to_async(js_signer.get_ab)(query, "")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[web.profile] a_bogus 失败 err={type(e).__name__}: {e}")
        return None

    url = f"{url_base}?{query}&a_bogus={a_bogus}"
    headers = {
        "user-agent": user_agent,
        "referer": referer,
        "accept": "application/json, text/plain, */*",
        "cookie": _cookie_header(dict(cookies)),
    }
    try:
        async with httpx.AsyncClient(
            timeout=timeout_s,
            proxy=proxy_url,
            follow_redirects=True,
            verify=True,
        ) as client:
            resp = await client.get(url, headers=headers)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[web.profile] GET 失败 url={url_base} err={type(e).__name__}: {e}")
        return None

    if resp.status_code // 100 != 2:
        logger.warning(
            f"[web.profile] GET 非 2xx url={url_base} status={resp.status_code} "
            f"preview={(resp.text or '')[:160]!r}"
        )
        return None
    try:
        return json.loads(resp.text or "{}")
    except json.JSONDecodeError as e:
        logger.warning(f"[web.profile] JSON 解析失败 url={url_base} err={e}")
        return None


async def _fetch_sec_uid_from_user_self(
    cookies: Mapping[str, str],
    user_agent: str,
    *,
    proxy_url: Optional[str] = None,
    timeout_s: float = 12.0,
) -> str:
    """对照 DouyinAPI.get_my_sec_uid：从 /user/self HTML 正则 secUid。"""
    import httpx

    cookies = ensure_web_cookie_fields(cookies)
    headers = {
        "user-agent": user_agent,
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "cookie": _cookie_header(dict(cookies)),
    }
    try:
        async with httpx.AsyncClient(
            timeout=timeout_s,
            proxy=proxy_url,
            follow_redirects=True,
            verify=True,
        ) as client:
            resp = await client.get(
                _WEB_USER_SELF_URL,
                params={"from_tab_name": "main"},
                headers=headers,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[web.profile] user/self 请求失败 err={type(e).__name__}: {e}")
        return ""

    if resp.status_code // 100 != 2:
        return ""
    m = re.search(r'\\"secUid\\":\\"(.*?)\\"', resp.text or "")
    return m.group(1).strip() if m else ""


def _profile_from_user_info_payload(payload: dict[str, Any]) -> Optional[dict]:
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
        user_id = int(user.get("uid") or user.get("short_id") or 0)
    except (TypeError, ValueError):
        user_id = 0
    return {
        "nickname": nickname,
        "avatar": avatar,
        "sec_uid": str(user.get("sec_uid") or "").strip(),
        "user_id": user_id,
    }


async def fetch_self_profile_via_douyin_web(
    cookies: Mapping[str, str],
    user_agent: str,
    *,
    proxy_url: Optional[str] = None,
    account_id: str = "",
) -> Optional[dict]:
    """
    主站链路拉当前登录用户资料（与 DouYin_Spider demo 一致）。

    Returns:
        {"nickname", "avatar", "sec_uid", "user_id"} 或 None
    """
    cookies = ensure_web_cookie_fields(cookies)
    ua = user_agent or (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    is_mac = "Macintosh" in ua
    aid = account_id or "?"

    # 1) query/user → user_uid
    q_params = _web_platform_params(is_mac=is_mac)
    q_data = await _signed_web_get(
        _WEB_QUERY_USER_URL,
        q_params,
        cookies=cookies,
        user_agent=ua,
        referer="https://www.douyin.com/",
        proxy_url=proxy_url,
    )
    user_uid = 0
    if isinstance(q_data, dict):
        try:
            user_uid = int(q_data.get("user_uid") or q_data.get("uid") or 0)
        except (TypeError, ValueError):
            user_uid = 0
        if user_uid <= 0:
            logger.warning(
                f"[web.profile] query/user 无 user_uid account={aid} keys={list(q_data.keys())[:8]}"
            )
    else:
        logger.warning(f"[web.profile] query/user 失败 account={aid}")

    # 2) user/self → sec_uid
    sec_uid = await _fetch_sec_uid_from_user_self(
        cookies, ua, proxy_url=proxy_url,
    )

    # 3) profile/other → nickname（需 sec_uid）
    profile: Optional[dict] = None
    if sec_uid:
        p_params = _web_platform_params(is_mac=is_mac)
        p_params.update({
            "source": "channel_pc_web",
            "sec_user_id": sec_uid,
            "personal_center_strategy": "1",
            "update_version_code": "170400",
        })
        p_data = await _signed_web_get(
            _WEB_PROFILE_OTHER_URL,
            p_params,
            cookies=cookies,
            user_agent=ua,
            referer=f"https://www.douyin.com/user/{quote(sec_uid, safe='')}",
            proxy_url=proxy_url,
        )
        if isinstance(p_data, dict) and p_data.get("status_code", 0) == 0:
            profile = _profile_from_user_info_payload(p_data)
        elif isinstance(p_data, dict):
            logger.warning(
                f"[web.profile] profile/other 业务错误 account={aid} "
                f"status_code={p_data.get('status_code')} msg={p_data.get('status_msg')!r}"
            )

    if profile:
        if user_uid > 0:
            profile["user_id"] = user_uid
        if sec_uid and not profile.get("sec_uid"):
            profile["sec_uid"] = sec_uid
        logger.info(
            f"[web.profile] 成功 account={aid} nickname={profile.get('nickname')!r} "
            f"user_uid={profile.get('user_id')} sec_uid={(profile.get('sec_uid') or '')[:20]}"
        )
        return profile

    # query/user 至少有 uid 时仍返回最小资料（昵称待用户改）
    if user_uid > 0 and sec_uid:
        logger.info(
            f"[web.profile] profile/other 无昵称，返回 uid+sec_uid account={aid} user_uid={user_uid}"
        )
        return {
            "nickname": "",
            "avatar": "",
            "sec_uid": sec_uid,
            "user_id": user_uid,
        }

    return None
