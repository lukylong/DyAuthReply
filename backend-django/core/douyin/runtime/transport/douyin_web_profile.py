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
from urllib.parse import quote, urlencode, urlparse

from asgiref.sync import sync_to_async

from core.douyin.runtime.transport.browser_fingerprint import browser_fingerprint
from core.douyin.runtime.transport.local_sign_provider import _cookie_header
from core.douyin.runtime.transport.sign.mstoken import random_mstoken, resolve_mstoken

logger = logging.getLogger(__name__)

_WEB_QUERY_USER_URL = "https://www.douyin.com/aweme/v1/web/query/user/"
_WEB_USER_SELF_URL = "https://www.douyin.com/user/self"
_WEB_PROFILE_OTHER_URL = "https://www.douyin.com/aweme/v1/web/user/profile/other/"
_WEB_AWEME_POST_URL = "https://www.douyin.com/aweme/v1/web/aweme/post/"


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
    # SignProvider historically exposes lower-case lookup keys. Restore the
    # case-sensitive names Chromium actually sends before serializing Cookie.
    for canonical in ("UIFID", "UIFID_temp", "msToken", "msToken_ss"):
        matches = [key for key in out if str(key).casefold() == canonical.casefold()]
        if canonical not in out and matches:
            out[canonical] = out[matches[0]]
        for key in matches:
            if key != canonical:
                out.pop(key, None)
    if not (out.get("msToken") or out.get("msToken_ss")):
        out["msToken"] = random_mstoken()
    if not out.get("s_v_web_id"):
        out["s_v_web_id"] = gen_verify_fp()
    return out


def _cookie_value(cookies: Mapping[str, str], name: str) -> str:
    if name in cookies:
        return str(cookies.get(name) or "")
    wanted = name.casefold()
    for key, value in cookies.items():
        if str(key).casefold() == wanted:
            return str(value or "")
    return ""


def _web_platform_params(*, user_agent: str) -> dict[str, str]:
    fingerprint = browser_fingerprint(user_agent)
    return {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "publish_video_strategy_type": "2",
        "update_version_code": "170400",
        "pc_client_type": "1",
        "pc_libra_divert": fingerprint["pc_libra_divert"],
        "support_h265": "1",
        "support_dash": "1",
        "cpu_core_num": "8",
        "version_code": "170400",
        "version_name": "17.4.0",
        "cookie_enabled": "true",
        "screen_width": "1920",
        "screen_height": "1080",
        "browser_language": "zh-CN",
        "browser_platform": fingerprint["browser_platform"],
        "browser_name": "Chrome",
        "browser_version": fingerprint["browser_version"],
        "browser_online": "true",
        "engine_name": "Blink",
        "engine_version": fingerprint["engine_version"],
        "os_name": fingerprint["os_name"],
        "os_version": fingerprint["os_version"],
        "device_memory": "8",
        "platform": "PC",
        "downlink": "10",
        "effective_type": "4g",
        "round_trip_time": "0",
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
    verify_fp_after_sign: bool = False,
) -> Optional[dict[str, Any]]:
    """带 a_bogus 的 www.douyin.com GET；返回 JSON dict 或 None。"""
    import httpx

    from core.douyin.runtime.transport.sign import js_signer

    cookies = ensure_web_cookie_fields(cookies)
    s_v_web_id = cookies.get("s_v_web_id") or ""
    uifid = _cookie_value(cookies, "UIFID")
    ms = resolve_mstoken(cookies)
    params = dict(params)
    params["webid"] = params.get("webid") or generate_fake_webid()
    if uifid:
        params["uifid"] = uifid
    if not verify_fp_after_sign:
        params["verifyFp"] = s_v_web_id
        params["fp"] = s_v_web_id
    params["msToken"] = ms

    query = urlencode(params)
    try:
        a_bogus = await sync_to_async(js_signer.get_ab, thread_sensitive=False)(query, "")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[web.profile] a_bogus 失败 err={type(e).__name__}: {e}")
        return None

    url = f"{url_base}?{query}&a_bogus={a_bogus}"
    if verify_fp_after_sign:
        encoded_fp = quote(s_v_web_id, safe="")
        url = f"{url}&verifyFp={encoded_fp}&fp={encoded_fp}"
    from core.douyin.runtime.transport.sign import secsdk_web_sign

    if secsdk_web_sign.is_protected(urlparse(url_base).path):
        url = secsdk_web_sign.sign_url(url, uifid=uifid)
    fingerprint = browser_fingerprint(user_agent)
    headers = {
        "user-agent": user_agent,
        "referer": referer,
        "accept": "application/json, text/plain, */*",
        "sec-ch-ua": fingerprint["sec_ch_ua"],
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": fingerprint["sec_ch_ua_platform"],
        "accept-language": "zh-CN,zh;q=0.9",
        "priority": "u=1, i",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "cookie": _cookie_header(dict(cookies)),
    }
    if uifid:
        headers["uifid"] = uifid
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


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


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
        "unique_id": str(user.get("unique_id") or "").strip(),
        "follower_count": _to_int(user.get("follower_count")),
        "following_count": _to_int(user.get("following_count")),
        "aweme_count": _to_int(user.get("aweme_count")),
        "total_favorited": _to_int(user.get("total_favorited")),
    }


async def fetch_profile_stats_via_douyin_web(
    cookies: Mapping[str, str],
    user_agent: str,
    sec_uid: str,
    *,
    proxy_url: Optional[str] = None,
    account_id: str = "",
) -> Optional[dict]:
    """拉取指定 sec_uid 的主页计数（粉丝/关注/作品/获赞）。

    对照 DouYin_Spider DouyinAPI.get_user_info（/aweme/v1/web/user/profile/other/）。
    需要账号自身 Cookie + a_bogus，无需 bd-ticket-guard。

    Returns:
        {nickname, avatar, sec_uid, user_id, unique_id, follower_count,
         following_count, aweme_count, total_favorited} 或 None
    """
    sec_uid = (sec_uid or "").strip()
    if not sec_uid:
        return None
    cookies = ensure_web_cookie_fields(cookies)
    ua = user_agent or (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    aid = account_id or "?"

    p_params = {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "publish_video_strategy_type": "2",
        "source": "channel_pc_web",
        "sec_user_id": sec_uid,
        "personal_center_strategy": "1",
        "profile_other_record_enable": "1",
        "land_to": "1",
    }
    p_params.update(_web_platform_params(user_agent=ua))
    p_data = await _signed_web_get(
        _WEB_PROFILE_OTHER_URL,
        p_params,
        cookies=cookies,
        user_agent=ua,
        referer=f"https://www.douyin.com/user/{quote(sec_uid, safe='')}",
        proxy_url=proxy_url,
        verify_fp_after_sign=True,
    )
    if not isinstance(p_data, dict):
        logger.warning(f"[web.profile] profile-stats 请求失败 account={aid}")
        return None
    if p_data.get("status_code", 0) != 0:
        logger.warning(
            f"[web.profile] profile-stats 业务错误 account={aid} "
            f"status_code={p_data.get('status_code')} msg={p_data.get('status_msg')!r}"
        )
        return None
    profile = _profile_from_user_info_payload(p_data)
    if profile and not profile.get("sec_uid"):
        profile["sec_uid"] = sec_uid
    return profile


def _work_from_aweme(item: dict[str, Any]) -> dict:
    """从 aweme_list 单条作品提取 UI 所需字段（对照 DouYin_Spider handle_work_info）。"""
    stats = item.get("statistics") or {}
    cover = ""
    video = item.get("video") or {}
    for key in ("cover", "origin_cover", "dynamic_cover"):
        urls = (video.get(key) or {}).get("url_list") or []
        if urls:
            cover = str(urls[0])
            break
    if not cover:
        images = item.get("images") or []
        if images:
            urls = (images[0] or {}).get("url_list") or []
            if urls:
                cover = str(urls[0])
    aweme_type = item.get("aweme_type")
    work_type = "image" if aweme_type == 68 else "video"
    aweme_id = str(item.get("aweme_id") or "")
    return {
        "aweme_id": aweme_id,
        "desc": str(item.get("desc") or ""),
        "cover": cover,
        "work_type": work_type,
        "like_count": _to_int(stats.get("digg_count")),
        "comment_count": _to_int(stats.get("comment_count")),
        "collect_count": _to_int(stats.get("collect_count")),
        "share_count": _to_int(stats.get("share_count")),
        "create_time": _to_int(item.get("create_time")),
        "share_url": f"https://www.douyin.com/video/{aweme_id}" if aweme_id else "",
    }


async def fetch_user_works_via_douyin_web(
    cookies: Mapping[str, str],
    user_agent: str,
    sec_uid: str,
    *,
    max_cursor: str = "0",
    count: int = 18,
    proxy_url: Optional[str] = None,
    account_id: str = "",
) -> Optional[dict]:
    """拉取指定 sec_uid 的作品列表（分页）。

    对照 DouYin_Spider DouyinAPI.get_user_work_info（/aweme/v1/web/aweme/post/）。

    Returns:
        {items: [...], max_cursor: str, has_more: bool} 或 None
    """
    sec_uid = (sec_uid or "").strip()
    if not sec_uid:
        return None
    cookies = ensure_web_cookie_fields(cookies)
    ua = user_agent or (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    aid = account_id or "?"
    cursor = str(max_cursor or "0")

    params = {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "sec_user_id": sec_uid,
        "max_cursor": cursor,
        "locate_query": "false",
        "show_live_replay_strategy": "1",
        "need_time_list": "1" if cursor == "0" else "0",
        "time_list_query": "0",
        "whale_cut_token": "",
        "cut_version": "1",
        "count": str(count),
        "publish_video_strategy_type": "2",
        "from_user_page": "0",
    }
    params.update(_web_platform_params(user_agent=ua))
    params["version_code"] = "290100"
    params["version_name"] = "29.1.0"
    data = await _signed_web_get(
        _WEB_AWEME_POST_URL,
        params,
        cookies=cookies,
        user_agent=ua,
        referer=f"https://www.douyin.com/user/{quote(sec_uid, safe='')}",
        proxy_url=proxy_url,
        verify_fp_after_sign=True,
    )
    if not isinstance(data, dict):
        logger.warning(f"[web.profile] works 请求失败 account={aid}")
        return None
    if data.get("status_code", 0) != 0 and "aweme_list" not in data:
        logger.warning(
            f"[web.profile] works 业务错误 account={aid} "
            f"status_code={data.get('status_code')} msg={data.get('status_msg')!r}"
        )
        return None
    aweme_list = data.get("aweme_list") or []
    items = [_work_from_aweme(it) for it in aweme_list if isinstance(it, dict)]
    return {
        "items": items,
        "max_cursor": str(data.get("max_cursor") or "0"),
        "has_more": bool(data.get("has_more")),
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
    aid = account_id or "?"

    # 1) query/user → user_uid
    q_params = _web_platform_params(user_agent=ua)
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
        p_params = {
            "device_platform": "webapp",
            "aid": "6383",
            "channel": "channel_pc_web",
            "publish_video_strategy_type": "2",
            "source": "channel_pc_web",
            "sec_user_id": sec_uid,
            "personal_center_strategy": "1",
            "profile_other_record_enable": "1",
            "land_to": "1",
        }
        p_params.update(_web_platform_params(user_agent=ua))
        p_data = await _signed_web_get(
            _WEB_PROFILE_OTHER_URL,
            p_params,
            cookies=cookies,
            user_agent=ua,
            referer=f"https://www.douyin.com/user/{quote(sec_uid, safe='')}",
            proxy_url=proxy_url,
            verify_fp_after_sign=True,
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
