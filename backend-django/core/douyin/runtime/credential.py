#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: runtime/credential.py
@Desc: 抖音登录态凭证解析 —— 把「浏览器粘贴的 Cookie / web_protect / keys」转成
        内部 storage_state 结构（沿用 Playwright 时代的加密存储，零迁移）。

去浏览器化后，账号接入方式从「扫码登录」改为「粘贴 Cookie 录入」。本模块是纯函数层
（无 Django / 无网络 / 无浏览器），负责三件套解析，对照 DouYin_Spider/builder/auth.py：

    cookie       —— 监控/接收 + 发送都需要（必填）
    web_protect  —— bd-ticket-guard 的 ticket / ts_sign / client_cert（发送私信需要）
    keys         —— 含 ec_privateKey(priK)（发送私信需要）

输出的 storage_state 结构与 storage.load_storage_state / JsSignProvider._load_account_credentials
对齐：
    {
      "cookies": [{"name","value","domain":".douyin.com","path":"/"}, ...],
      "origins": [],
      "_bd_ticket": {"private_key","ticket","ts_sign","client_cert","csr"}  # 可选
    }

其中 csr（来自 keys 的 ec_csr）用于 bd-ticket 自动续期端点的 certificate 参数。
"""
from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import unquote


def parse_cookie_header(cookie_str: str) -> dict[str, str]:
    """把 "a=1; b=2; c=3" 形式的 Cookie 整行解析成 name -> value。

    对照 DouYin_Spider/utils/dy_util.py:trans_cookies（兼容 value 内含 '=' 的情况）。
    """
    out: dict[str, str] = {}
    for part in (cookie_str or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        if name:
            out[name] = value.strip()
    return out


def _unwrap_data_json(raw: str) -> dict[str, Any]:
    """解析抖音导出的 `{"data":"<内层JSON字符串>"}` 双层结构；兼容直接给内层 JSON。

    对照 auth.py：`json.loads(json.loads(raw)['data'])`。
    """
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        outer = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        raise ValueError(f"不是合法 JSON: {e}") from e
    # 双层：{"data": "..."}（data 可能是 JSON 字符串或已是 dict）
    if isinstance(outer, dict) and "data" in outer:
        data = outer["data"]
        if isinstance(data, str):
            try:
                return json.loads(data)
            except (json.JSONDecodeError, TypeError) as e:
                raise ValueError(f"data 字段不是合法 JSON: {e}") from e
        if isinstance(data, dict):
            return data
    # 单层：直接就是内层
    if isinstance(outer, dict):
        return outer
    raise ValueError("JSON 顶层不是对象")


def parse_web_protect(raw: str) -> dict[str, str]:
    """解析 web_protect → {ticket, ts_sign, client_cert, create_time}（任一缺失则为空串）。

    create_time 是抖音签发该 bd-ticket 时的 epoch 秒，用于「凭证临期提前告警」估算 ticket 年龄。
    """
    if not (raw or "").strip():
        return {}
    inner = _unwrap_data_json(raw)
    out = {
        "ticket": str(inner.get("ticket") or ""),
        "ts_sign": str(inner.get("ts_sign") or ""),
        "client_cert": str(inner.get("client_cert") or ""),
    }
    create_time = inner.get("create_time")
    if create_time:
        out["create_time"] = str(create_time)
    return out


def parse_keys(raw: str) -> dict[str, str]:
    """解析 keys → {private_key, csr}（ec_privateKey 必有；ec_csr 用于 bd-ticket 自动续期）。

    ec_csr 是浏览器生成的证书签名请求（CSR，PEM 文本）。bd-ticket 续期端点
    （creator.douyin.com/.../im/user_token/v2）要求把它 base64 后作为 `certificate` 参数提交，
    服务端据此重新签发 sdk_cert / token / ts_sign。早期导入只取了 private_key，这里补上 csr，
    使后续无需重新抓取即可自动续期；缺失时为空串（老登录态续期会因此跳过，靠告警保底）。
    """
    if not (raw or "").strip():
        return {}
    inner = _unwrap_data_json(raw)
    priv = inner.get("ec_privateKey") or inner.get("private_key") or ""
    if not priv:
        return {}
    out = {"private_key": str(priv)}
    csr = inner.get("ec_csr") or inner.get("csr") or ""
    if csr:
        out["csr"] = str(csr)
    return out


# 一键导入串前缀：浏览器扩展把 cookie/web_protect/keys 打包成单行，避免逐项粘贴误操作。
_BUNDLE_PREFIX = "DYCRED1."


def parse_credential_bundle(raw: str) -> dict[str, str]:
    """解析浏览器扩展生成的「一键导入串」→ {cookie, web_protect, keys, user_agent, sec_uid, nickname, unique_id}。

    支持两种形态：
      1) 带前缀的 base64url：``DYCRED1.<base64url(JSON)>``（扩展默认产出，单行不易误改）；
      2) 裸 JSON：``{"cookie": "...", "web_protect": "...", "keys": "...", "ua": "..."}``。

    内层 JSON 字段名兼容 ``ua`` / ``user_agent``。任一缺失则对应值为空串。

    v1.8.0 新增：支持 sec_uid、nickname、unique_id 字段（由插件导出）。

    Raises:
        ValueError: 串为空、base64/JSON 解析失败、或顶层不是对象。
    """
    s = (raw or "").strip()
    if not s:
        raise ValueError("一键导入串为空")

    payload: Any
    if s.startswith(_BUNDLE_PREFIX):
        b64 = s[len(_BUNDLE_PREFIX):].strip()
        try:
            pad = "=" * (-len(b64) % 4)
            decoded = base64.urlsafe_b64decode(b64 + pad).decode("utf-8")
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"一键导入串 base64 解码失败：{e}") from e
        try:
            payload = json.loads(decoded)
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(f"一键导入串内层不是合法 JSON：{e}") from e
    else:
        try:
            payload = json.loads(s)
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(
                "无法识别的一键导入串（应以 DYCRED1. 开头，或为 {cookie,web_protect,keys} JSON）："
                f"{e}"
            ) from e

    if not isinstance(payload, dict):
        raise ValueError("一键导入串顶层不是对象")

    return {
        "cookie": str(payload.get("cookie") or ""),
        "web_protect": str(payload.get("web_protect") or ""),
        "keys": str(payload.get("keys") or ""),
        "user_agent": str(payload.get("user_agent") or payload.get("ua") or ""),
        "sec_uid": str(payload.get("sec_uid") or ""),          # 新增
        "nickname": str(payload.get("nickname") or ""),        # 新增
        "unique_id": str(payload.get("unique_id") or ""),      # 新增
        "avatar": str(payload.get("avatar") or ""),            # 新增
    }


def _b64url_json(raw: str) -> dict[str, Any]:
    """解码 cookie 里 base64(可能 url-encoded、缺 padding)的 JSON 值；失败返回 {}。"""
    s = unquote(raw or "")
    if not s:
        return {}
    try:
        pad = "=" * (-len(s) % 4)
        return json.loads(base64.b64decode(s + pad))
    except Exception:  # noqa: BLE001
        return {}


def parse_bd_ticket_from_cookie(cookies: dict[str, str]) -> dict[str, str]:
    """从 cookie 自动提取 bd-ticket-guard 中**能自动得到**的部分。

    可自动得到：`ts_sign`、`ree_public_key`（在 cookie 的 `bd_ticket_guard_client_data_v2`
    里），以及浏览器最近算好的 `client_data` 快照。
    **无法**自动得到：`ec_privateKey`(私钥)——浏览器用 Web Crypto 生成后存于本地
    IndexedDB，出于安全从不写入 cookie/请求，因此发送私信所需的私钥必须另行从浏览器导出。
    """
    out: dict[str, str] = {}
    v2 = _b64url_json(cookies.get("bd_ticket_guard_client_data_v2", ""))
    if v2.get("ts_sign"):
        out["ts_sign"] = str(v2["ts_sign"])
    if v2.get("ree_public_key"):
        out["ree_public_key"] = str(v2["ree_public_key"])
    snapshot = unquote(cookies.get("bd_ticket_guard_client_data", "") or "")
    if snapshot:
        out["client_data_snapshot"] = snapshot
    return out


def merge_storage_state(
    base_state: dict[str, Any] | None,
    cookie_str: str = "",
    *,
    web_protect: str = "",
    keys: str = "",
    domain: str = ".douyin.com",
) -> dict[str, Any]:
    """在已有 storage_state 之上做**增量更新**，构造新的 storage_state。

    用于「先导入 Cookie，之后再补 web_protect/keys」的分步录入场景：

    - ``cookie_str`` 非空：用新 Cookie 覆盖整组 cookies；
      ``cookie_str`` 为空：复用 ``base_state`` 里已存的 cookies（只补 bd-ticket 凭证）。
    - ``web_protect`` / ``keys`` 解析后覆盖 ``_bd_ticket`` 对应字段；为空则保留旧值，
      因此补私钥不会丢掉之前的 ticket，补 ticket 也不会丢掉之前的私钥。

    Raises:
        ValueError: 既没有新 Cookie，又没有可复用的旧 Cookie。
    """
    base = base_state or {}
    new_cookies = parse_cookie_header(cookie_str)
    if new_cookies:
        from core.douyin.runtime.transport.douyin_web_profile import ensure_web_cookie_fields

        new_cookies = ensure_web_cookie_fields(new_cookies)
        cookies = new_cookies
        cookie_list = [
            {"name": k, "value": v, "domain": domain, "path": "/"} for k, v in cookies.items()
        ]
    else:
        cookie_list = list(base.get("cookies") or [])
        if not cookie_list:
            raise ValueError(
                "首次导入必须提供 Cookie（应为浏览器复制的 Cookie 整行）；"
                "补充 web_protect/keys 时可留空以复用已导入的 Cookie"
            )
        cookies = {c.get("name"): c.get("value", "") for c in cookie_list if c.get("name")}

    state: dict[str, Any] = {"cookies": cookie_list, "origins": base.get("origins") or []}

    # bd-ticket：旧值为基底 → cookie 自动解析覆盖 → 显式 web_protect/keys 覆盖
    bd: dict[str, str] = dict(base.get("_bd_ticket") or {})
    old_ts_sign = bd.get("ts_sign")
    new_bd = parse_bd_ticket_from_cookie(cookies)
    bd.update(new_bd)
    
    import time
    if new_bd.get("ts_sign") and new_bd.get("ts_sign") != old_ts_sign:
        bd["create_time"] = str(int(time.time()))

    wp = parse_web_protect(web_protect)
    for key in ("ticket", "ts_sign", "client_cert", "create_time"):  # 显式 web_protect 覆盖自动值
        if wp.get(key):
            bd[key] = wp[key]
    ks = parse_keys(keys)
    if ks.get("private_key"):  # 私钥只能显式提供（不在 cookie）
        bd["private_key"] = ks["private_key"]
    if ks.get("csr"):  # CSR 供 bd-ticket 自动续期（certificate 参数），缺失则续期跳过
        bd["csr"] = ks["csr"]
    if bd:
        state["_bd_ticket"] = bd
    return state


def build_storage_state(
    cookie_str: str,
    *,
    web_protect: str = "",
    keys: str = "",
    domain: str = ".douyin.com",
) -> dict[str, Any]:
    """把粘贴的三件套构造成内部 storage_state（可直接交给 storage.save_storage_state）。

    等价于「无历史登录态」时的 :func:`merge_storage_state`，此时 cookie 必填。

    Raises:
        ValueError: cookie 为空/格式不正确，或 web_protect/keys 提供了但解析失败。
    """
    if not parse_cookie_header(cookie_str):
        raise ValueError("cookie 为空或格式不正确（应为浏览器复制的 Cookie 整行）")
    return merge_storage_state(
        None, cookie_str, web_protect=web_protect, keys=keys, domain=domain
    )


def has_send_credential(state: dict[str, Any]) -> bool:
    """判断该 storage_state 是否具备发送私信所需的 bd-ticket 三要素。"""
    bd = (state or {}).get("_bd_ticket") or {}
    return bool(bd.get("private_key") and bd.get("ticket") and bd.get("ts_sign"))


def session_fingerprint_from_state(state: dict[str, Any] | None) -> tuple[str, str]:
    """从 storage_state 提取 (sessionid, uid_tt)，用于多账号 cookie 去重。"""
    cookies = {
        c.get("name"): c.get("value", "")
        for c in (state or {}).get("cookies") or []
        if c.get("name")
    }
    return str(cookies.get("sessionid") or ""), str(cookies.get("uid_tt") or "")


def extract_self_uid_from_cookies(cookies: dict[str, str]) -> int:
    """从 Cookie 键值推断当前登录账号的数字 user_id（profile 拉取兜底）。

    创作者中心登录态常见 ``login_uid``；部分导出还带 ``uid`` / ``user_uid``。
    """
    if not cookies:
        return 0
    for key in ("login_uid", "uid", "user_uid", "passport_uid"):
        raw = str(cookies.get(key) or "").strip()
        if raw.isdigit():
            try:
                return int(raw)
            except ValueError:
                continue
    return 0


def find_duplicate_session_owner(
    *,
    account_id: str,
    sessionid: str,
    uid_tt: str = "",
) -> tuple[str, str, str] | None:
    """若其它账号已占用相同 sessionid（或 uid_tt），返回 (other_id, other_name, reason)。

    reason 为 ``sessionid`` / ``uid_tt`` / ``sessionid_deleted`` / ``uid_tt_deleted``，
    供错误提示区分匹配依据。

    增强：同时检查活跃账号和 7 天内删除的账号。
    """
    from datetime import timedelta
    from django.utils import timezone

    sid = (sessionid or "").strip()
    if not sid and not (uid_tt or "").strip():
        return None
    try:
        from core.douyin.douyin_account_model import DouyinAccount
        from core.douyin.runtime.storage import load_storage_state
    except Exception:  # noqa: BLE001
        return None

    # 1. 检查活跃账号（is_deleted=False）
    for acc in DouyinAccount.objects.exclude(id=account_id).filter(is_deleted=False).exclude(status=3):
        other_st = load_storage_state(str(acc.id))
        osid, ouid = session_fingerprint_from_state(other_st)
        if sid and osid == sid:
            return str(acc.id), str(acc.nickname or acc.id), "sessionid"
        # uid_tt 作为辅助：sessionid 未命中但 uid 相同也视为同一登录态
        if uid_tt and ouid and uid_tt == ouid:
            return str(acc.id), str(acc.nickname or acc.id), "uid_tt"

    # 2. 检查 7 天内删除的账号
    cutoff = timezone.now() - timedelta(days=7)
    deleted_accounts = DouyinAccount.objects.exclude(id=account_id).filter(
        is_deleted=True,
        deleted_at__isnull=False,
        deleted_at__gte=cutoff
    )
    for acc in deleted_accounts:
        other_st = load_storage_state(str(acc.id))
        if not other_st:
            continue
        osid, ouid = session_fingerprint_from_state(other_st)
        if sid and osid == sid:
            days_ago = (timezone.now() - acc.deleted_at).days
            nickname_with_hint = f"{acc.nickname or acc.id}（{days_ago}天前删除）"
            return str(acc.id), nickname_with_hint, "sessionid_deleted"
        if uid_tt and ouid and uid_tt == ouid:
            days_ago = (timezone.now() - acc.deleted_at).days
            nickname_with_hint = f"{acc.nickname or acc.id}（{days_ago}天前删除）"
            return str(acc.id), nickname_with_hint, "uid_tt_deleted"

    return None


def format_duplicate_session_error(
    *,
    other_name: str,
    reason: str,
    sessionid: str,
    uid_tt: str = "",
) -> str:
    """构造重复登录态拦截说明，附带 sessionid 前缀便于用户自查。"""
    sid = (sessionid or "").strip()
    sid_hint = f"{sid[:12]}…" if len(sid) >= 12 else sid or "（空）"

    if reason == "uid_tt":
        uid = (uid_tt or "").strip()
        uid_hint = f"{uid[:12]}…" if len(uid) >= 12 else uid or "（空）"
        detail = f"uid_tt 相同（{uid_hint}）"
    elif reason == "sessionid_deleted":
        detail = f"sessionid 相同（{sid_hint}）"
        return (
            f"此 Cookie 与最近删除的账号「{other_name}」是同一抖音登录态（{detail}），"
            f"不能导入到多个账号槽位。"
            f"请等待 7 天后再导入，或使用不同的抖音账号；"
            f"也可通过「凭证诊断」页面强制清理历史记录。"
        )
    elif reason == "uid_tt_deleted":
        uid = (uid_tt or "").strip()
        uid_hint = f"{uid[:12]}…" if len(uid) >= 12 else uid or "（空）"
        detail = f"uid_tt 相同（{uid_hint}）"
        return (
            f"此 Cookie 与最近删除的账号「{other_name}」是同一抖音登录态（{detail}），"
            f"不能导入到多个账号槽位。"
            f"请等待 7 天后再导入，或使用不同的抖音账号。"
        )
    else:
        detail = f"sessionid 相同（{sid_hint}）"

    return (
        f"此 Cookie 与账号「{other_name}」是同一抖音登录态（{detail}），"
        f"不能导入到多个账号槽位。"
        f"请在浏览器中确认右上角已登录的是目标账号后再导出；"
        f"无痕窗口若仍导出到相同 sessionid，说明实际登录的还是「{other_name}」。"
        f"也可先删除「{other_name}」槽位后再导入（不推荐，会丢失该槽位配置）。"
    )


def dedupe_managed_accounts_by_session(rows: list[dict]) -> list[dict]:
    """同一 sessionid 或 sec_uid 只保留一个账号托管（priority 高 → sort 高 → id 小 优先）。

    防止多账号槽位导入同一套 cookie 后，worker 双协程扫同一 inbox、重复自动回复。

    增强：同时按 sec_uid 去重（如果数据库中有）。
    """
    import logging

    from core.douyin.runtime.storage import load_storage_state

    log = logging.getLogger(__name__)
    if not rows:
        return rows

    # 按优先级排序
    ordered = sorted(
        rows,
        key=lambda x: (-int(x.get("priority") or 0), str(x.get("id") or "")),
    )
    kept: list[dict] = []
    session_owner: dict[str, str] = {}  # sessionid -> kept account_id
    sec_uid_owner: dict[str, str] = {}  # sec_uid -> kept account_id

    for row in ordered:
        account_id = row["id"]
        nickname = row.get("nickname", "")

        # 检查 sessionid 重复
        st = load_storage_state(account_id)
        sid, _uid = session_fingerprint_from_state(st)
        if sid:
            owner = session_owner.get(sid)
            if owner:
                log.warning(
                    f"[credential] 跳过重复 session 账号 nickname={nickname!r} "
                    f"id={account_id[:8]}… 与 id={owner[:8]}… 共用 sessionid={sid[:12]}…"
                )
                continue
            session_owner[sid] = account_id

        # 检查 sec_uid 重复（从数据库获取）
        sec_uid = row.get("sec_uid")
        if sec_uid:
            sec_owner = sec_uid_owner.get(sec_uid)
            if sec_owner:
                log.warning(
                    f"[credential] 跳过重复 sec_uid 账号 nickname={nickname!r} "
                    f"id={account_id[:8]}… 与 id={sec_owner[:8]}… 共用 sec_uid={sec_uid[:16]}…"
                )
                continue
            sec_uid_owner[sec_uid] = account_id

        kept.append(row)

    if len(kept) < len(rows):
        log.warning(
            f"[credential] session/sec_uid 去重：{len(rows)} 个候选 → 托管 {len(kept)} 个"
        )

    # 记录最终托管的账号映射（便于调试）
    if kept:
        log.info(f"[credential] 启动时托管 {len(kept)} 个账号:")
        for row in kept:
            st = load_storage_state(row["id"])
            sid, _ = session_fingerprint_from_state(st)
            sec = row.get("sec_uid", "")
            log.info(
                f"  - {row.get('nickname')} ({row['id'][:8]}…) "
                f"sessionid={sid[:12] if sid else '(无)'}… "
                f"sec_uid={sec[:16] if sec else '(无)'}…"
            )

    return kept
