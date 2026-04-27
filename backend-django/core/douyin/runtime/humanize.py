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
from contextlib import suppress

logger = logging.getLogger(__name__)


async def random_sleep(min_s: float = 0.5, max_s: float = 1.5) -> None:
    """随机等待一段时间"""
    await asyncio.sleep(random.uniform(min_s, max_s))


async def human_type(locator, text: str, *, per_char_min: float = 0.04, per_char_max: float = 0.12) -> None:
    """
    模拟人类逐字输入：
    - 先 click 获得焦点
    - 再按“分行 + 逐段输入”写入内容，避免 URL / 标点在 contenteditable 中丢失
    - 每段之间插入短暂停顿，模拟人工输入节奏

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

    lines = text.split('\n')
    for idx, line in enumerate(lines):
        if idx > 0:
            try:
                await locator.page.keyboard.press("Shift+Enter")
            except Exception:
                try:
                    await locator.press("Shift+Enter")
                except Exception:
                    pass
            await asyncio.sleep(random.uniform(0.08, 0.18))
        if not line:
            continue
        try:
            await locator.type(
                line,
                delay=random.uniform(per_char_min * 1000, per_char_max * 1000),
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[humanize] line type failed, fallback insert_text: {e}")
            try:
                await locator.page.keyboard.insert_text(line)
            except Exception as inner_e:  # noqa: BLE001
                logger.debug(f"[humanize] insert_text failed: {inner_e}")
        await asyncio.sleep(random.uniform(0.15, 0.35))


async def human_click(locator) -> None:
    """
    模拟人类点击：
    - 先 hover（让 bbox 与内部元素稳定）
    - 尝试滚动到可视区域
    - 随机延迟
    - click
    """
    last_error: Exception | None = None
    with suppress(Exception):
        await locator.scroll_into_view_if_needed(timeout=5000)
    with suppress(Exception):
        await locator.wait_for(state="visible", timeout=5000)
    try:
        await locator.hover()
    except Exception:
        pass
    await asyncio.sleep(random.uniform(0.08, 0.24))
    for kwargs in ({}, {"force": True}):
        try:
            await locator.click(timeout=8000, **kwargs)
            return
        except Exception as e:  # noqa: BLE001
            last_error = e
            logger.debug(f"[humanize] click failed kwargs={kwargs}: {e}")
    try:
        await locator.evaluate("(el) => el.click()")
        return
    except Exception as e:  # noqa: BLE001
        last_error = e
        logger.debug(f"[humanize] js click failed: {e}")
    if last_error is not None:
        raise last_error


async def human_scroll(page, distance: int = 400) -> None:
    """向下滚动一段距离，用于触发会话列表懒加载"""
    await page.mouse.wheel(0, distance + random.randint(-30, 30))
    await asyncio.sleep(random.uniform(0.3, 0.7))
