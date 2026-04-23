#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: humanize.py
@Desc: Humanized Interaction - 拟人化交互封装

所有对页面的"写入"动作都应走这里，便于统一节流、添加抖动与调试。
"""
from __future__ import annotations

import asyncio
import logging
import random

logger = logging.getLogger(__name__)


async def random_sleep(min_s: float = 0.5, max_s: float = 1.5) -> None:
    """随机等待一段时间"""
    await asyncio.sleep(random.uniform(min_s, max_s))


async def human_type(locator, text: str, *, per_char_min: float = 0.04, per_char_max: float = 0.12) -> None:
    """
    模拟人类逐字输入：
    - 先 click 获得焦点
    - 再 press_sequentially 按字符输入，每字符 40~120ms 随机延迟
    - 每 8~14 个字符会插入一个额外 200~450ms 停顿模拟"想一下"

    tips:
      Playwright 的 `press_sequentially(delay=...)` 本身已支持固定延迟，但固定延迟过于规律。
      这里用手工循环做更真实的节奏。
    """
    if not text:
        return
    try:
        await locator.click()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[humanize] click before type failed (非致命): {e}")

    buffer_count = 0
    pause_budget = random.randint(8, 14)
    for ch in text:
        # Playwright press 不接受换行；换行用 Shift+Enter 模拟
        if ch == '\n':
            try:
                await locator.page.keyboard.press("Shift+Enter")
            except Exception:
                try:
                    await locator.press("Shift+Enter")
                except Exception:
                    pass
            await asyncio.sleep(random.uniform(0.08, 0.18))
            continue
        try:
            await locator.press(ch)
        except Exception:
            # 某些 IME 字符 press 不了，fallback 用 type
            try:
                await locator.type(ch)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[humanize] type char '{ch}' failed: {e}")
        await asyncio.sleep(random.uniform(per_char_min, per_char_max))
        buffer_count += 1
        if buffer_count >= pause_budget:
            await asyncio.sleep(random.uniform(0.2, 0.45))
            buffer_count = 0
            pause_budget = random.randint(8, 14)


async def human_click(locator) -> None:
    """
    模拟人类点击：
    - 先 hover（让 bbox 与内部元素稳定）
    - 随机延迟
    - click
    """
    try:
        await locator.hover()
    except Exception:
        pass
    await asyncio.sleep(random.uniform(0.08, 0.24))
    await locator.click()


async def human_scroll(page, distance: int = 400) -> None:
    """向下滚动一段距离，用于触发会话列表懒加载"""
    await page.mouse.wheel(0, distance + random.randint(-30, 30))
    await asyncio.sleep(random.uniform(0.3, 0.7))
