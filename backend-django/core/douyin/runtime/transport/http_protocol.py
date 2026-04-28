#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: transport/http_protocol.py
@Desc: HttpProtocolTransport —— Phase 3 hybrid 协议化 transport（骨架）

设计：
  - 每账号一份；持有自己的 SignProvider（浏览器签名服务）和共享 httpx 客户端
  - scan_inbox / send_reply / send_text 三个 verb 走 HTTP 协议路径
  - 当协议路径**不可用**或**未实现**时，自动 fallback 到内嵌的 BrowserTransport
    （即 DOM 兜底）；这样灰度切换风险可控

当前版本（Phase 3.2a 骨架）：
  - 所有 verb 默认走 fallback；HTTP 实现由 _impl_*_via_http 占位
  - 等 sniff 报告把接口字段填进 _ENDPOINTS / _impl_* 后即可逐个开启
  - 对 worker 的可见行为：与 BrowserTransport 完全等价（零回归风险）

Phase 3.2b 将逐个 verb 切：
  send_text  → 第一个切（没有副作用范围最小，最容易回滚）
  send_reply → 第二个切（仍然是出向）
  scan_inbox → 最后切（涉及历史拉取 + 落库幂等，最复杂）
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, List, Optional

from core.douyin.runtime.sender import (
    _build_segments,
    _record_auto_outbound_message,
    _write_reply_log,
    write_manual_out_message,
)
from core.douyin.runtime.transport.base import AccountTransport
from core.douyin.runtime.transport.browser import BrowserTransport
from core.douyin.runtime.transport.sign_provider import (
    SignedResponse,
    SignerUnavailable,
    SignProvider,
)
from core.douyin.runtime.transport.wire import (
    SendMessageResult,
    decode_send_message_response,
    encode_send_message_request,
)

if TYPE_CHECKING:
    from core.douyin.douyin_account_model import DouyinAccount
    from core.douyin.douyin_rule_model import DouyinRule
    from core.douyin.runtime.inbox import ScannedMessage

logger = logging.getLogger(__name__)


# 协议接口表（Phase 3.0 sniff 报告确认）
# 来源：im_protocol_map.md §HTTP 请求统计 + §关键 Endpoint 请求/响应样本
_ENDPOINTS: dict[str, dict[str, str]] = {
    # 发消息（创作者中心私信）
    "send_message": {
        "method": "POST",
        "url": "https://imapi.douyin.com/v1/message/send",
        "content_type": "application/x-protobuf",
    },
    # 拉某会话的历史消息
    "get_by_conversation": {
        "method": "POST",
        "url": "https://imapi.douyin.com/v1/message/get_by_conversation",
        "content_type": "application/x-protobuf",
    },
    # 会话列表（陌生人 / 私信入口）
    "list_conversations": {
        "method": "POST",
        "url": "https://imapi.snssdk.com/v1/stranger/get_conversation_list",
        "content_type": "application/x-protobuf",
    },
}


# 抖音 IM HTTP 接口共用 headers
_BASE_IM_HEADERS: dict[str, str] = {
    "content-type": "application/x-protobuf",
    "referer": "https://creator.douyin.com/",
    "accept": "*/*",
}


class HttpProtocolTransport(AccountTransport):
    """
    Hybrid HTTP 协议 transport：浏览器只做签名 + cookie 维护，
    业务流量走 Python httpx；不可用时自动 fallback 到 BrowserTransport。

    特性：
      - 完全实现 AccountTransport 契约（worker 可以无缝替换）
      - 失败/未实现自动 fallback（灰度安全）
      - 失败 streak > N 时主动停用本 transport，让 worker 切回 browser
    """

    name = "http_protocol"

    def __init__(
        self,
        *,
        sign_provider: Optional[SignProvider] = None,
        fallback: Optional[AccountTransport] = None,
        max_signer_failures: int = 5,
        send_text_enabled: Optional[bool] = None,
        send_reply_enabled: Optional[bool] = None,
        scan_inbox_enabled: Optional[bool] = None,
    ) -> None:
        self._sign = sign_provider or SignProvider()
        # fallback 默认就是 BrowserTransport —— 它已经被 worker / sender 验证过
        self._fallback: AccountTransport = fallback or BrowserTransport()
        self._max_signer_failures = int(max_signer_failures)
        self._signer_failures = 0

        # verb 级开关 —— Phase 3.2b 逐个翻 True
        # 优先级：构造参数 > Django setting > 默认 False
        self._http_send_text_enabled = self._resolve_flag(
            send_text_enabled, "DOUYIN_HTTP_PROTOCOL_SEND_TEXT"
        )
        self._http_send_reply_enabled = self._resolve_flag(
            send_reply_enabled, "DOUYIN_HTTP_PROTOCOL_SEND_REPLY"
        )
        self._http_scan_inbox_enabled = self._resolve_flag(
            scan_inbox_enabled, "DOUYIN_HTTP_PROTOCOL_SCAN_INBOX"
        )

    @staticmethod
    def _resolve_flag(explicit: Optional[bool], setting_name: str) -> bool:
        """构造参数 > Django setting > False"""
        if explicit is not None:
            return bool(explicit)
        try:
            from django.conf import settings as _s
            return bool(getattr(_s, setting_name, False))
        except Exception:  # noqa: BLE001
            return False

    # ---------------- 生命周期 ----------------
    async def start(self, account: "DouyinAccount") -> None:
        # 同时启动 fallback（确保 BrowserContext 在）和 SignProvider（signer page）
        try:
            await self._fallback.start(account)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[transport.http] fallback.start 异常 account={account.id} err={e}"
            )
        try:
            await self._sign.start(account)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[transport.http] SignProvider.start 异常 account={account.id} err={e}"
            )
        logger.info(
            f"[transport.http] HttpProtocolTransport 就绪 account={account.id} "
            f"signer_ready={self._sign.is_ready} "
            f"flags=(send_text={self._http_send_text_enabled},"
            f"send_reply={self._http_send_reply_enabled},"
            f"scan={self._http_scan_inbox_enabled})"
        )

    async def stop(self, account: "DouyinAccount") -> None:
        with_errs = []
        for label, awaitable in (
            ("sign", self._sign.stop(account)),
            ("fallback", self._fallback.stop(account)),
        ):
            try:
                await awaitable
            except Exception as e:  # noqa: BLE001
                with_errs.append(f"{label}={type(e).__name__}")
        if with_errs:
            logger.warning(f"[transport.http] stop 部分失败 account={account.id} errs={with_errs}")

    # ---------------- 主 verbs ----------------
    async def scan_inbox(
        self,
        account: "DouyinAccount",
        *,
        max_conversations: int = 15,
        include_recent_without_unread: bool = False,
    ) -> List["ScannedMessage"]:
        if self._http_scan_inbox_enabled and self._signer_healthy():
            try:
                return await self._impl_scan_inbox_via_http(
                    account,
                    max_conversations=max_conversations,
                    include_recent_without_unread=include_recent_without_unread,
                )
            except _NotImplementedYet:
                pass
            except SignerUnavailable as e:
                self._on_signer_failure(f"scan_inbox: {e}")
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"[transport.http] scan_inbox 协议路径异常，fallback browser "
                    f"account={account.id} err={type(e).__name__}: {e}"
                )
        return await self._fallback.scan_inbox(
            account,
            max_conversations=max_conversations,
            include_recent_without_unread=include_recent_without_unread,
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
        if self._http_send_reply_enabled and self._signer_healthy():
            try:
                return await self._impl_send_reply_via_http(
                    account,
                    conversation_id=conversation_id,
                    trigger_message_id=trigger_message_id,
                    rule=rule,
                    peer_nickname=peer_nickname,
                )
            except _NotImplementedYet:
                pass
            except SignerUnavailable as e:
                self._on_signer_failure(f"send_reply: {e}")
            except ValueError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"[transport.http] send_reply 协议路径异常，fallback browser "
                    f"account={account.id} err={type(e).__name__}: {e}"
                )
        return await self._fallback.send_reply(
            account,
            page,
            conversation_id=conversation_id,
            trigger_message_id=trigger_message_id,
            rule=rule,
            peer_nickname=peer_nickname,
        )

    async def send_text(
        self,
        account: "DouyinAccount",
        page: Any,
        *,
        conversation_id: str,
        text: str,
    ) -> str:
        if self._http_send_text_enabled and self._signer_healthy():
            try:
                return await self._impl_send_text_via_http(
                    account,
                    conversation_id=conversation_id,
                    text=text,
                )
            except _NotImplementedYet:
                pass
            except SignerUnavailable as e:
                self._on_signer_failure(f"send_text: {e}")
            except ValueError:
                # 调用方传错参数（空 text / 空 conv），透传——fallback 也救不了
                raise
            except Exception as e:  # noqa: BLE001
                # HTTP 失败 / 解码失败 / 协议状态非 0 —— 主动降级到 browser
                # 双发风险靠 worker 的 _recent_outbound_texts 90s 去重兜住
                logger.warning(
                    f"[transport.http] send_text 协议路径异常，fallback browser "
                    f"account={account.id} err={type(e).__name__}: {e}"
                )
        return await self._fallback.send_text(
            account,
            page,
            conversation_id=conversation_id,
            text=text,
        )

    # ---------------- 内部：信号 / 健康 ----------------
    def _signer_healthy(self) -> bool:
        if not self._sign.is_ready:
            return False
        if self._signer_failures >= self._max_signer_failures:
            return False
        return True

    def _on_signer_failure(self, reason: str) -> None:
        self._signer_failures += 1
        logger.warning(
            f"[transport.http] signer 失败计数 {self._signer_failures}/"
            f"{self._max_signer_failures} reason={reason}"
        )
        if self._signer_failures >= self._max_signer_failures:
            logger.error(
                "[transport.http] signer 失败次数到达阈值，本 transport 进入降级模式（仅走 fallback）"
            )

    # ---------------- 协议路径实现 (Phase 3.2b) ----------------
    async def _post_send_message(
        self,
        account: "DouyinAccount",
        conversation_id: str,
        text: str,
        *,
        log_tag: str = "send",
    ) -> tuple[SendMessageResult, str]:
        """
        协议层"纯发送"：编码 → signed_fetch → 解响应 → 校验。
        **不落库**，由调用方决定写哪种 DouyinMessage / 是否写 DouyinReplyLog。

        Returns:
            (result, client_msg_id)：成功时返回；失败抛异常。

        Raises:
            ValueError: 入参非法（空 text / 空 conv）
            SignerUnavailable: 浏览器签名机不可用（上层 fallback）
            RuntimeError: HTTP 非 2xx / 解码失败 / 协议层 status_code != 0（上层 fallback）
        """
        normalized = (text or "").strip()
        if not normalized:
            raise ValueError(f"{log_tag} 不能发送空文本")
        if not conversation_id:
            raise ValueError(f"{log_tag} 需要 conversation_id")

        endpoint = _ENDPOINTS["send_message"]
        body, client_msg_id, seq_id = encode_send_message_request(
            conversation_id=conversation_id,
            text=normalized,
        )

        logger.info(
            f"[transport.http] {log_tag} → POST {endpoint['url']} "
            f"account={account.id} conv={conversation_id} "
            f"client_msg_id={client_msg_id} seq_id={seq_id} body_len={len(body)}"
        )

        resp: SignedResponse = await self._sign.signed_fetch(
            method=endpoint["method"],
            url=endpoint["url"],
            body=body,
            headers=_BASE_IM_HEADERS,
        )

        if not resp.ok:
            logger.warning(
                f"[transport.http] {log_tag} HTTP 非 2xx account={account.id} "
                f"status={resp.status} text_preview={resp.text[:200]!r}"
            )
            raise RuntimeError(f"{log_tag} http status={resp.status}")

        if not resp.content:
            logger.warning(
                f"[transport.http] {log_tag} 响应 body 为空 account={account.id}"
            )
            raise RuntimeError(f"{log_tag} 响应 body 为空")

        try:
            result = decode_send_message_response(resp.content)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[transport.http] {log_tag} 解码响应失败 account={account.id} "
                f"err={type(e).__name__}: {e} content_len={len(resp.content)}"
            )
            raise RuntimeError(f"{log_tag} decode failed: {e}") from e

        if result.status_code != 0:
            logger.warning(
                f"[transport.http] {log_tag} 协议层失败 account={account.id} "
                f"status_code={result.status_code} status_msg={result.status_msg!r} "
                f"server_msg_id={result.server_msg_id} client_msg_id={result.client_msg_id}"
            )
            raise RuntimeError(
                f"{log_tag} protocol status={result.status_code} "
                f"msg={result.status_msg!r}"
            )

        if result.client_msg_id and result.client_msg_id != client_msg_id:
            logger.warning(
                f"[transport.http] {log_tag} client_msg_id 不一致 "
                f"sent={client_msg_id} echo={result.client_msg_id}"
            )

        logger.info(
            f"[transport.http] {log_tag} 成功 account={account.id} "
            f"server_msg_id={result.server_msg_id} client_msg_id={client_msg_id}"
        )
        return result, client_msg_id

    async def _impl_send_text_via_http(
        self,
        account: "DouyinAccount",
        *,
        conversation_id: str,
        text: str,
    ) -> str:
        """
        手动发送文本（创作者中心 IM 文本框入口）。
        失败时**不**自己落库—— fallback 会让 BrowserTransport.send_text 决定。
        """
        normalized = (text or "").strip()
        await self._post_send_message(
            account, conversation_id, normalized, log_tag="send_text"
        )
        # 落 DouyinMessage(direction='out', external_msg_id=manual_out_*)
        msg_id = await write_manual_out_message(
            str(account.id), conversation_id, normalized
        )
        return msg_id

    async def _impl_send_reply_via_http(
        self,
        account: "DouyinAccount",
        *,
        conversation_id: str,
        trigger_message_id: str,
        rule: "DouyinRule",
        peer_nickname: str,
    ) -> str:
        """
        自动回复：复用 sender._build_segments / _record_auto_outbound_message /
        _write_reply_log，把"分段发送"由 DOM 改为 HTTP 协议。

        失败语义：
          - 任何段发送失败 → 立刻停止后续段、写 result='failed' 的 reply log，
            然后**抛 RuntimeError**让上层 fallback 走 BrowserTransport
          - 上层 catch 到异常后会调 BrowserTransport.send_reply，BrowserTransport
            会**重新跑一遍** _build_segments + _send_one + _write_reply_log。
            所以这里抛异常前**不**写 'failed' log，否则一次失败会落两条 log
          - "段间已发出"的部分：worker 的 _recent_outbound_texts 90s 去重 +
            DouyinMessage 的 get_or_create(external_msg_id) 会兜住重复
        """
        import asyncio
        import random
        from datetime import datetime

        account_id = str(account.id)
        rule_id = str(rule.id)
        t0 = datetime.utcnow().timestamp()

        segments = _build_segments(rule, peer_nickname)
        logger.info(
            f"[transport.http] send_reply 开始 account={account_id} peer={peer_nickname!r} "
            f"rule={rule.name!r} send_mode={getattr(rule, 'send_mode', '?')} "
            f"segments={len(segments)} conv={conversation_id}"
        )

        if not segments:
            logger.warning(
                f"[transport.http] send_reply 渲染结果为空，跳过 account={account_id} "
                f"rule={rule.name!r}"
            )
            return await _write_reply_log(
                account_id=account_id,
                conversation_id=conversation_id,
                trigger_message_id=trigger_message_id,
                rule_id=rule_id,
                text='',
                links=[],
                result='skipped',
                error_message='规则渲染结果为空',
            )

        sent_count = 0
        for i, seg in enumerate(segments):
            preview = seg[:40].replace('\n', ' ')
            logger.info(
                f"[transport.http] send_reply 发送段 {i + 1}/{len(segments)} "
                f"account={account_id} len={len(seg)} preview={preview!r}"
            )
            # 协议层发送；任何失败让 _post_send_message 抛 → 上层 fallback
            await self._post_send_message(
                account, conversation_id, seg, log_tag=f"send_reply[{i + 1}]"
            )
            sent_count += 1
            # 每段发送成功后立刻记 outbound message（让 echo_blacklist 命中）
            try:
                await _record_auto_outbound_message(
                    account_id=account_id,
                    conversation_id=conversation_id,
                    text=seg,
                    rule_id=rule_id,
                )
            except Exception as e:  # noqa: BLE001
                # 落库失败不影响主流程；下一轮 scan_inbox 还有 _recent_outbound_texts 兜底
                logger.warning(
                    f"[transport.http] send_reply 落 outbound DouyinMessage 失败（不致命） "
                    f"account={account_id} conv={conversation_id} err={e}"
                )
            # 段间随机间隔，与 BrowserTransport 行为对齐
            if i < len(segments) - 1:
                gap = random.uniform(1.0, 3.0)
                logger.debug(f"[transport.http] send_reply 段间等待 {gap:.2f}s")
                await asyncio.sleep(gap)

        duration = int((datetime.utcnow().timestamp() - t0) * 1000)
        tpl = getattr(rule, 'template', None)
        links_payload = (tpl.links if tpl else getattr(rule, 'links', None)) or []

        log_id = await _write_reply_log(
            account_id=account_id,
            conversation_id=conversation_id,
            trigger_message_id=trigger_message_id,
            rule_id=rule_id,
            text=segments[0],
            links=links_payload,
            result='success',
            duration_ms=duration,
        )
        logger.info(
            f"[transport.http] send_reply 成功 account={account_id} peer={peer_nickname!r} "
            f"segments={sent_count}/{len(segments)} duration_ms={duration} reply_log={log_id}"
        )
        return log_id

    async def _impl_scan_inbox_via_http(
        self,
        account: "DouyinAccount",
        *,
        max_conversations: int,
        include_recent_without_unread: bool,
    ) -> List["ScannedMessage"]:
        """
        Phase 3.2b 决策：**不实现，永远 fallback 到 BrowserTransport.scan_inbox**

        理由：
          1. Phase 2 的 WsInboundDecorator 已经把入向消息延时降到秒级
             （WS 帧到达 → 立即唤醒 scan_inbox），延时不是瓶颈
          2. list_conversations / get_by_conversation 的响应是巨型 protobuf
             （sniff 报告里 get_by_conversation 一次 127KB），字段映射在没有
             完整 .proto 的情况下无法 100% 准确。一旦解错字段，**入向消息会
             整批错落库 / 错触发回复 / 错 mark_read** —— 风险远大于发送侧
          3. 出向（send_text / send_reply）已经协议化，浏览器只剩"扫 DOM
             读消息"这一个职责，资源占用并没本质增加
          4. 真要协议化 scan_inbox，需要先有"双跑差异检测"机制（同时跑
             HTTP 和 DOM，对比落库结果），ROI 不在 Phase 3 范围内

        如果将来要做（Phase 4+）：
          1. signed_fetch list_conversations → 拿到 conversation_list + 未读计数
          2. 对每个未读 conversation: signed_fetch get_by_conversation
          3. 解析新增消息、direction 判定 (peer_sec_uid != account.sec_uid)
          4. 落 DouyinConversation / DouyinMessage
          5. 返回 List[ScannedMessage]

        不变量（实现时必须满足，与 BrowserTransport.scan_inbox 共享）：
          - DouyinMessage.external_msg_id 必须从 server_msg_id 哈希出稳定值
          - direction='in' 才进 ScannedMessage
          - 自己刚发出的消息（90s 内 _recent_outbound_texts）不能误为入向
        """
        raise _NotImplementedYet(
            "scan_inbox via http 暂不实现（Phase 3.2b 决策：fallback browser）"
        )


class _NotImplementedYet(RuntimeError):
    """显式标记：协议路径还没开启，应该 fallback。"""
