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
  - 懒加载：模块 import 不触发 Node 编译；首次签名时才启动；避免无 Node 环境时
    仅 import 就炸（清理阶段/单测可安全 import）。
  - 常驻进程池（默认）：N 个常驻 node 子进程（sign_pool_runner.js），各自启动时只
    eval 一次 dy_ab.js，之后按行 JSON 收发，消除 PyExecJS「每次 call 起一个 node 子
    进程」在高并发（数十~数百账号）下的子进程风暴。池不可用时自动回退 PyExecJS。
  - 线程安全：池用空闲队列分发、每 worker 持锁串行；compile 兜底路径用锁保护。

依赖（部署）：
  - pip 包 PyExecJS
  - Node.js 18+
  - sign/js/dy_ab.js 及其 npm 依赖 sign/js/node_modules（jsdom/jsrsasign/sdenv/vm 等）
"""
from __future__ import annotations

import base64
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from contextlib import suppress
from functools import partial
from pathlib import Path
from shutil import which
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)


def _resolve_js_dir() -> Path:
    """Locate vendored dy_ab.js (dev tree or PyInstaller _MEIPASS)."""
    if getattr(sys, 'frozen', False):
        bundled = Path(sys._MEIPASS) / 'core' / 'douyin' / 'runtime' / 'transport' / 'sign' / 'js'
        if bundled.is_dir():
            return bundled
    return Path(__file__).resolve().parent / 'js'


# dy_ab.js 与其 node_modules 的位置（与本文件同级的 js/ 目录）
_JS_DIR = _resolve_js_dir()
_DY_AB_JS = _JS_DIR / "dy_ab.js"
_NODE_MODULES = _JS_DIR / "node_modules"
_POOL_RUNNER = _JS_DIR / "sign_pool_runner.js"

# 懒加载的 execjs 编译上下文（单例，作为进程池不可用时的兜底）
_ctx = None
_ctx_lock = threading.Lock()


class JsSignerUnavailable(RuntimeError):
    """JS 签名引擎不可用（PyExecJS 未装 / Node 缺失 / dy_ab.js 缺失 / 编译失败）。

    与 transport.sign_provider.SignerUnavailable 区分：这是“引擎层”错误，
    JsSignProvider 捕获后应转译成 SignerUnavailable 以触发上层 fallback。
    """


# ──────────────────────── 常驻 Node 进程池（消除子进程风暴） ────────────────────────


def _node_bin() -> str:
    explicit = os.environ.get('DOUYIN_NODE_BIN', '').strip()
    if explicit and Path(explicit).is_file():
        return explicit
    if getattr(sys, 'frozen', False):
        runtime = Path(sys._MEIPASS) / 'runtime'
        for name in ('node', 'node.exe'):
            candidate = runtime / name
            if candidate.is_file():
                return str(candidate)
    return os.environ.get('DOUYIN_NODE_BIN', 'node')


def _pool_enabled() -> bool:
    try:
        from django.conf import settings
        return bool(getattr(settings, "DOUYIN_SIGN_POOL_ENABLED", True))
    except Exception:  # noqa: BLE001
        return True


# 账号数提示：worker 在 refresh 时写入当前托管账号数，供 auto 模式计算池大小。
_account_hint: int = 0


def set_account_hint(n: int) -> None:
    """worker 上报当前托管账号数；auto 模式据此自适应签名池大小（仅增不减）。

    若池已创建且当前规模不足，立即按需扩容（新增常驻 node 进程），避免账号
    增多后签名排队。缩容不做：避免误杀正在处理请求的进程，多余空闲进程留到
    worker 重启回收。
    """
    global _account_hint
    try:
        _account_hint = max(0, int(n))
    except Exception:  # noqa: BLE001
        return
    pool = _pool
    if pool is not None:
        with suppress(Exception):
            pool.grow(_pool_size())


def _pool_cap() -> int:
    try:
        from django.conf import settings
        return max(1, int(getattr(settings, "DOUYIN_SIGN_POOL_MAX", 6) or 6))
    except Exception:  # noqa: BLE001
        return 6


def _pool_size() -> int:
    """签名进程池目标大小。

    DOUYIN_SIGN_POOL_SIZE='auto'（默认）时随托管账号数自适应：约等于账号数，
    下限 2（保证扫描/发送不互相饿死），上限 DOUYIN_SIGN_POOL_MAX（默认 6，
    每个 node 进程约数十 MB 内存）。也可设为具体数字固定大小。
    """
    try:
        from django.conf import settings
        raw = getattr(settings, "DOUYIN_SIGN_POOL_SIZE", "auto")
    except Exception:  # noqa: BLE001
        raw = "auto"
    if isinstance(raw, str) and raw.strip().lower() in ("auto", ""):
        cap = _pool_cap()
        return max(1, min(max(2, _account_hint), cap))
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 2


def _pool_call_timeout() -> float:
    try:
        from django.conf import settings
        return float(getattr(settings, "DOUYIN_SIGN_POOL_TIMEOUT", 20) or 20)
    except Exception:  # noqa: BLE001
        return 20.0


class _NodeWorker:
    """一个常驻 node 子进程：启动时 eval dy_ab.js 一次，之后按行 JSON 收发签名请求。"""

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._seq = 0
        self._start()

    def _start(self) -> None:
        self._proc = subprocess.Popen(
            [_node_bin(), str(_POOL_RUNNER), str(_DY_AB_JS)],
            cwd=str(_JS_DIR),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        # 读启动握手行（{"id":"ready"...}）；失败说明 dy_ab.js 加载/Node 出错
        ready = self._proc.stdout.readline() if self._proc.stdout else ""
        if not ready or '"ready"' not in ready:
            err = ""
            try:
                if self._proc.stderr:
                    err = self._proc.stderr.readline()
            except Exception:  # noqa: BLE001
                pass
            self._kill()
            raise JsSignerUnavailable(f"签名进程启动失败（Node/dy_ab.js 加载错误）: {err[:300]!r}")

    def _kill(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is not None:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def call(self, method: str, args: list) -> str:
        """串行（持锁）发一条请求并读回响应；进程死掉则自动重启一次后重试。"""
        with self._lock:
            if not self._alive():
                self._start()
            try:
                return self._roundtrip(method, args)
            except JsSignerUnavailable:
                # 进程异常：重启一次再试，仍失败则抛出
                self._kill()
                self._start()
                return self._roundtrip(method, args)

    def _roundtrip(self, method: str, args: list) -> str:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise JsSignerUnavailable("签名进程不可用")
        self._seq += 1
        req_id = self._seq
        line = json.dumps({"id": req_id, "method": method, "args": args}, ensure_ascii=False)
        try:
            proc.stdin.write(line + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise JsSignerUnavailable(f"签名进程 stdin 写入失败: {e}") from e

        # 读到匹配 id 的响应（正常情况下串行下一行就是本次响应）
        deadline = time.time() + _pool_call_timeout()
        while time.time() < deadline:
            resp_line = proc.stdout.readline()
            if resp_line == "":  # EOF：进程已退出
                raise JsSignerUnavailable("签名进程已退出（EOF）")
            resp_line = resp_line.strip()
            if not resp_line:
                continue
            try:
                resp = json.loads(resp_line)
            except json.JSONDecodeError:
                continue
            if resp.get("id") != req_id:
                continue  # 跳过握手/陈旧行
            if "error" in resp and resp["error"]:
                raise JsSignerUnavailable(f"JS 签名执行错误: {str(resp['error'])[:300]}")
            return str(resp.get("result", ""))
        raise JsSignerUnavailable("签名进程响应超时")

    def stop(self) -> None:
        with self._lock:
            self._kill()


class _NodeSignPool:
    """常驻签名进程池：N 个 _NodeWorker，按空闲队列取用，调用串行不会交叉。"""

    def __init__(self, size: int) -> None:
        self._grow_lock = threading.Lock()
        self._workers: list[_NodeWorker] = [_NodeWorker() for _ in range(max(1, size))]
        self._idle: "queue.Queue[_NodeWorker]" = queue.Queue()
        for w in self._workers:
            self._idle.put(w)

    def grow(self, target: int) -> None:
        """扩容到 target（仅增不减）。新增 node 进程加入空闲队列即可被取用。"""
        with self._grow_lock:
            added = 0
            while len(self._workers) < target:
                w = _NodeWorker()
                self._workers.append(w)
                self._idle.put(w)
                added += 1
            if added:
                logger.info(f"[sign.js] 签名进程池扩容 +{added} -> size={len(self._workers)}")

    def call(self, method: str, args: list) -> str:
        worker = self._idle.get()
        try:
            return worker.call(method, args)
        finally:
            self._idle.put(worker)

    def stop(self) -> None:
        for w in self._workers:
            w.stop()


_pool: Optional[_NodeSignPool] = None
_pool_lock = threading.Lock()
_pool_failed = False
_pool_error = ''


def _get_pool() -> Optional[_NodeSignPool]:
    """取（必要时启动）签名进程池；不可用则返回 None。"""
    global _pool, _pool_failed, _pool_error
    if not _pool_enabled() or _pool_failed:
        return None
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        if not _POOL_RUNNER.exists() or not _DY_AB_JS.exists():
            _pool_failed = True
            _pool_error = f"签名脚本缺失: runner={_POOL_RUNNER} dy_ab={_DY_AB_JS}"
            return None
        try:
            _pool = _NodeSignPool(_pool_size())
            logger.info(f"[sign.js] 常驻签名进程池启动成功 size={_pool_size()}")
        except Exception as e:  # noqa: BLE001
            _pool_failed = True
            _pool_error = f"{type(e).__name__}: {e}"
            logger.warning(f"[sign.js] 常驻签名进程池启动失败: {_pool_error}")
            return None
    return _pool


def _pool_or_ctx_call(method: str, *args: Any) -> str:
    """优先走常驻进程池；客户端不回退 PyExecJS，避免误调用系统 Java runtime。"""
    pool = _get_pool()
    if pool is not None:
        return pool.call(method, list(args))
    if os.environ.get('ZQ_ENV') == 'client':
        detail = _pool_error or '签名进程池未启动'
        raise JsSignerUnavailable(f"Node 签名进程不可用: {detail}")
    return _get_ctx().call(method, *args)


def _compile():
    """编译 dy_ab.js，返回 execjs 上下文。失败抛 JsSignerUnavailable。"""
    os.environ.setdefault('EXECJS_RUNTIME', 'Node')
    node = _node_bin()
    if node != 'node' and Path(node).is_file():
        os.environ['EXECJS_RUNTIME'] = 'Node'
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

    if not Path(_node_bin()).is_file() and which('node') is None:
        raise JsSignerUnavailable(
            'Node.js 不可用（打包客户端需内嵌 runtime/node，或设置 DOUYIN_NODE_BIN）'
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


def shutdown_pool() -> None:
    """停止常驻签名进程池（进程退出钩子可调用，避免遗留 node 子进程）。"""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.stop()
            _pool = None


def is_available() -> bool:
    """探测 JS 签名引擎是否可用（不抛异常，用于健康检查/灰度判断）。

    优先探测常驻进程池；服务端开发环境可回退 PyExecJS 编译。
    """
    pool = _get_pool()
    if pool is not None:
        return True
    if os.environ.get('ZQ_ENV') == 'client':
        return False
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
    return _pool_or_ctx_call("get_ab", query, data)


def get_req_sign(payload: Union[str, dict], prik: str) -> str:
    """计算 bd-ticket-guard 的 req_sign。

    Args:
        payload: 私信场景传 obj(dict)，其它拼接串传 str（对照 DouYin_Spider 注释）。
        prik: 账号 EC 私钥（ec_privateKey）。
    """
    return _pool_or_ctx_call("get_req_sign", payload, prik)


def get_ree_key(prik: str) -> str:
    """由私钥派生 ree_key。"""
    return _pool_or_ctx_call("get_ree_key", prik)


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
