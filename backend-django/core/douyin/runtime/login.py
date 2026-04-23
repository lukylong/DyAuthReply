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
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from asgiref.sync import sync_to_async
from django.utils import timezone

from core.douyin.runtime import selectors as S
from core.douyin.runtime.browser import BrowserManager
from core.douyin.runtime.ws_notify import push_to_user

if TYPE_CHECKING:
    from core.douyin.douyin_account_model import DouyinAccount

logger = logging.getLogger(__name__)


# -------------------- 工具 --------------------
def _is_login_success(url: str) -> bool:
    return any(hint in url for hint in S.LOGIN_SUCCESS_URL_HINTS)


def _is_login_page(url: str) -> bool:
    return any(hint in url for hint in S.CREATOR_LOGIN_URL_HINTS)


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
def _mark_login_failed(account_id: str, reason: str) -> Optional[str]:
    from core.douyin.douyin_account_model import DouyinAccount
    from core.douyin.douyin_event_model import DouyinEvent

    acc = DouyinAccount.objects.filter(id=account_id).first()
    if acc is None:
        return None
    acc.status = 2
    acc.save(update_fields=['status', 'sys_update_datetime'])
    DouyinEvent.objects.create(
        account=acc,
        event_type='login_expired',
        level='warning',
        title=f'账号 {acc.nickname} 登录失败',
        detail=reason,
        occurred_at=timezone.now(),
    )
    return str(acc.owner_id)


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
    owner_id = str(account.owner_id)
    account_id = str(account.id)

    context = await BrowserManager.get_or_create_context(account)
    page = await context.new_page()
    try:
        logger.info(f"[login] 打开创作者中心 account={account_id}")
        await page.goto(S.CREATOR_HOME, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)

        # Case 1: 已经登录 → 短路
        if _is_login_success(page.url):
            logger.info(f"[login] 账号已登录，跳过扫码: {page.url}")
            profile = await _collect_profile(page)
            storage = await BrowserManager.persist_storage_state(account_id)
            await _update_account_after_login(account_id, profile, storage)
            await push_to_user(owner_id, "login_success", {
                "account_id": account_id,
                "nickname": profile.get("nickname") or account.nickname,
            })
            return True

        # Case 2: 登录页 → 抓二维码
        qr_b64 = await _capture_qr_base64(page)
        if not qr_b64:
            reason = "未找到二维码元素；creator.douyin.com 可能改版或未进入登录态"
            logger.error(f"[login] {reason}")
            await _mark_login_failed(account_id, reason)
            await push_to_user(owner_id, "login_failed", {
                "account_id": account_id, "reason": reason,
            })
            return False

        logger.info(f"[login] 二维码已截取，推送前端 account={account_id}")
        await push_to_user(owner_id, "qr_image", {
            "account_id": account_id,
            "image_base64": qr_b64,
            "hint": "请使用抖音 APP 扫码登录（3 分钟内有效）",
        })

        # 每 2 秒刷新一次二维码 + 每 1 秒轮询一次 URL
        deadline = datetime.utcnow().timestamp() + timeout
        last_refresh = 0
        while datetime.utcnow().timestamp() < deadline:
            await asyncio.sleep(1.0)
            if _is_login_success(page.url):
                break
            # 每 45 秒重新截取一次二维码（抖音二维码 60 秒过期）
            now = datetime.utcnow().timestamp()
            if now - last_refresh > 45:
                new_qr = await _capture_qr_base64(page)
                if new_qr:
                    await push_to_user(owner_id, "qr_image", {
                        "account_id": account_id,
                        "image_base64": new_qr,
                        "hint": "二维码已刷新，请重新扫码",
                    })
                    last_refresh = now

        if not _is_login_success(page.url):
            reason = "扫码超时或被拒绝"
            logger.warning(f"[login] {reason} current_url={page.url}")
            await _mark_login_failed(account_id, reason)
            await push_to_user(owner_id, "login_failed", {
                "account_id": account_id, "reason": reason,
            })
            return False

        await asyncio.sleep(2)  # 等页面稳定
        profile = await _collect_profile(page)
        storage = await BrowserManager.persist_storage_state(account_id)
        await _update_account_after_login(account_id, profile, storage)
        await push_to_user(owner_id, "login_success", {
            "account_id": account_id,
            "nickname": profile.get("nickname") or account.nickname,
        })
        logger.info(f"[login] 登录成功 account={account_id} nickname={profile.get('nickname')}")
        return True

    except Exception as e:  # noqa: BLE001
        reason = f"登录异常: {type(e).__name__}: {e}"
        logger.exception(f"[login] {reason}")
        await _mark_login_failed(account_id, reason)
        await push_to_user(owner_id, "login_failed", {"account_id": account_id, "reason": reason})
        return False
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def is_login_valid(account: "DouyinAccount") -> bool:
    """打开创作者中心首页，检查是否仍处于登录态"""
    context = await BrowserManager.get_or_create_context(account)
    page = await context.new_page()
    try:
        await page.goto(S.CREATOR_HOME, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(1.5)
        return _is_login_success(page.url) or (not _is_login_page(page.url))
    except Exception:
        return False
    finally:
        try:
            await page.close()
        except Exception:
            pass
