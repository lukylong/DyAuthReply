#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: browser.py
@Desc: Browser Context Manager - 浏览器上下文管理

功能：
  - 统一管理一个 Playwright 实例下的多个 BrowserContext（每账号一个）
  - 每账号一个独立 user_data_dir，保持缓存/指纹/Service Worker 隔离
  - 支持从加密 storage_state 恢复登录态
  - 支持 headless / proxy / user_agent / viewport 的统一配置

生命周期：
    await BrowserManager.start()         # 进程启动一次
    ctx = await BrowserManager.get_or_create_context(account)
    ...  # worker 循环里使用 ctx
    await BrowserManager.close_context(account_id)   # 登出/停止该账号
    await BrowserManager.stop()          # 进程退出前
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

from django.conf import settings

from core.douyin.runtime.storage import (
    get_user_data_dir,
    load_storage_state,
    save_storage_state,
)

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Playwright
    from core.douyin.douyin_account_model import DouyinAccount

logger = logging.getLogger(__name__)

# 默认 User-Agent（Chromium 120+）
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class BrowserManager:
    """进程级单例，管理所有账号的浏览器上下文。"""

    _lock = asyncio.Lock()
    _playwright: Optional["Playwright"] = None
    _contexts: dict[str, "BrowserContext"] = {}

    # ---------------- 生命周期 ----------------
    @classmethod
    async def start(cls) -> None:
        """启动 Playwright（进程启动时调用一次）"""
        if cls._playwright is not None:
            return
        from playwright.async_api import async_playwright
        cls._playwright = await async_playwright().start()
        logger.info("[browser] Playwright 已启动")

    @classmethod
    async def stop(cls) -> None:
        """优雅关闭所有上下文与 Playwright"""
        for acc_id in list(cls._contexts.keys()):
            await cls.close_context(acc_id)
        if cls._playwright is not None:
            await cls._playwright.stop()
            cls._playwright = None
            logger.info("[browser] Playwright 已关闭")

    # ---------------- 上下文 ----------------
    @classmethod
    async def get_or_create_context(cls, account: "DouyinAccount") -> "BrowserContext":
        """
        为指定账号创建或返回已有的浏览器上下文。

        使用 launch_persistent_context：
          - 每账号独立 user_data_dir，自动持久化 cookies/storage/indexedDB
          - 同时应用环境变量中的 DOUYIN_WORKER_HEADLESS
          - 若存在加密 storage_state，则先恢复到新 context（额外兜底）
        """
        async with cls._lock:
            if str(account.id) in cls._contexts:
                return cls._contexts[str(account.id)]

            if cls._playwright is None:
                await cls.start()

            user_data_dir = get_user_data_dir(str(account.id))
            headless = getattr(settings, 'DOUYIN_WORKER_HEADLESS', False)

            launch_kwargs: dict = {
                "headless": headless,
                "user_agent": account.user_agent or _DEFAULT_UA,
                "viewport": {"width": 1280, "height": 800},
                "locale": "zh-CN",
                "timezone_id": "Asia/Shanghai",
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                ],
            }
            if account.proxy_url:
                launch_kwargs["proxy"] = {"server": account.proxy_url}

            logger.info(
                f"[browser] 启动账号上下文 account={account.id} headless={headless} "
                f"proxy={'Y' if account.proxy_url else 'N'}"
            )
            assert cls._playwright is not None
            context = await cls._playwright.chromium.launch_persistent_context(
                str(user_data_dir),
                **launch_kwargs,
            )

            # 尝试从加密 storage_state 额外恢复（防止 user_data_dir 被清）
            restored = load_storage_state(str(account.id))
            if restored and restored.get('cookies'):
                try:
                    await context.add_cookies(restored['cookies'])
                    logger.info(f"[browser] 已从加密 storage_state 恢复 {len(restored['cookies'])} 个 cookie")
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[browser] 恢复 storage_state 失败: {e}")

            # 注入反爬脚本（移除 navigator.webdriver）
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = window.chrome || { runtime: {} };
                Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
            """)

            cls._contexts[str(account.id)] = context
            return context

    @classmethod
    async def close_context(cls, account_id: str) -> None:
        """关闭指定账号的上下文"""
        ctx = cls._contexts.pop(str(account_id), None)
        if ctx is not None:
            try:
                await ctx.close()
                logger.info(f"[browser] 已关闭上下文: account={account_id}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[browser] 关闭上下文异常: {e}")

    @classmethod
    def has_context(cls, account_id: str) -> bool:
        return str(account_id) in cls._contexts

    # ---------------- 登录态持久化 ----------------
    @classmethod
    async def persist_storage_state(cls, account_id: str) -> Optional[str]:
        """
        把当前 context 的 storage_state 导出并加密落盘。
        成功返回相对路径；context 不存在返回 None。
        """
        ctx = cls._contexts.get(str(account_id))
        if ctx is None:
            return None
        state = await ctx.storage_state()
        return save_storage_state(str(account_id), state)
