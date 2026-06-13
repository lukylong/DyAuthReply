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
      "_bd_ticket": {"private_key","ticket","ts_sign","client_cert"}   # 可选
    }
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
    """解析 keys → {private_key}（即 ec_privateKey）。"""
    if not (raw or "").strip():
        return {}
    inner = _unwrap_data_json(raw)
    priv = inner.get("ec_privateKey") or inner.get("private_key") or ""
    return {"private_key": str(priv)} if priv else {}


# 一键导入串前缀：浏览器扩展把 cookie/web_protect/keys 打包成单行，避免逐项粘贴误操作。
_BUNDLE_PREFIX = "DYCRED1."


def parse_credential_bundle(raw: str) -> dict[str, str]:
    """解析浏览器扩展生成的「一键导入串」→ {cookie, web_protect, keys, user_agent}。

    支持两种形态：
      1) 带前缀的 base64url：``DYCRED1.<base64url(JSON)>``（扩展默认产出，单行不易误改）；
      2) 裸 JSON：``{"cookie": "...", "web_protect": "...", "keys": "...", "ua": "..."}``。

    内层 JSON 字段名兼容 ``ua`` / ``user_agent``。任一缺失则对应值为空串。

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
    bd.update(parse_bd_ticket_from_cookie(cookies))
    wp = parse_web_protect(web_protect)
    for key in ("ticket", "ts_sign", "client_cert", "create_time"):  # 显式 web_protect 覆盖自动值
        if wp.get(key):
            bd[key] = wp[key]
    ks = parse_keys(keys)
    if ks.get("private_key"):  # 私钥只能显式提供（不在 cookie）
        bd["private_key"] = ks["private_key"]
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
