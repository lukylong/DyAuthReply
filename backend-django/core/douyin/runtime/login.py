#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: login.py
@Desc: Login Flow - 扫码登录流程
        首次为账号建立 storage_state：打开 creator.douyin.com，
        截图二维码 → 推送给前端 → 用户扫码 → 检测登录成功 → 保存加密 storage_state。

@TODO M2:
    - async def scan_qrcode_login(account: DouyinAccount) -> bool
        1. 打开 https://creator.douyin.com/
        2. 定位登录弹窗中的二维码 <canvas> 或 <img>
        3. 截图/提取 base64 → channel_layer.group_send 推送到 douyin_user_{user_id}
        4. 轮询 URL/DOM 判断登录成功
        5. 调用 browser.save_storage_state(account, context)
        6. 回填 account.sec_uid / nickname / avatar / last_login_at / status=1
    - 处理异常：二维码过期、扫码被拒、账号风控等
"""


async def scan_qrcode_login(account_id: str) -> bool:
    """
    执行扫码登录流程。
    返回 True 表示登录成功并已持久化 storage_state；False 表示失败/取消。
    """
    raise NotImplementedError("scan_qrcode_login 将在 M2 里程碑实现")


async def is_login_valid(account_id: str) -> bool:
    """校验当前 storage_state 是否仍有效（打开创作者中心是否跳转登录）"""
    raise NotImplementedError("is_login_valid 将在 M2 里程碑实现")
