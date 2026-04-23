#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: browser.py
@Desc: Browser Context Manager - 浏览器上下文管理
        负责 Playwright 浏览器启动/关闭、storage_state 的加解密读写、
        每个抖音账号一个独立 user_data_dir 以隔离 cookies 与指纹。

@TODO M2:
    - async def launch_context(account: DouyinAccount) -> BrowserContext
    - async def save_storage_state(account, context)
    - async def load_storage_state(account) -> dict | None
    - storage_state 使用 DOUYIN_STORAGE_ENCRYPTION_KEY（Fernet）加解密
    - 统一管理 headless / proxy / viewport / user_agent
"""


async def launch_context(account_id: str):
    """启动一个绑定到抖音账号的 Playwright 浏览器上下文（M2 实现）"""
    raise NotImplementedError("launch_context 将在 M2 里程碑实现")


async def close_context(account_id: str) -> None:
    """关闭指定账号的浏览器上下文（M2 实现）"""
    raise NotImplementedError("close_context 将在 M2 里程碑实现")
