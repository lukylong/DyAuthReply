#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: sign/js_signer.py
@Desc: 抖音 web JS 签名引擎封装（PyExecJS + Node 执行 dy_ab.js）

vendored 自 cv-cat/DouYin_Spider（static/dy_ab.js），用 PyExecJS 在 Node 子进程里
执行其混淆后的签名核心，暴露 4 个纯函数：

    get_ab(query, data="")              -> a_bogus（web 公共参数签名）
    get_req_sign(payload, prik)         -> bd-ticket-guard 的 req_sign（私信发送/建会话必需）
    get_ree_key(prik)                   -> ree_key
    build_bd_ticket_client_data(...)    -> bd-ticket-guard-client-data 头的值（base64url）

调用约定严格对照 DouYin_Spider/utils/dy_util.py（已被其作者验证可用）：
    dy_js.call('get_ab', query, data)
    dy_js.call('get_req_sign', e, priK)
    dy_js.call('get_ree_key', prik)

设计要点：
  - a_bogus / req_sign 是 (参数 + body + 私钥) 的纯函数，与账号在线态无关 ⇒ 一个进程
    即可为所有账号签名（共享签名引擎），无需每账号一个浏览器。
  - 懒加载：模块 import 不触发 Node 编译；首次签名时才 compile，避免无 Node 环境时
    仅 import 就炸（清理阶段/单测可安全 import）。
  - 线程安全：compile 用锁保护；PyExecJS ExternalRuntime 每次 call 起独立子进程，调用本身安全。

依赖（部署）：
  - pip 包 PyExecJS
  - Node.js 18+
  - sign/js/dy_ab.js 及其 npm 依赖 sign/js/node_modules（jsdom/jsrsasign/sdenv/vm 等）
"""
from __future__ import annotations

import base64
import json
import logging
import threading
import time
from functools import partial
from pathlib import Path
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)

# dy_ab.js 与其 node_modules 的位置（与本文件同级的 js/ 目录）
_JS_DIR = Path(__file__).resolve().parent / "js"
_DY_AB_JS = _JS_DIR / "dy_ab.js"
_NODE_MODULES = _JS_DIR / "node_modules"

# 懒加载的 execjs 编译上下文（单例）
_ctx = None
_ctx_lock = threading.Lock()


class JsSignerUnavailable(RuntimeError):
    """JS 签名引擎不可用（PyExecJS 未装 / Node 缺失 / dy_ab.js 缺失 / 编译失败）。

    与 transport.sign_provider.SignerUnavailable 区分：这是“引擎层”错误，
    JsSignProvider 捕获后应转译成 SignerUnavailable 以触发上层 fallback。
    """


def _compile():
    """编译 dy_ab.js，返回 execjs 上下文。失败抛 JsSignerUnavailable。"""
    try:
        import execjs  # noqa: PLC0415  延迟导入，未装时给清晰提示
    except ImportError as e:
        raise JsSignerUnavailable(
            "PyExecJS 未安装，无法执行 JS 签名。请 `pip install PyExecJS` 并确保 Node.js 18+ 可用。"
        ) from e

    if not _DY_AB_JS.exists():
        raise JsSignerUnavailable(
            f"签名脚本缺失: {_DY_AB_JS}（应从 DouYin_Spider/static/dy_ab.js vendoring 进来）"
        )

    # Windows 下 subprocess 默认编码可能不是 utf-8，导致 JS 输出乱码/解析失败。
    # 与 DouYin_Spider/utils/dy_util.py 一致地强制 utf-8。
    import subprocess

    subprocess.Popen = partial(subprocess.Popen, encoding="utf-8")  # type: ignore[assignment]

    cwd = str(_NODE_MODULES) if _NODE_MODULES.exists() else str(_JS_DIR)
    try:
        source = _DY_AB_JS.read_text(encoding="utf-8")
        ctx = execjs.compile(source, cwd=cwd)
    except Exception as e:  # noqa: BLE001
        raise JsSignerUnavailable(f"dy_ab.js 编译失败（Node 缺失或依赖未装?）: {type(e).__name__}: {e}") from e

    logger.info(f"[sign.js] dy_ab.js 已编译就绪 cwd={cwd}")
    return ctx


def _get_ctx():
    """取（必要时编译）单例 execjs 上下文。"""
    global _ctx
    if _ctx is not None:
        return _ctx
    with _ctx_lock:
        if _ctx is None:
            _ctx = _compile()
    return _ctx


def is_available() -> bool:
    """探测 JS 签名引擎是否可用（不抛异常，用于健康检查/灰度判断）。"""
    try:
        _get_ctx()
        return True
    except JsSignerUnavailable as e:
        logger.warning(f"[sign.js] 引擎不可用: {e}")
        return False


def get_ab(query: str, data: str = "") -> str:
    """计算 a_bogus。

    Args:
        query: 不含 a_bogus 的查询串（应已含 msToken）。
        data: POST body 字符串（protobuf 是否参与由上游决定；GET 传 ""）。

    Returns:
        a_bogus 值（字符串）。
    """
    return _get_ctx().call("get_ab", query, data)


def get_req_sign(payload: Union[str, dict], prik: str) -> str:
    """计算 bd-ticket-guard 的 req_sign。

    Args:
        payload: 私信场景传 obj(dict)，其它拼接串传 str（对照 DouYin_Spider 注释）。
        prik: 账号 EC 私钥（ec_privateKey）。
    """
    return _get_ctx().call("get_req_sign", payload, prik)


def get_ree_key(prik: str) -> str:
    """由私钥派生 ree_key。"""
    return _get_ctx().call("get_ree_key", prik)


def build_bd_ticket_client_data(
    api: str,
    ticket: str,
    ts_sign: str,
    prik: str,
    *,
    timestamp: Optional[int] = None,
) -> str:
    """生成 `bd-ticket-guard-client-data` 头的值（base64url(JSON)）。

    完整复刻 DouYin_Spider/utils/dy_util.py:generate_bd_ticket_client_data：
        res_sign = f"ticket={ticket}&path={api}&timestamp={ts}"
        payload  = {ts_sign, req_content:"ticket,path,timestamp", req_sign, timestamp}
        return base64.urlsafe_b64encode(json.dumps(payload, sep=(',',':')))

    Args:
        api: 接口 path（如 "/v1/message/send"）。
        ticket: 来自 web_protect 的 ticket。
        ts_sign: 来自 web_protect 的 ts_sign。
        prik: 账号 EC 私钥。
        timestamp: 显式时间戳（秒）；默认取当前时间。
    """
    ts = int(timestamp if timestamp is not None else time.time())
    res_sign = f"ticket={ticket}&path={api}&timestamp={ts}"
    payload: dict[str, Any] = {
        "ts_sign": ts_sign,
        "req_content": "ticket,path,timestamp",
        "req_sign": get_req_sign(res_sign, prik),
        "timestamp": ts,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8")
