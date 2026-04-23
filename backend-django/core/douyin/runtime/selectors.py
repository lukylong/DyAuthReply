#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: selectors.py
@Desc: Douyin Creator Center DOM Selectors - 抖音创作者中心 DOM 选择器常量

说明：
  抖音 creator.douyin.com 的前端是动态生成的，class 名经常变化。
  这里把常用的选择器集中起来，便于 DOM 调整后一处改全局生效。

  每个常量都提供**多个候选选择器**（优先级从高到低），调用方用 Playwright
  的 `locator(sel).first` 逐个尝试，直到命中。
"""
from __future__ import annotations

# ---------------- 基础 URL ----------------
CREATOR_HOME = "https://creator.douyin.com/"
CREATOR_IM = "https://creator.douyin.com/creator-micro/im"
CREATOR_LOGIN_URL_HINTS = ("login", "passport")  # URL 里包含这些关键词即视为登录页

# ---------------- 登录相关 ----------------
# 二维码 canvas 或 img（二者只有一个会存在，按顺序尝试）
LOGIN_QR_SELECTORS = [
    "div.qrcode-panel canvas",       # 2024 新版
    "div[class*='qrcode'] canvas",   # 通配
    "img[class*='qrcode']",           # 旧版 img
    "canvas[class*='qrcode']",
]
# 扫码成功后会跳转到这些页面之一
LOGIN_SUCCESS_URL_HINTS = (
    "creator.douyin.com/creator-micro/home",
    "creator.douyin.com/creator-micro/content",
    "creator.douyin.com/creator-micro/data-center",
)

# ---------------- 私信相关 ----------------
# 会话列表：每个会话项
IM_CONVERSATION_ITEMS = [
    "div[class*='conversation-list'] div[class*='conversation-item']",
    "div[class*='session-list'] div[class*='session-item']",
    "li[class*='conversation']",
]
# 单个会话项内：对方昵称
IM_CONV_NICKNAME = [
    "div[class*='nickname']",
    "span[class*='nickname']",
    "p[class*='name']",
]
# 单个会话项内：最近一条消息预览
IM_CONV_LAST_MESSAGE = [
    "div[class*='last-message']",
    "div[class*='message-preview']",
    "p[class*='last']",
]
# 未读角标
IM_CONV_UNREAD_BADGE = [
    "span[class*='unread']",
    "div[class*='badge']",
]

# 打开会话后：消息列表中的消息气泡
IM_MESSAGE_BUBBLES = [
    "div[class*='message-item']",
    "div[class*='msg-item']",
    "div[class*='bubble']",
]
# 消息文本内容
IM_MESSAGE_TEXT = [
    "div[class*='text-content']",
    "span[class*='content']",
    "div[class*='msg-text']",
]
# 区分方向：本方（右侧气泡）常带 me/self/send 关键词
IM_MESSAGE_SELF_HINT = ("me", "self", "send", "own", "right", "sent")

# 当前对话窗口的输入框 + 发送按钮
IM_INPUT_BOX = [
    "div[contenteditable='true']",
    "textarea[placeholder*='请输入']",
    "textarea[placeholder*='输入']",
]
IM_SEND_BUTTON = [
    "button[class*='send-btn']",
    "div[class*='send-button']",
    "button:has-text('发送')",
]

# 顶部头像/昵称（用于 回填 account.nickname / sec_uid）
ACCOUNT_NICKNAME = [
    "div[class*='nickname']",
    "span[class*='user-name']",
    "div[class*='user-info'] span",
]
ACCOUNT_AVATAR = [
    "img[class*='avatar']",
    "div[class*='avatar'] img",
]


def first_available(page, selectors: list[str]):
    """
    在 page 上按优先级返回第一个真实存在（count > 0）的 Locator。
    找不到则返回 None。调用方应做好 None 检查。
    """
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            # Playwright 的 count() 是同步方法在 async page 下不可用 —— 这里仅返回 locator，
            # 真正的 is_visible / 等待交给调用方决定。
            return loc
        except Exception:
            continue
    return None
