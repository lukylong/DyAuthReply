"""
快手创作者平台 __NS_sig3 签名实现

签名算法: JSON body → MD5 → TachyonVM 字节码 → __NS_sig3

依赖 PyMiniRacer (内嵌 V8) 执行 JS 签名逻辑，无需 Node.js 子进程。
签名核心 JS 来自社区逆向工程 (github: gaozhenqiang/kwai-ns_sig3)。
"""

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ============================================================
# 签名核心 JS (Minified, 来自 kuaiShoSignCore.js)
# 如果 PyMiniRacer 不可用，可回退为 Node 子进程模式
# ============================================================

_SIGN_CORE_JS = """
// 快手 __NS_sig3 签名核心 — TachyonVM 字节码执行引擎
// 来源: github.com/gaozhenqiang/kwai-ns_sig3 (社区逆向)

var _0x5b95=[...]; // 字节码数组 (实际部署时替换为完整JS)
var _0x12aa=[...];
var _0x3f0a={...};

function computeSignature(md5Hash) {
    // ... TachyonVM 字节码执行逻辑 ...
    return signature;
}
"""


class SignProvider:
    """
    快手 API 签名提供器

    两种模式:
    1. PyMiniRacer 模式 (推荐): 内嵌 V8，性能好、零外部依赖
    2. Node 子进程模式: 回退方案，需 Node 环境

    Usage:
        provider = SignProvider()
        signed_url = provider.sign("/rest/cp/im/conversation/list", {"page": 1})
        # → "/rest/cp/im/conversation/list?__NS_sig3=xxxxx"
    """

    def __init__(self, sign_js_path: str | None = None):
        """
        Args:
            sign_js_path: 签名核心 JS 文件路径 (默认在同目录下查找)
                         期望文件内容 export function computeSignature(md5) {...}
        """
        self._js_loaded = False
        self._sign_js_path = sign_js_path or str(
            Path(__file__).parent / "ks_sign_core.js"
        )
        self._js_ctx = None

    # ---- 签名入口 ----

    def sign(self, path: str, body: dict | None = None) -> str:
        """
        计算 _NS_sig3 并附加到 URL 后

        Args:
            path: API 路径 (如 "/rest/cp/im/conversation/list")
            body: 请求体 dict

        Returns:
            带签名的完整路径: "/rest/cp/im/conversation/list?__NS_sig3=xxx"
        """
        sig3_value = self._compute_sig3(body or {})
        sep = "&" if "?" in path else "?"
        return f"{path}{sep}__NS_sig3={sig3_value}"

    def compute_raw(self, body: dict) -> str:
        """只计算 sig3 值，不拼接 URL"""
        return self._compute_sig3(body)

    # ---- 内部实现 ----

    def _compute_sig3(self, body: dict) -> str:
        """MD5 → TachyonVM → signature"""
        json_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        md5 = hashlib.md5(json_str.encode("utf-8")).hexdigest()

        try:
            return self._sign_with_py_mini_racer(md5)
        except Exception:
            logger.warning("PyMiniRacer sign failed, trying Node fallback")
            return self._sign_with_node_subprocess(md5)

    def _sign_with_py_mini_racer(self, md5: str) -> str:
        """通过 PyMiniRacer 计算签名"""
        self._ensure_js_loaded()
        if self._js_ctx is None:
            raise RuntimeError("PyMiniRacer not initialized")
        return str(self._js_ctx.call("computeKSNSig3", md5))

    def _sign_with_node_subprocess(self, md5: str) -> str:
        """通过 Node.js 子进程计算签名 (回退方案)"""
        import subprocess
        import tempfile

        script = f"""
        const sign = require('{self._sign_js_path}');
        console.log(sign.computeKSNSig3('{md5}'));
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as f:
            f.write(script)
            tmp_path = f.name

        try:
            result = subprocess.run(
                ["node", tmp_path], capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                raise RuntimeError(f"Node sign failed: {result.stderr}")
            return result.stdout.strip()
        finally:
            import os

            os.unlink(tmp_path)

    def _ensure_js_loaded(self) -> None:
        """惰性加载 JS 签名引擎"""
        if self._js_loaded:
            return

        try:
            import py_mini_racer  # noqa: F401

            self._js_ctx = py_mini_racer.MiniRacer()

            # 加载签名核心 JS
            if Path(self._sign_js_path).exists():
                with open(self._sign_js_path) as f:
                    js_code = f.read()
            else:
                js_code = _SIGN_CORE_JS

            self._js_ctx.eval(js_code)
            self._js_loaded = True
            logger.info("Kuaishou sign provider initialized (PyMiniRacer)")

        except ImportError:
            logger.warning(
                "py-mini-racer not installed. Install with: pip install py-mini-racer"
            )
            raise


# 注意: ks_sign_core.js 需要从 kwai-ns_sig3 仓库获取完整 JS 文件
# 路径: github.com/gaozhenqiang/kwai-ns_sig3/blob/master/kuaiShoSignCore.js
#
# 部署时将该文件复制到 backend-django/core/kuaishou/ks_sign_core.js
# 并修改为可被 Node require() 或 PyMiniRacer eval() 的格式:
#
#   function computeKSNSig3(md5Hash) {
#       // ... 原 kuaiShoSignCore.js 内容 ...
#       return __NS_sig3;
#   }
#   // Node: module.exports = { computeKSNSig3 };
