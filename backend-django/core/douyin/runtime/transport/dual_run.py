#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: transport/dual_run.py
@Desc: DualRunDecorator —— Phase 3 协议路径"影子干跑"对账装饰器

目的：
    在不改变主路径行为的前提下，每次发送时**只额外编码一次** protobuf body
    （complete with client_msg_id / seq_id / endpoint / headers），把"我们以为的
    协议帧长什么样"完整 dump 到日志。配合 sniffer 抓的真实出站 IM 流量做事后对账，
    用最小成本验证协议层与平台真实格式一致。

零风险保证：
    本装饰器**绝对不会**调 signed_fetch，也不会发任何 HTTP。完全是"准备好了，
    但偏不发"的影子模式。主路径走原 transport（BrowserTransport / HttpProtocolTransport
    都行），所以两边发的次数不会变多。

典型用法：
    main = build_transport(backend='browser')          # 真发走 DOM
    if settings.DOUYIN_TRANSPORT_DUAL_RUN:
        main = DualRunDecorator(main)                  # 影子日志 + 真发
    await main.send_reply(account, page, ...)
    # 日志会同时出现：
    #   [transport.dual_run] send_reply[1] encoded body_len=89 hex=... seq_id=...
    #   [sender] ✔ 发送成功 ...                         （主路径真发的）

观察指标（建议放看板）：
    - dual_run.encode.success.total   每次成功编码的计数
    - dual_run.encode.failure.total   编码异常计数（理论上应为 0）
    - dual_run.encoded_bytes_p99      body 长度分布（异常大可能意味着错构）
    - 与 sniffer 的 send_message.body 做 byte-prefix 对账：
        前 7~9 字节（envelope cmd_id + seq_id + status_code）应永远字面一致
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, AsyncIterator, List, Optional

from core.douyin.runtime.transport.base import AccountTransport, InboundEvent
from core.douyin.runtime.transport.wire import encode_send_message_request

if TYPE_CHECKING:
    from core.douyin.douyin_account_model import DouyinAccount
    from core.douyin.douyin_rule_model import DouyinRule
    from core.douyin.runtime.inbox import ScannedMessage

logger = logging.getLogger(__name__)


# 日志 hex 截取长度（字节）。覆盖 envelope 头 + 部分业务体，足够人眼判断结构是否正确。
_HEX_DUMP_BYTES = 48


class DualRunDecorator(AccountTransport):
    """
    "影子编码 + 真路径真发"双跑装饰器。

    主路径 = inner（任意 AccountTransport），所有 verb 都透传到 inner。
    每次发送类 verb（send_text / send_reply）调用前，**额外**编码一份 protobuf
    并 log 出来；编码失败不影响主路径。

    scan_inbox / start / stop / inbound_events 透传，无副作用。
    """

    name = "dual_run"

    def __init__(self, inner: AccountTransport):
        self._inner = inner

    # ---------------- 生命周期：透传 ----------------
    async def start(self, account: "DouyinAccount") -> None:
        await self._inner.start(account)
        logger.info(
            f"[transport.dual_run] 启用 影子编码（不真发）+ 主路径={self._inner.name} "
            f"account={account.id}"
        )

    async def stop(self, account: "DouyinAccount") -> None:
        await self._inner.stop(account)

    # ---------------- 主 verbs ----------------
    async def scan_inbox(
        self,
        account: "DouyinAccount",
        *,
        max_conversations: int = 15,
        include_recent_without_unread: bool = False,
    ) -> List["ScannedMessage"]:
        # 入向不做 dual_run（响应才是难点，请求是平台主动推过来的）
        return await self._inner.scan_inbox(
            account,
            max_conversations=max_conversations,
            include_recent_without_unread=include_recent_without_unread,
        )

    async def send_text(
        self,
        account: "DouyinAccount",
        page: Any,
        *,
        conversation_id: str,
        text: str,
    ) -> str:
        # 影子：先编码 dump
        self._shadow_encode(
            account_id=str(account.id),
            conversation_id=conversation_id,
            text=text,
            tag="send_text",
        )
        # 主路径：真发
        return await self._inner.send_text(
            account, page, conversation_id=conversation_id, text=text
        )

    async def send_reply(
        self,
        account: "DouyinAccount",
        page: Any,
        *,
        conversation_id: str,
        trigger_message_id: str,
        rule: "DouyinRule",
        peer_nickname: str = "",
    ) -> str:
        # 影子：对每个 segment 编码 dump 一次。
        # 注意：_build_segments 在 send_mode='random' 下两次调用结果可能不同，
        # 但本装饰器目的是"协议格式对账"，不要求 segments 与主路径完全一致；
        # 主路径自己会再调一次 _build_segments，那里产生的 segments 才是真发的。
        try:
            from core.douyin.runtime.sender import _build_segments

            segments = _build_segments(rule, peer_nickname)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[transport.dual_run] send_reply 影子编码：渲染 segments 失败 "
                f"account={account.id} err={type(e).__name__}: {e}"
            )
            segments = []

        for i, seg in enumerate(segments):
            self._shadow_encode(
                account_id=str(account.id),
                conversation_id=conversation_id,
                text=seg,
                tag=f"send_reply[{i + 1}/{len(segments)}]",
            )

        return await self._inner.send_reply(
            account,
            page,
            conversation_id=conversation_id,
            trigger_message_id=trigger_message_id,
            rule=rule,
            peer_nickname=peer_nickname,
        )

    # ---------------- 事件流：透传 ----------------
    async def inbound_events(self) -> AsyncIterator[InboundEvent]:
        async for evt in self._inner.inbound_events():
            yield evt

    async def wait_for_inbound_signal(self, *, timeout: float) -> Optional[InboundEvent]:
        return await self._inner.wait_for_inbound_signal(timeout=timeout)

    # ---------------- 影子编码 ----------------
    def _shadow_encode(
        self,
        *,
        account_id: str,
        conversation_id: str,
        text: str,
        tag: str,
    ) -> None:
        """
        编码 SendMessageRequest 但**不**真发，把 hex / metadata 落到日志。

        失败必须吞掉，不能让影子路径影响主路径。
        """
        if not text or not text.strip() or not conversation_id:
            return  # 主路径会拒绝这种入参，影子无须编码

        try:
            body, client_msg_id, seq_id = encode_send_message_request(
                conversation_id=conversation_id,
                text=text.strip(),
            )
        except Exception as e:  # noqa: BLE001
            logger.error(
                f"[transport.dual_run] {tag} 影子编码异常 account={account_id} "
                f"err={type(e).__name__}: {e}"
            )
            return

        hex_prefix = body[:_HEX_DUMP_BYTES].hex()
        truncated = "" if len(body) <= _HEX_DUMP_BYTES else f"...(+{len(body) - _HEX_DUMP_BYTES}B)"
        preview = text[:40].replace("\n", " ")
        logger.info(
            f"[transport.dual_run] {tag} encoded account={account_id} "
            f"conv={conversation_id} body_len={len(body)} "
            f"client_msg_id={client_msg_id} seq_id={seq_id} "
            f"text_preview={preview!r} hex={hex_prefix}{truncated}"
        )
