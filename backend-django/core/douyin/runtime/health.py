#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: runtime/health.py
@Desc: 抖音凭证健康治理 —— 主动探活 + 失效语义化分类（纯 HTTP 协议，无浏览器）。

职责：
  1. classify_signed_response：把一次已签名请求的 HTTP/协议层状态码统一判定为
     valid / login_expired / inconclusive。worker 的 scan/send 失败处理与 scheduler
     的主动探活共用同一套判定，避免"有的地方判失效有的地方判未知异常"。
  2. probe_account_credential：对单个账号做一次轻量只读探活（get_by_user limit=1），
     判定 cookie 是否仍有效，并结合 storage 里的 bd-ticket 推断"可发送/仅接收"。
  3. run_credential_probe：scheduler 入口（同步函数），批量探活在线账号，失效则打回
     登录态并 WS 推送，正常则刷新 credential_state / last_probe_at。

设计取向：保守。只有出现**明确**的失效信号（HTTP 401/403 或运维显式配置的协议状态码）
才把账号判为 login_expired；网络抖动 / 5xx / 未知协议错误一律 inconclusive，绝不误伤
正常账号（误判会把好号打成失效、停止托管，代价高）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# 探活判定结果
PROBE_VALID = "valid"
PROBE_LOGIN_EXPIRED = "login_expired"
PROBE_INCONCLUSIVE = "inconclusive"

# 凭证能力分级（与 DouyinAccount.CREDENTIAL_STATE_CHOICES 对齐）
CRED_UNKNOWN = "unknown"
CRED_SENDABLE = "sendable"
CRED_RECEIVE_ONLY = "receive_only"
CRED_INVALID = "invalid"

# 默认判失效的 HTTP 状态码（强信号）。可被 settings.DOUYIN_AUTH_EXPIRED_HTTP_STATUS 覆盖。
_DEFAULT_EXPIRED_HTTP_STATUS = (401, 403)


@dataclass
class ProbeResult:
    """单次探活结果。"""
    status: str  # PROBE_VALID / PROBE_LOGIN_EXPIRED / PROBE_INCONCLUSIVE
    http_status: Optional[int] = None
    proto_status_code: Optional[int] = None
    detail: str = ""


def _expired_http_status() -> frozenset[int]:
    try:
        from django.conf import settings
        raw = getattr(settings, "DOUYIN_AUTH_EXPIRED_HTTP_STATUS", None)
        if raw:
            return frozenset(int(x) for x in raw)
    except Exception:  # noqa: BLE001
        pass
    return frozenset(_DEFAULT_EXPIRED_HTTP_STATUS)


def _expired_proto_codes() -> frozenset[int]:
    """协议层（imapi 返回 BaseResp.status_code）判失效的码集合。

    抖音不同接口的"未登录/会话失效"status_code 取值并不统一，且误伤代价高，故默认空集，
    只靠 HTTP 401/403 这类强信号判失效。运维抓到真实失效码后，可通过
    settings.DOUYIN_AUTH_EXPIRED_STATUS_CODES = [xxx, ...] 显式扩展。
    """
    try:
        from django.conf import settings
        raw = getattr(settings, "DOUYIN_AUTH_EXPIRED_STATUS_CODES", None)
        if raw:
            return frozenset(int(x) for x in raw)
    except Exception:  # noqa: BLE001
        pass
    return frozenset()


# 协议层 status_msg 命中以下任一关键字（不区分大小写、子串匹配）→ 判失效。
# 实测：get_by_user 在 bd-ticket / 登录态失效时返回 status_code=1、
# status_msg='unexepcted session length'（抖音侧拼写带 typo）。这是个**可复现的强信号**：
# 同账号重新导入新鲜 cookie+bd-ticket 后立即恢复 status_code=0。仅凭 status_code 区分会误伤
# （status_code=1 在别处也用作通用错误），故按 message 关键字精确命中，避免假阳性。
# 运维可用 settings.DOUYIN_AUTH_EXPIRED_STATUS_MESSAGES = ["...", ...] 覆盖/扩展。
_DEFAULT_EXPIRED_PROTO_MSG_KEYWORDS = (
    "unexepcted session length",   # 抖音返回的原始（带 typo）文案
    "unexpected session length",   # 正确拼写，防止抖音后续改正
)


def _expired_proto_messages() -> tuple[str, ...]:
    try:
        from django.conf import settings
        raw = getattr(settings, "DOUYIN_AUTH_EXPIRED_STATUS_MESSAGES", None)
        if raw:
            return tuple(str(x).strip().lower() for x in raw if str(x).strip())
    except Exception:  # noqa: BLE001
        pass
    return _DEFAULT_EXPIRED_PROTO_MSG_KEYWORDS


def _proto_msg_is_expired(proto_status_msg: Optional[str]) -> bool:
    if not proto_status_msg:
        return False
    low = proto_status_msg.lower()
    return any(kw in low for kw in _expired_proto_messages())


def classify_signed_response(
    http_status: Optional[int],
    proto_status_code: Optional[int],
    proto_status_msg: Optional[str] = None,
) -> str:
    """把一次已签名请求的状态统一判定为 valid / login_expired / inconclusive。

    判定优先级（保守）：
      1. HTTP 命中失效码集合（默认 401/403）→ login_expired
      2. HTTP 5xx / 无 HTTP 状态 → inconclusive（服务端/网络问题，不判失效）
      3. 协议层 status_code 命中显式配置的失效码 → login_expired
      4. 协议层 status_msg 命中失效关键字（默认 'unexpected session length'）→ login_expired
      5. 协议层 status_code 非 0（其它）→ inconclusive（业务错误，不一定是登录失效）
      6. 其余（HTTP 2xx 且协议层 0/未知）→ valid
    """
    if http_status is not None:
        if http_status in _expired_http_status():
            return PROBE_LOGIN_EXPIRED
        if http_status >= 500:
            return PROBE_INCONCLUSIVE
        if http_status < 200 or http_status >= 300:
            # 其它非 2xx（如 429 限流、451 风控）：不判登录失效，交给重试/限流处理
            return PROBE_INCONCLUSIVE

    if proto_status_code is not None and proto_status_code != 0:
        if proto_status_code in _expired_proto_codes():
            return PROBE_LOGIN_EXPIRED
        if _proto_msg_is_expired(proto_status_msg):
            return PROBE_LOGIN_EXPIRED
        return PROBE_INCONCLUSIVE

    return PROBE_VALID


# ──────────────────────── 单账号探活 ────────────────────────


async def probe_account_credential(account) -> ProbeResult:
    """对单个账号做一次轻量只读探活，判定 cookie 是否仍有效。

    用 get_by_user(limit=1, cursor=now) 只验证登录态、不拉历史、不落库、不动 worker cursor。
    """
    from core.douyin.runtime.transport.http_protocol import HttpProtocolTransport

    transport = HttpProtocolTransport()
    try:
        await transport.start(account)
        return await transport.probe_credential(account)
    except Exception as e:  # noqa: BLE001
        return ProbeResult(status=PROBE_INCONCLUSIVE, detail=f"探活异常: {type(e).__name__}: {e}")
    finally:
        try:
            await transport.stop(account)
        except Exception:  # noqa: BLE001
            pass


def _resolve_credential_state(probe_status: str, has_send: bool) -> str:
    if probe_status == PROBE_LOGIN_EXPIRED:
        return CRED_INVALID
    if probe_status == PROBE_VALID:
        return CRED_SENDABLE if has_send else CRED_RECEIVE_ONLY
    return CRED_UNKNOWN  # inconclusive：不改判


# ──────────────────────── 批量探活（scheduler 入口） ────────────────────────


def run_credential_probe() -> dict:
    """scheduler 入口（同步）：批量探活在线账号。

    任务代码：scheduler.tasks.douyin_probe_credentials
    建议 cron：每 10~30 分钟一次（*/15 * * * *）。
    """
    try:
        from django.conf import settings
        if not getattr(settings, "DOUYIN_PROBE_ENABLED", True):
            return {"success": True, "message": "探活已禁用（DOUYIN_PROBE_ENABLED=False）"}
    except Exception:  # noqa: BLE001
        pass
    try:
        return asyncio.run(_probe_all_async())
    except Exception as e:  # noqa: BLE001
        logger.error(f"[probe] 凭证探活任务异常: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


async def _probe_all_async() -> dict:
    from asgiref.sync import sync_to_async
    from django.conf import settings

    accounts = await sync_to_async(_load_online_accounts)()
    if not accounts:
        return {"success": True, "message": "无在线账号需探活", "checked": 0}

    concurrency = int(getattr(settings, "DOUYIN_PROBE_CONCURRENCY", 5) or 5)
    sem = asyncio.Semaphore(max(1, concurrency))

    stats = {"checked": 0, "valid": 0, "login_expired": 0, "inconclusive": 0}

    async def _one(acc) -> None:
        async with sem:
            await _probe_and_apply(acc, stats)

    await asyncio.gather(*[_one(a) for a in accounts], return_exceptions=True)
    logger.info(
        f"[probe] 探活完成 checked={stats['checked']} valid={stats['valid']} "
        f"expired={stats['login_expired']} inconclusive={stats['inconclusive']}"
    )
    return {"success": True, **stats}


def _load_online_accounts() -> list:
    """加载需要探活的账号：在线(status=1) 且不在人工验证冷却中。"""
    from django.utils import timezone
    from core.douyin.douyin_account_model import DouyinAccount

    now = timezone.now()
    rows = list(DouyinAccount.objects.filter(status=1))
    out = []
    for acc in rows:
        if acc.pending_verification_until and now < acc.pending_verification_until:
            continue
        out.append(acc)
    return out


async def _probe_and_apply(account, stats: dict) -> None:
    from asgiref.sync import sync_to_async

    account_id = str(account.id)
    has_send = await sync_to_async(_account_has_send_credential)(account_id)
    result = await probe_account_credential(account)
    stats["checked"] += 1

    if result.status == PROBE_LOGIN_EXPIRED:
        # 失效前二次确认：单次探活判失效有可能是瞬时抖动（网络/签名进程瞬态）。再探一次，
        # 两次都失效才打回，避免把好号误打成失效（与 worker 接收循环同一保守取向）。
        confirmed = await _reconfirm_probe_expired(account, result)
        if confirmed.status == PROBE_LOGIN_EXPIRED:
            stats["login_expired"] += 1
            await _on_probe_expired(account, confirmed)
        else:
            stats["inconclusive"] += 1
            await sync_to_async(_update_probe_inconclusive)(
                account_id,
                f"首探判失效但二次未复现（second={confirmed.status}）：{confirmed.detail[:140]}",
            )
            logger.info(
                f"[probe] 失效二次未复现，按未知处理不打回 account={account_id} "
                f"first_detail={result.detail[:80]}"
            )
        return

    if result.status == PROBE_VALID:
        stats["valid"] += 1
        await sync_to_async(_update_probe_ok)(account_id, has_send)
        # 提前告警：凭证仍有效，但 bd-ticket 已临近老化阈值时预警，给重导留出提前量
        await sync_to_async(_maybe_warn_ticket_aging)(account_id)
        return

    # inconclusive：仅记录探活时间与原因，不改账号状态
    stats["inconclusive"] += 1
    await sync_to_async(_update_probe_inconclusive)(account_id, result.detail)


async def _reconfirm_probe_expired(account, first: ProbeResult) -> ProbeResult:
    """对「首探判失效」做一次复核探活；默认开启，可用 DOUYIN_PROBE_RECONFIRM=False 关闭。

    返回复核结果：复核仍 login_expired 才视为真失效；否则把结果当作非失效（交由调用方按
    inconclusive 处理，不打回）。复核异常时也按非失效处理（保守，不误伤）。
    """
    from django.conf import settings

    if not getattr(settings, "DOUYIN_PROBE_RECONFIRM", True):
        return first
    delay = float(getattr(settings, "DOUYIN_PROBE_RECONFIRM_DELAY_S", 3) or 0)
    if delay > 0:
        await asyncio.sleep(delay)
    second = await probe_account_credential(account)
    logger.info(
        f"[probe] 失效二次确认 account={account.id} first={first.status} "
        f"second={second.status}"
    )
    return second


async def _on_probe_expired(account, result: ProbeResult) -> None:
    """探活判定失效：打回登录态 + 标记 credential_state=invalid + WS 推送。"""
    from asgiref.sync import sync_to_async
    from core.douyin.runtime.account_status import mark_account_login_invalid
    from core.douyin.runtime.ws_notify import push_to_user

    account_id = str(account.id)
    reason = (
        f"主动探活判定登录失效 http={result.http_status} "
        f"proto={result.proto_status_code} {result.detail[:120]}"
    )
    logger.warning(f"[probe] ✘ 账号探活失效 account={account_id} {reason}")
    owner_id = await mark_account_login_invalid(account_id, reason)
    await sync_to_async(_update_probe_invalid)(account_id, reason)
    if owner_id:
        await push_to_user(owner_id, "login_expired", {
            "account_id": account_id,
            "reason": "凭证已失效，请重新导入登录态",
            "source": "probe",
        })


# ──────────────────────── 同步 DB 辅助 ────────────────────────


def _account_has_send_credential(account_id: str) -> bool:
    from core.douyin.runtime.credential import has_send_credential
    try:
        from core.douyin.runtime.storage import load_storage_state
        state = load_storage_state(account_id)
    except Exception:  # noqa: BLE001
        return False
    return has_send_credential(state or {})


def _update_probe_ok(account_id: str, has_send: bool) -> None:
    from django.utils import timezone
    from core.douyin.douyin_account_model import DouyinAccount

    state = CRED_SENDABLE if has_send else CRED_RECEIVE_ONLY
    DouyinAccount.objects.filter(id=account_id).update(
        credential_state=state,
        last_probe_at=timezone.now(),
        last_probe_error=None,
    )


def _update_probe_inconclusive(account_id: str, detail: str) -> None:
    from django.utils import timezone
    from core.douyin.douyin_account_model import DouyinAccount

    DouyinAccount.objects.filter(id=account_id).update(
        last_probe_at=timezone.now(),
        last_probe_error=(detail or "")[:255] or None,
    )


def _update_probe_invalid(account_id: str, reason: str) -> None:
    from django.utils import timezone
    from core.douyin.douyin_account_model import DouyinAccount

    DouyinAccount.objects.filter(id=account_id).update(
        credential_state=CRED_INVALID,
        last_probe_at=timezone.now(),
        last_probe_error=(reason or "")[:255] or None,
    )


def _bd_ticket_create_time(account_id: str) -> Optional[int]:
    """读取 storage 里 bd-ticket 的 create_time（epoch 秒），无则 None。"""
    try:
        from core.douyin.runtime.storage import load_storage_state
        state = load_storage_state(account_id) or {}
    except Exception:  # noqa: BLE001
        return None
    bd = (state.get("_bd_ticket") or {})
    ct = bd.get("create_time")
    if not ct:
        return None
    try:
        return int(str(ct).strip())
    except (TypeError, ValueError):
        return None


def _maybe_warn_ticket_aging(account_id: str) -> None:
    """bd-ticket 老化提前告警：超过阈值且 6 小时内未告警过则记一条 risk_alert（仅发送号有意义）。"""
    from datetime import timedelta
    from django.conf import settings
    from django.utils import timezone
    from core.douyin.douyin_event_model import DouyinEvent

    warn_hours = int(getattr(settings, "DOUYIN_TICKET_WARN_AGE_HOURS", 24) or 24)
    create_time = _bd_ticket_create_time(account_id)
    if not create_time:
        return  # 无 bd-ticket（仅接收号）或拿不到时间，跳过
    age_hours = (time.time() - create_time) / 3600.0
    if age_hours < warn_hours:
        return
    recent = DouyinEvent.objects.filter(
        account_id=account_id,
        title="凭证临期提醒",
        occurred_at__gte=timezone.now() - timedelta(hours=6),
    ).exists()
    if recent:
        return
    DouyinEvent.objects.create(
        account_id=account_id,
        event_type="risk_alert",
        level="warning",
        title="凭证临期提醒",
        detail=(
            f"bd-ticket 已使用约 {age_hours:.1f} 小时（阈值 {warn_hours}h），"
            f"建议尽快重新导入 web_protect/keys，避免发送私信中途失效。"
        ),
        occurred_at=timezone.now(),
    )
    logger.info(f"[health] 凭证临期提醒 account={account_id} age={age_hours:.1f}h")


# ──────────────────────── bd-ticket 自动续期（默认关闭，PoC 验证通过后开启） ────────────────────────

# 续期端点与响应里可刷新的字段名（与 douyin_renew_poc 一致）。
# 真实端点为 GET，需带 certificate=base64(CSR) 与 creator 专属公共参数（见 http_protocol）。
_RENEW_URL_DEFAULT = "https://creator.douyin.com/aweme/v1/creator/im/user_token/v2/"
_RENEW_FIELD_KEYS = ("ts_sign", "client_cert", "ticket", "token", "sdk_cert")


def run_ticket_autorenew() -> dict:
    """bd-ticket 自动续期（同步，scheduler 入口）。

    默认关闭（DOUYIN_TICKET_AUTORENEW_ENABLED=False）：PoC（douyin_renew_poc）人工确认
    续期端点与字段语义后，才把此开关打开。关闭时直接返回，依赖 P1/P2 的探活告警保底。

    任务代码：scheduler.tasks.douyin_ticket_autorenew
    """
    from django.conf import settings
    if not getattr(settings, "DOUYIN_TICKET_AUTORENEW_ENABLED", False):
        return {"success": True, "message": "自动续期未启用（默认关闭，依赖告警保底）", "renewed": 0}
    try:
        return asyncio.run(_autorenew_all_async())
    except Exception as e:  # noqa: BLE001
        logger.error(f"[renew] 自动续期任务异常: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


async def _autorenew_all_async() -> dict:
    from asgiref.sync import sync_to_async
    from django.conf import settings

    refresh_hours = int(getattr(settings, "DOUYIN_TICKET_REFRESH_AGE_HOURS", 18) or 18)
    accounts = await sync_to_async(_load_online_accounts)()
    renewed = 0
    failed = 0
    for acc in accounts:
        account_id = str(acc.id)
        ct = await sync_to_async(_bd_ticket_create_time)(account_id)
        if not ct:
            continue  # 仅接收号 / 无 bd-ticket，无需续期
        if (time.time() - ct) / 3600.0 < refresh_hours:
            continue  # 还没到续期阈值
        ok = await _renew_one(acc)
        if ok:
            renewed += 1
        else:
            failed += 1
    logger.info(f"[renew] 自动续期完成 renewed={renewed} failed={failed}")
    return {"success": True, "renewed": renewed, "failed": failed}


async def _renew_one(account) -> bool:
    """对单账号尝试续期：调用续期端点，解析刷新字段，写回 storage 的 _bd_ticket。

    真实形态：GET user_token/v2，带 certificate=base64(CSR) 与 creator 专属公共参数。
    缺 csr（老登录态未存）则跳过，靠告警保底。
    """
    import base64
    import json
    from asgiref.sync import sync_to_async
    from django.conf import settings
    from core.douyin.runtime.transport.http_protocol import (
        CREATOR_USER_TOKEN_URL,
        _BASE_CREATOR_JSON_HEADERS,
        HttpProtocolTransport,
        creator_token_base_params,
    )
    from core.douyin.runtime.storage import load_storage_state
    from core.douyin.runtime.transport.sign_types import SignerUnavailable

    account_id = str(account.id)
    url = getattr(settings, "DOUYIN_TICKET_RENEW_URL", _RENEW_URL_DEFAULT)
    state = await sync_to_async(load_storage_state)(account_id) or {}
    csr = str(((state.get("_bd_ticket") or {}).get("csr") or "")).strip()
    if not csr:
        prik = str(((state.get("_bd_ticket") or {}).get("private_key") or "")).strip()
        if prik:
            try:
                from cryptography import x509
                from cryptography.x509.oid import NameOID
                from cryptography.hazmat.primitives import hashes, serialization
                from cryptography.hazmat.primitives.serialization import load_pem_private_key
                key = load_pem_private_key(prik.encode("utf-8"), password=None)
                builder = x509.CertificateSigningRequestBuilder().subject_name(x509.Name([
                    x509.NameAttribute(NameOID.COMMON_NAME, "douyin.com")
                ]))
                csr_obj = builder.sign(key, hashes.SHA256())
                csr = csr_obj.public_bytes(serialization.Encoding.PEM).decode("utf-8")
                # 写回存储以便下次直接读取
                await sync_to_async(_write_back_bd_ticket)(account_id, {"csr": csr})
                logger.info(f"[renew] 已成功为无 csr 的账号离线生成并补齐 ec_csr：account={account_id}")
            except Exception as e:
                logger.warning(f"[renew] 自动生成 csr 失败 account={account_id} err={e}")
                return False
        else:
            logger.info(f"[renew] 跳过续期：account={account_id} storage 无 csr 且无私钥（仅接收账号无需续期）")
            return False
    certificate = base64.b64encode(csr.encode("utf-8")).decode("ascii")
    transport = HttpProtocolTransport()
    try:
        await transport.start(account)
        if not transport._signer_healthy():  # noqa: SLF001
            return False
        resp = await transport._sign.signed_fetch(  # noqa: SLF001
            method="GET", url=url, body=None, headers=_BASE_CREATOR_JSON_HEADERS,
            base_params=creator_token_base_params(getattr(account, "user_agent", "") or ""),
            extra_params={"certificate": certificate},
        )
    except SignerUnavailable:
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[renew] 续期请求异常 account={account_id}: {e}")
        return False
    finally:
        try:
            await transport.stop(account)
        except Exception:  # noqa: BLE001
            pass

    if not resp.ok:
        logger.warning(f"[renew] 续期 HTTP 非 2xx account={account_id} status={resp.status}")
        return False
    try:
        payload = json.loads(resp.text)
    except Exception:  # noqa: BLE001
        logger.warning(f"[renew] 续期响应非 JSON account={account_id}")
        return False

    fields = _extract_renew_fields(payload)
    if not fields:
        logger.info(f"[renew] 续期响应无可刷新字段 account={account_id}（保持告警保底）")
        return False

    ok = await sync_to_async(_write_back_bd_ticket)(account_id, fields)
    if ok:
        logger.info(f"[renew] ✅ 账号续期成功 account={account_id} fields={list(fields.keys())}")
    return ok


def _extract_renew_fields(payload) -> dict:
    """从续期响应里抽取可写回的 bd-ticket 字段（仅取已知非空字段）。

    将 v2 实测响应字段映射回 im_send_pb2 读取的 v1 字段：
      - sdk_cert -> client_cert
      - token -> ticket
      - ts_sign -> ts_sign
    """
    out: dict[str, str] = {}

    def _walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                lk = k.lower()
                if lk in _RENEW_FIELD_KEYS and isinstance(v, str) and v:
                    # 将 v2 的 sdk_cert/token 映射到 storage v1 对应的字段名
                    if lk == "sdk_cert":
                        target = "client_cert"
                    elif lk == "token":
                        target = "ticket"
                    else:
                        target = lk
                    out.setdefault(target, v)
                _walk(v)
        elif isinstance(obj, list):
            for it in obj:
                _walk(it)

    _walk(payload)
    return out


def _write_back_bd_ticket(account_id: str, fields: dict) -> bool:
    from core.douyin.runtime.storage import update_bd_ticket
    return update_bd_ticket(account_id, **fields)


# ──────────────────────── 僵尸会话清理 + worker 存活巡检 ────────────────────────


def cleanup_stale_sessions() -> dict:
    """清理僵尸 DouyinSession + worker 存活巡检（同步，scheduler 入口）。

    1. 心跳超时（默认 > DOUYIN_SESSION_STALE_SECONDS=120s）且未 stopped 的会话 → 置 stopped，
       并记 risk_alert 事件（worker 崩溃 / 卡死时面板不再显示"运行中"的假象）。
    2. 在线账号(status=1)但没有任何存活会话 → 记 risk_alert（提示 worker 可能未托管该账号）。

    任务代码：scheduler.tasks.douyin_cleanup_stale_sessions
    建议 cron：*/2 * * * *（每 2 分钟）。
    """
    from datetime import timedelta
    from django.conf import settings
    from django.utils import timezone
    from core.douyin.douyin_account_model import DouyinAccount
    from core.douyin.douyin_event_model import DouyinEvent
    from core.douyin.douyin_session_model import DouyinSession

    stale_seconds = int(getattr(settings, "DOUYIN_SESSION_STALE_SECONDS", 120) or 120)
    cutoff = timezone.now() - timedelta(seconds=stale_seconds)

    stale_qs = DouyinSession.objects.filter(last_heartbeat__lt=cutoff).exclude(status="stopped")
    stale_list = list(stale_qs.select_related("account"))
    cleaned = 0
    for sess in stale_list:
        DouyinEvent.objects.create(
            account_id=sess.account_id,
            session_id=sess.id,
            event_type="risk_alert",
            level="warning",
            title="僵尸会话已清理",
            detail=(
                f"会话心跳超时 {stale_seconds}s 未更新（worker={sess.worker_id} "
                f"last_heartbeat={sess.last_heartbeat}），已置 stopped。"
            ),
            occurred_at=timezone.now(),
            worker_id=sess.worker_id,
        )
        cleaned += 1
    if stale_list:
        stale_qs.update(status="stopped", error_message="心跳超时，调度器判定为僵尸会话")

    # worker 存活巡检：在线账号但无存活会话
    orphan = 0
    online_accounts = DouyinAccount.objects.filter(status=1)
    for acc in online_accounts:
        sess = DouyinSession.objects.filter(account_id=acc.id).first()
        alive = bool(sess and sess.last_heartbeat and sess.last_heartbeat >= cutoff
                     and sess.status != "stopped")
        if not alive:
            orphan += 1
            # 去重：同一账号在 10 分钟内只告警一次，避免每轮刷屏
            recent = DouyinEvent.objects.filter(
                account_id=acc.id,
                title="在线账号无存活会话",
                occurred_at__gte=timezone.now() - timedelta(minutes=10),
            ).exists()
            if not recent:
                DouyinEvent.objects.create(
                    account_id=acc.id,
                    event_type="risk_alert",
                    level="warning",
                    title="在线账号无存活会话",
                    detail="账号标记为在线，但无 worker 心跳会话（worker 可能未运行/未托管该账号）。",
                    occurred_at=timezone.now(),
                )

    logger.info(f"[health] 僵尸会话清理 cleaned={cleaned} orphan_online={orphan}")
    return {"success": True, "cleaned": cleaned, "orphan_online": orphan}
