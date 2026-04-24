#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: login.py
@Desc: Login Flow - 扫码登录流程

流程：
  1. 打开 creator.douyin.com
  2. 若已登录（URL 跳转到 home）→ 直接回填账号信息
  3. 否则定位二维码元素 → 截图 → base64 推送前端
  4. 轮询 URL，等待登录成功或超时
  5. 成功后：
       - 采集 nickname / avatar（sec_uid 从 cookie 或 URL 提取）
       - 调用 BrowserManager.persist_storage_state 加密落盘
       - 更新 DouyinAccount 字段 + 写 DouyinEvent
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

from asgiref.sync import sync_to_async
from django.utils import timezone

from core.douyin.runtime import selectors as S
from core.douyin.runtime.browser import BrowserManager
from core.douyin.runtime.ws_notify import push_to_user

if TYPE_CHECKING:
    from core.douyin.douyin_account_model import DouyinAccount

logger = logging.getLogger(__name__)

_VERIFICATION_PENDING_MINUTES = 20


# -------------------- 工具 --------------------
# 抖音登录后会下发这些 cookie 里至少一个，存在即视为已登录态（会随版本增减）
_SESSION_COOKIE_NAMES = (
    "sessionid_ss",
    "sessionid",
    "sid_guard",
    "sid_tt",
    "sid_ucp_v1",
)

# 这些 cookie 在未完全登录时也可能出现，只作为辅助观察，不用于判定登录成功
_WEAK_SESSION_COOKIE_NAMES = (
    "passport_auth_status",
    "passport_auth_status_ss",
    "passport_auth_mix_state",
    "uid_tt",
    "uid_tt_ss",
)

_VERIFICATION_HINTS = {
    "sms": (
        "短信验证",
        "短信验证码",
        "手机验证",
        "手机号验证",
        "短信校验",
        "输入短信验证码",
        "请输入短信验证码",
    ),
    "face": (
        "人脸验证",
        "刷脸",
        "人脸识别",
        "面容验证",
        "身份核验",
        "本人验证",
    ),
    "captcha": (
        "安全验证",
        "滑块",
        "拖动滑块",
        "拼图",
        "图形验证码",
        "请完成验证",
        "点击完成验证",
    ),
    "security": (
        "设备验证",
        "登录保护",
        "异常登录",
        "风险校验",
        "账号安全",
        "安全校验",
        "二次验证",
    ),
}

_VERIFICATION_KIND_LABELS = {
    "sms": "短信验证",
    "face": "人脸验证",
    "captcha": "验证码校验",
    "security": "安全验证",
    "unknown": "待人工确认",
}


def _is_login_success(url: str) -> bool:
    return any(hint in url for hint in S.LOGIN_SUCCESS_URL_HINTS)


def _is_login_page(url: str) -> bool:
    return any(hint in url for hint in S.CREATOR_LOGIN_URL_HINTS)


async def _has_session_cookie(context) -> bool:
    """检查浏览器上下文中是否存在抖音登录态 cookie。"""
    try:
        cookies = await context.cookies()
        names = {c.get("name") for c in cookies}
        return any(n in names for n in _SESSION_COOKIE_NAMES)
    except Exception:
        return False


async def _has_weak_session_cookie(context) -> bool:
    """弱登录态 cookie（仅用于触发主动跳转复检，不直接判成功）。"""
    try:
        cookies = await context.cookies()
        names = {c.get("name") for c in cookies}
        return any(n in names for n in _WEAK_SESSION_COOKIE_NAMES)
    except Exception:
        return False


async def _looks_like_login_gate(page) -> bool:
    """
    通过页面文本识别“登录门面”。
    说明：抖音有时 URL 在 creator-micro/*，但页面主体仍是扫码登录页。
    """
    try:
        text = await page.evaluate("() => (document.body?.innerText || '').slice(0, 2000)")
        text = (text or "").replace("\n", "")
        hints = ("扫码登录", "验证码登录", "登录/注册", "创作者登录", "我是创作者")
        return any(h in text for h in hints)
    except Exception:
        return False


async def _looks_like_business_shell(page) -> bool:
    """识别创作者中心业务页骨架，用于放宽登录成功判定。"""
    try:
        text = await page.evaluate("() => (document.body?.innerText || '').slice(0, 2500)")
        text = (text or "").replace("\n", "")
        hints = (
            "创作者中心",
            "创作者服务中心",
            "数据中心",
            "内容管理",
            "作品管理",
            "私信管理",
            "直播管理",
            "电商带货",
            "数据看板",
        )
        return any(h in text for h in hints)
    except Exception:
        return False


async def _is_logged_in(page) -> bool:
    """
    综合判断当前是否已登录。

    注意顺序：扫码成功后中间态 URL 常仍带 ``login`` / ``passport``（OAuth 回调），
    若先判「登录页」会直接 return False，导致永远等不到登录。必须先认 cookie 再否定。
    """
    has_strong_cookie = await _has_session_cookie(page.context)
    if has_strong_cookie:
        return not await _looks_like_login_gate(page)
    if await _looks_like_login_gate(page):
        return False
    if _is_login_success(page.url) and await _looks_like_business_shell(page):
        return True
    return False


async def _verify_im_ready(page) -> bool:
    """登录后验活：进入 IM 页面且不是登录门面，才算业务可用。"""
    try:
        await page.goto(S.CREATOR_IM, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(1.5)
        if await _looks_like_login_gate(page):
            return False
        return True
    except Exception:
        return False


async def _session_cookie_debug_names(context) -> list[str]:
    """返回当前上下文中与登录相关的 cookie 名（用于心跳日志，便于线上排查）。"""
    try:
        cookies = await context.cookies()
        names = {c.get("name") for c in cookies}
        hit = sorted(
            n for n in names if n in (_SESSION_COOKIE_NAMES + _WEAK_SESSION_COOKIE_NAMES)
        )
        return hit
    except Exception:
        return []


def _clean_text(text: str, limit: int = 200) -> str:
    cleaned = " ".join((text or "").split())
    return cleaned[:limit]


def _looks_like_login_gate_text(text: str) -> bool:
    hints = ("扫码登录", "验证码登录", "密码登录", "登录/注册", "我是创作者", "我是MCN机构")
    return any(h in (text or "") for h in hints)


def _infer_verification_kind(title: str, text: str, url: str) -> Optional[str]:
    haystack = "\n".join(filter(None, [title, text, url]))
    for kind, hints in _VERIFICATION_HINTS.items():
        if any(hint in haystack for hint in hints):
            return kind
    return None


async def _capture_page_base64(page) -> Optional[str]:
    try:
        buf = await page.screenshot(type="jpeg", quality=65, full_page=False)
        return base64.b64encode(buf).decode("ascii")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[login] 截取验证页截图失败: {e}")
        return None


async def _inspect_verification(page, *, allow_unknown: bool = False) -> Optional[dict]:
    try:
        title = await page.title()
    except Exception:
        title = ""
    try:
        body_text = await page.evaluate(
            "() => (document.body?.innerText || '').slice(0, 4000)"
        )
    except Exception:
        body_text = ""

    title = _clean_text(title, 120)
    body_text = _clean_text(body_text, 260)
    if await _looks_like_login_gate(page):
        return None
    if _looks_like_login_gate_text(body_text):
        return None
    kind = _infer_verification_kind(title, body_text, page.url)
    if kind is None and allow_unknown:
        kind = "unknown"
    if kind is None:
        return None

    screenshot_base64 = await _capture_page_base64(page)
    return {
        "kind": kind,
        "kind_label": _VERIFICATION_KIND_LABELS.get(kind, "安全验证"),
        "title": title,
        "text_excerpt": body_text,
        "current_url": page.url,
        "image_base64": screenshot_base64,
    }


async def _push_verification_required(
    owner_id: str,
    account_id: str,
    page,
    *,
    elapsed: int,
    remain: int,
    allow_unknown: bool = False,
) -> Optional[str]:
    verification = await _inspect_verification(page, allow_unknown=allow_unknown)
    if verification is None:
        return None

    hint = (
        f"检测到{verification['kind_label']}，请在浏览器窗口中手动完成；"
        "完成后保持窗口打开，系统会继续等待强登录态 cookie。"
    )
    payload = {
        "account_id": account_id,
        "elapsed": elapsed,
        "remain": remain,
        "hint": hint,
        **verification,
    }
    await _mark_pending_verification(account_id, verification["kind"])
    await push_to_user(owner_id, "verification_required", payload)
    logger.warning(
        f"[login] 发现验证页 account={account_id} kind={verification['kind']} "
        f"title={verification['title']!r} text={verification['text_excerpt']!r} "
        f"url={verification['current_url']}"
    )
    return "|".join(
        [
            verification["kind"],
            verification["title"],
            verification["text_excerpt"],
            verification["current_url"],
        ]
    )


async def _push_login_progress(
    owner_id: str,
    account_id: str,
    *,
    elapsed: int,
    remain: int,
    url: str,
    session_cookies: list[str],
    logged_in: bool,
    status_hint: Optional[str] = None,
) -> None:
    """向前端推送扫码登录进度（仅用于可视化排查）。"""
    await push_to_user(owner_id, "login_progress", {
        "account_id": account_id,
        "elapsed": elapsed,
        "remain": remain,
        "url": url,
        "session_cookies": session_cookies,
        "is_logged_in": logged_in,
        "status_hint": status_hint,
    })


# 同一账号只允许一条扫码登录协程，避免 account_loop 与 API 即时指令并发抢浏览器
_login_locks: dict[str, asyncio.Lock] = {}


def _login_lock_for(account_id: str) -> asyncio.Lock:
    lock = _login_locks.get(account_id)
    if lock is None:
        lock = asyncio.Lock()
        _login_locks[account_id] = lock
    return lock


async def _capture_qr_base64(page) -> Optional[str]:
    """从登录页截取二维码并返回 base64 字符串（不含前缀）"""
    for sel in S.LOGIN_QR_SELECTORS:
        try:
            locator = page.locator(sel).first
            count = await locator.count()
            if count == 0:
                continue
            await locator.wait_for(state="visible", timeout=5000)
            buf = await locator.screenshot()
            return base64.b64encode(buf).decode('ascii')
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[login] 尝试二维码选择器 '{sel}' 失败: {e}")
            continue
    return None


async def _collect_profile(page) -> dict:
    """尝试从页面采集当前登录账号的 nickname/avatar/sec_uid"""
    profile = {"nickname": None, "avatar": None, "sec_uid": None}

    for sel in S.ACCOUNT_NICKNAME:
        try:
            loc = page.locator(sel).first
            if await loc.count():
                txt = (await loc.inner_text()).strip()
                if txt:
                    profile["nickname"] = txt
                    break
        except Exception:
            continue

    for sel in S.ACCOUNT_AVATAR:
        try:
            loc = page.locator(sel).first
            if await loc.count():
                src = await loc.get_attribute("src")
                if src:
                    profile["avatar"] = src
                    break
        except Exception:
            continue

    # 从 cookies 中提取 sec_user_id
    try:
        cookies = await page.context.cookies()
        for c in cookies:
            if c.get("name") in ("sec_user_id", "sec_uid"):
                profile["sec_uid"] = c.get("value")
                break
    except Exception:
        pass
    # 兜底：尝试从 URL / body 中正则提取
    if not profile["sec_uid"]:
        try:
            content = await page.content()
            m = re.search(r'"sec_uid"\s*:\s*"([^"]+)"', content)
            if m:
                profile["sec_uid"] = m.group(1)
        except Exception:
            pass

    return profile


@sync_to_async
def _update_account_after_login(account_id: str, profile: dict, storage_path: Optional[str]) -> tuple[str, int]:
    """
    把采集到的信息写回 DouyinAccount（同步 ORM，用 sync_to_async 包装）。
    返回 (owner_id, 1) 便于外层推送。
    """
    from core.douyin.douyin_account_model import DouyinAccount
    from core.douyin.douyin_event_model import DouyinEvent

    acc = DouyinAccount.objects.filter(id=account_id).first()
    if acc is None:
        raise RuntimeError(f"账号不存在: {account_id}")
    update_fields = ['status', 'last_login_at', 'sys_update_datetime', 'last_heartbeat']
    acc.status = 1
    acc.last_login_at = timezone.now()
    acc.last_heartbeat = timezone.now()
    acc.pending_verification_type = None
    acc.pending_verification_at = None
    acc.pending_verification_until = None
    update_fields.extend([
        'pending_verification_type',
        'pending_verification_at',
        'pending_verification_until',
    ])
    if profile.get('nickname'):
        acc.nickname = profile['nickname']
        update_fields.append('nickname')
    if profile.get('avatar'):
        acc.avatar = profile['avatar']
        update_fields.append('avatar')
    if profile.get('sec_uid'):
        acc.sec_uid = profile['sec_uid']
        update_fields.append('sec_uid')
    if storage_path:
        acc.storage_state_path = storage_path
        update_fields.append('storage_state_path')
    acc.save(update_fields=list(set(update_fields)))

    DouyinEvent.objects.create(
        account=acc,
        event_type='login_success',
        level='info',
        title=f'账号 {acc.nickname} 登录成功',
        detail=f'sec_uid={acc.sec_uid}',
        occurred_at=timezone.now(),
    )
    return str(acc.owner_id), 1


@sync_to_async
def _mark_pending_verification(account_id: str, verification_type: str) -> Optional[str]:
    from core.douyin.douyin_account_model import DouyinAccount

    acc = DouyinAccount.objects.filter(id=account_id).first()
    if acc is None:
        return None
    now = timezone.now()
    acc.pending_verification_type = verification_type
    acc.pending_verification_at = now
    acc.pending_verification_until = now + timedelta(minutes=_VERIFICATION_PENDING_MINUTES)
    acc.save(update_fields=[
        'pending_verification_type',
        'pending_verification_at',
        'pending_verification_until',
        'sys_update_datetime',
    ])
    return str(acc.owner_id)


@sync_to_async
def _mark_login_failed(
    account_id: str,
    reason: str,
    *,
    keep_pending_verification: bool = False,
) -> Optional[str]:
    from core.douyin.douyin_account_model import DouyinAccount
    from core.douyin.douyin_event_model import DouyinEvent

    acc = DouyinAccount.objects.filter(id=account_id).first()
    if acc is None:
        return None
    acc.status = 2
    update_fields = ['status', 'sys_update_datetime']
    if not keep_pending_verification:
        acc.pending_verification_type = None
        acc.pending_verification_at = None
        acc.pending_verification_until = None
        update_fields.extend([
            'pending_verification_type',
            'pending_verification_at',
            'pending_verification_until',
        ])
    acc.save(update_fields=update_fields)
    DouyinEvent.objects.create(
        account=acc,
        event_type='login_expired',
        level='warning',
        title=f'账号 {acc.nickname} 登录失败',
        detail=reason,
        occurred_at=timezone.now(),
    )
    return str(acc.owner_id)


async def mark_account_login_invalid(
    account_id: str,
    reason: str,
    *,
    keep_pending_verification: bool = False,
) -> Optional[str]:
    """供 worker 统一将账号打回重新登录。"""
    return await _mark_login_failed(
        account_id,
        reason,
        keep_pending_verification=keep_pending_verification,
    )


async def _finalize_login_success(page, account, owner_id: str, account_id: str) -> None:
    """
    登录成功后统一处理：
      1. 若当前还停留在根路径，主动跳转到 /creator-micro/home（拿到真实用户数据）；
      2. 采集 profile；
      3. 持久化 storage_state；
      4. 更新 DB + 推送前端。
    """
    logger.info(f"[login] ▶ finalize 开始 account={account_id} url={page.url}")
    try:
        if not _is_login_success(page.url):
            logger.info(f"[login] 主动跳转创作者首页确认登录态 from={page.url}")
            await page.goto(
                "https://creator.douyin.com/creator-micro/home",
                wait_until="domcontentloaded",
                timeout=20000,
            )
            await asyncio.sleep(2)
            logger.info(f"[login] 跳转完成 url={page.url}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[login] 跳转 creator-micro/home 失败（忽略）: {e}")

    # 二次验活：必须能进入 IM 且非登录门面
    im_ready = await _verify_im_ready(page)
    logger.info(f"[login] IM 验活 account={account_id} ready={im_ready} url={page.url}")
    if not im_ready:
        raise RuntimeError("登录态验活失败：IM 页面仍为登录门面")

    # 页面还处于 loading 时再等 1 秒
    await asyncio.sleep(1)
    profile = await _collect_profile(page)
    logger.info(
        f"[login] profile 采集完成 account={account_id} "
        f"nickname={profile.get('nickname')!r} "
        f"sec_uid={profile.get('sec_uid')} "
        f"avatar={'有' if profile.get('avatar') else '无'}"
    )
    storage = await BrowserManager.persist_storage_state(account_id)
    logger.info(f"[login] storage_state 已持久化 account={account_id} path={storage}")
    await _update_account_after_login(account_id, profile, storage)
    logger.info(f"[login] DB 已更新 account={account_id} status=1")
    await push_to_user(owner_id, "login_success", {
        "account_id": account_id,
        "nickname": profile.get("nickname") or account.nickname,
    })
    logger.info(
        f"[login] ✔ 登录成功 account={account_id} nickname={profile.get('nickname')} "
        f"owner={owner_id} (已推送前端)"
    )


# -------------------- 主流程 --------------------
async def scan_qrcode_login(account: "DouyinAccount", *, timeout: int = 180) -> bool:
    """
    执行扫码登录流程。

    Args:
        account: 要登录的 DouyinAccount 实例
        timeout: 等待用户扫码并登录的最长秒数（默认 3 分钟）

    Returns:
        True  登录成功且已持久化
        False 失败 / 超时 / 取消
    """
    account_id = str(account.id)
    lock = _login_lock_for(account_id)
    if lock.locked():
        logger.warning(f"[login] 已有扫码流程在执行，拒绝重入 account={account_id}")
        return False
    async with lock:
        return await _scan_qrcode_login_locked(account, timeout=timeout)


async def _scan_qrcode_login_locked(account: "DouyinAccount", *, timeout: int) -> bool:
    """在账号互斥锁内执行扫码（避免 worker 主循环与 API 即时指令并发）。"""
    owner_id = str(account.owner_id)
    account_id = str(account.id)
    t_start = datetime.utcnow().timestamp()

    logger.info(
        f"[login] ▶ 开始扫码登录 account={account_id} nickname={account.nickname!r} "
        f"owner={owner_id} timeout={timeout}s"
    )
    context = await BrowserManager.get_or_create_context(account)
    page = await context.new_page()
    try:
        logger.info(f"[login] 打开创作者中心 account={account_id} url={S.CREATOR_HOME}")
        await page.goto(S.CREATOR_HOME, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
        logger.info(f"[login] 页面加载完成 account={account_id} current_url={page.url}")

        # Case 1: 已经登录 → 短路
        if await _is_logged_in(page):
            logger.info(f"[login] 账号已登录，跳过扫码 account={account_id} url={page.url}")
            await _finalize_login_success(page, account, owner_id, account_id)
            return True

        # Case 2: 登录页 → 抓二维码
        logger.info(f"[login] 未登录，开始截取二维码 account={account_id}")
        qr_b64 = await _capture_qr_base64(page)
        if not qr_b64:
            reason = "未找到二维码元素；creator.douyin.com 可能改版或未进入登录态"
            logger.error(f"[login] ✘ {reason} account={account_id} url={page.url}")
            await _mark_login_failed(account_id, reason)
            await push_to_user(owner_id, "login_failed", {
                "account_id": account_id, "reason": reason,
            })
            return False

        logger.info(
            f"[login] 二维码已截取（{len(qr_b64)} bytes base64），推送前端 account={account_id}"
        )
        await push_to_user(owner_id, "qr_image", {
            "account_id": account_id,
            "image_base64": qr_b64,
            "hint": "请使用抖音 APP 扫码登录（3 分钟内有效）",
        })

        # 每 45 秒刷新一次二维码 + 每 1 秒轮询一次 URL/cookie
        logger.info(f"[login] 进入轮询等待扫码 account={account_id} timeout={timeout}s")
        deadline = datetime.utcnow().timestamp() + timeout
        now_ts = datetime.utcnow().timestamp()
        last_refresh = now_ts      # 刚推送过二维码，45s 后再刷新
        last_heartbeat = now_ts    # 30s 后打第一条心跳
        last_weak_recheck = now_ts
        last_url = page.url
        last_verification_fingerprint: Optional[str] = None
        while datetime.utcnow().timestamp() < deadline:
            await asyncio.sleep(1.0)
            now = datetime.utcnow().timestamp()

            # URL 变化立刻记一条
            if page.url != last_url:
                hits = await _session_cookie_debug_names(page.context)
                logger.info(
                    f"[login] URL 变化 account={account_id} "
                    f"{last_url!r} -> {page.url!r} session_cookies={hits or '无'}"
                )
                elapsed = int(now - t_start)
                remain = int(deadline - now)
                status_hint = None
                if hits and not logged_in and await _looks_like_login_gate(page):
                    status_hint = (
                        "已扫码确认，但页面仍停留在登录门面，抖音尚未下发强登录态。"
                        "请先检查手机端是否还有继续确认步骤；若没有，通常是风控未放行。"
                    )
                await _push_login_progress(
                    owner_id,
                    account_id,
                    elapsed=elapsed,
                    remain=max(0, remain),
                    url=page.url,
                    session_cookies=hits,
                    logged_in=False,
                    status_hint=status_hint,
                )
                last_url = page.url

            if await _is_logged_in(page):
                elapsed = int(now - t_start)
                logger.info(
                    f"[login] ✔ 检测到登录态 account={account_id} "
                    f"url={page.url} elapsed={elapsed}s"
                )
                break

            # 抖音常见中间态：仅出现 weak cookie，URL 仍停留在根路径
            # 触发一次主动跳转复检，避免“扫码已确认但页面不跳”导致永远超时
            if now - last_weak_recheck >= 20 and await _has_weak_session_cookie(page.context):
                logger.info(
                    f"[login] 检测到弱登录态 cookie，主动跳转复检 account={account_id} "
                    f"url={page.url}"
                )
                try:
                    await page.goto(
                        "https://creator.douyin.com/creator-micro/home",
                        wait_until="domcontentloaded",
                        timeout=20000,
                    )
                    await asyncio.sleep(1.5)
                    logger.info(f"[login] 弱态复检跳转完成 account={account_id} url={page.url}")
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[login] 弱态复检跳转失败 account={account_id} err={e}")
                last_weak_recheck = now
                elapsed = int(now - t_start)
                remain = int(deadline - now)
                verification_fingerprint = await _push_verification_required(
                    owner_id,
                    account_id,
                    page,
                    elapsed=elapsed,
                    remain=max(0, remain),
                )
                if (
                    verification_fingerprint
                    and verification_fingerprint != last_verification_fingerprint
                ):
                    last_verification_fingerprint = verification_fingerprint

            # 每 30 秒心跳一次（便于实时观察是否卡死）
            if now - last_heartbeat >= 30:
                elapsed = int(now - t_start)
                remain = int(deadline - now)
                hits = await _session_cookie_debug_names(page.context)
                logged_in = await _is_logged_in(page)
                status_hint = None
                if hits and not logged_in and await _looks_like_login_gate(page):
                    status_hint = (
                        "已扫码确认，但页面仍停留在登录门面，抖音尚未下发强登录态。"
                        "请先检查手机端是否还有继续确认步骤；若没有，通常是风控未放行。"
                    )
                logger.info(
                    f"[login] 等待扫码中… account={account_id} "
                    f"elapsed={elapsed}s remain={remain}s url={page.url} "
                    f"session_cookies={hits or '无'} _is_logged_in={logged_in}"
                )
                await _push_login_progress(
                    owner_id,
                    account_id,
                    elapsed=elapsed,
                    remain=max(0, remain),
                    url=page.url,
                    session_cookies=hits,
                    logged_in=logged_in,
                    status_hint=status_hint,
                )
                if not logged_in and hits:
                    verification_fingerprint = await _push_verification_required(
                        owner_id,
                        account_id,
                        page,
                        elapsed=elapsed,
                        remain=max(0, remain),
                        allow_unknown=elapsed >= 30,
                    )
                    if (
                        verification_fingerprint
                        and verification_fingerprint != last_verification_fingerprint
                    ):
                        last_verification_fingerprint = verification_fingerprint
                last_heartbeat = now

            # 每 45 秒重新截取一次二维码（抖音二维码 60 秒过期）
            if now - last_refresh > 45:
                logger.info(f"[login] 二维码即将过期，刷新 account={account_id}")
                new_qr = await _capture_qr_base64(page)
                if new_qr:
                    await push_to_user(owner_id, "qr_image", {
                        "account_id": account_id,
                        "image_base64": new_qr,
                        "hint": "二维码已刷新，请重新扫码",
                    })
                    logger.info(f"[login] 二维码已刷新并推送 account={account_id}")
                # 无论刷新是否成功，都更新节流时间，避免每秒重复刷新干扰扫码状态
                last_refresh = now

        if not await _is_logged_in(page):
            try:
                cookie_names = sorted({c.get("name") for c in await page.context.cookies()})
            except Exception:
                cookie_names = []
            weak_hits = [n for n in cookie_names if n in _WEAK_SESSION_COOKIE_NAMES]
            strong_hits = [n for n in cookie_names if n in _SESSION_COOKIE_NAMES]
            if weak_hits and not strong_hits:
                verification = await _inspect_verification(page, allow_unknown=True)
                if verification:
                    reason = (
                        "扫码已确认但验证未完成"
                        f"（{verification['kind_label']}，仅弱登录态，未下发强会话）"
                    )
                else:
                    reason = "扫码已确认但被风控拦截（仅弱登录态，未下发强会话）"
            else:
                reason = "扫码超时或被拒绝"
            logger.warning(
                f"[login] ✘ {reason} account={account_id} "
                f"current_url={page.url} cookies={cookie_names[:30]}"
            )
            await _mark_login_failed(
                account_id,
                reason,
                keep_pending_verification=bool(weak_hits and not strong_hits),
            )
            await push_to_user(owner_id, "login_failed", {
                "account_id": account_id, "reason": reason,
            })
            return False

        await _finalize_login_success(page, account, owner_id, account_id)
        elapsed = int(datetime.utcnow().timestamp() - t_start)
        logger.info(f"[login] ▶ 登录流程结束 account={account_id} 总耗时={elapsed}s")
        return True

    except Exception as e:  # noqa: BLE001
        reason = f"登录异常: {type(e).__name__}: {e}"
        logger.exception(f"[login] ✘ {reason} account={account_id}")
        await _mark_login_failed(account_id, reason)
        await push_to_user(owner_id, "login_failed", {"account_id": account_id, "reason": reason})
        return False
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def is_login_valid(account: "DouyinAccount") -> bool:
    """打开创作者中心首页与 IM 页面，检查是否仍处于业务可用登录态。"""
    context = await BrowserManager.get_or_create_context(account)
    page = await context.new_page()
    try:
        await page.goto(S.CREATOR_HOME, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(1.5)
        if not await _is_logged_in(page):
            return False
        return await _verify_im_ready(page)
    except Exception:
        return False
    finally:
        try:
            await page.close()
        except Exception:
            pass
