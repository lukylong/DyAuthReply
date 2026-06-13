#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: sign/poc.py
@Desc: 签名层 PoC 自测（不依赖 Django / 浏览器 / 网络）

运行：
    docker compose exec backend python -m core.douyin.runtime.transport.sign.poc

校验三件事：
  1. 内置 SM3 与标准向量一致（保证 a_bogus 的哈希基座正确）
  2. ABogus 能为一个 GET 参数串生成 a_bogus（web 视频接口形态）
  3. ABogus 能为 imapi /v1/message/send 形态（POST + body）生成 a_bogus
出现合法长度的 a_bogus 即说明纯 Python 签名链路打通。
"""
from __future__ import annotations

from core.douyin.runtime.transport.sign._gmssl_compat import bytes_to_list, sm3_hash
from core.douyin.runtime.transport.sign.abogus import ABogus
from core.douyin.runtime.transport.sign.signer import sign_params


def _check_sm3() -> bool:
    got = sm3_hash(bytes_to_list(b"abc"))
    expect = "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0"
    ok = got == expect
    print(f"[1] SM3(abc) = {got}  {'OK' if ok else 'FAIL expect=' + expect}")
    return ok


def _check_get() -> bool:
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    ab = ABogus(user_agent=ua)
    params = (
        "device_platform=webapp&aid=6383&channel=channel_pc_web&pc_client_type=1"
        "&version_code=190500&cookie_enabled=true&screen_width=1920&screen_height=1080"
    )
    new_params, abogus, used_ua, _ = ab.generate_abogus(params=params, body="")
    ok = bool(abogus) and "a_bogus=" in new_params and len(abogus) > 100
    print(f"[2] GET  a_bogus len={len(abogus)} sample={abogus[:32]}...  {'OK' if ok else 'FAIL'}")
    return ok


def _check_imapi_post() -> bool:
    """imapi /v1/message/send 形态：POST + protobuf body（这里用占位 body 串验证可签名）。"""
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    ab = ABogus(user_agent=ua, options=[0, 1, 14])  # POST
    params = (
        "device_platform=webapp&aid=6383&channel=channel_pc_web&version_code=190500"
        "&cookie_enabled=true&screen_width=1920&screen_height=1080"
    )
    body = "0a2430303a313a31323334353637383930"  # 占位（真实为 protobuf hex/bytes）
    new_params, abogus, _ua, _b = ab.generate_abogus(params=params, body=body)
    ok = bool(abogus) and "a_bogus=" in new_params
    print(f"[3] POST a_bogus len={len(abogus)} sample={abogus[:32]}...  {'OK' if ok else 'FAIL'}")
    return ok


def _check_signer() -> bool:
    """统一入口：cookie 带 msToken 时应复用；不带时随机补；a_bogus 追加在末尾。"""
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    fp = "1920|1080|1920|1040|0|0|0|0|1920|1040|1920|1080|1920|1040|24|24|Win32"
    params = "device_platform=webapp&aid=6383&channel=channel_pc_web&version_code=190500"

    r1 = sign_params(params, user_agent=ua, fp=fp, cookies={"msToken": "COOKIE_TOKEN_123"})
    reuse_ok = r1.ms_token == "COOKIE_TOKEN_123" and "msToken=COOKIE_TOKEN_123" in r1.query
    # a_bogus 必须排在 msToken 之后（即对含 msToken 的串签名）
    tail_ok = "&a_bogus=" in r1.query and r1.query.index("a_bogus=") > r1.query.index("msToken=")

    r2 = sign_params(params, user_agent=ua, fp=fp, cookies=None, method="POST")
    rand_ok = len(r2.ms_token) >= 100 and "msToken=" in r2.query and "&a_bogus=" in r2.query

    # 同 fp+UA 下，params 不变两次签名 a_bogus 不同（含时间戳/随机），属正常
    ok = reuse_ok and tail_ok and rand_ok
    print(f"[4] signer reuse_cookie={reuse_ok} order_ok={tail_ok} random_post={rand_ok}  "
          f"{'OK' if ok else 'FAIL'}")
    return ok


def _check_js() -> bool:
    """vendored dy_ab.js（JS 签名引擎）能否产出 a_bogus。需 PyExecJS + Node + sign/js/。

    缺环境（未装 PyExecJS/Node 或未 vendoring dy_ab.js）时打印 SKIP 不阻塞纯 Python 自测。
    """
    try:
        from core.douyin.runtime.transport.sign import js_signer

        if not js_signer.is_available():
            print("[5] JS   引擎不可用（PyExecJS/Node/dy_ab.js 缺失）  SKIP")
            return True
        ab = js_signer.get_ab("device_platform=webapp&aid=6383&msToken=ABC123DEF456", "")
        ok = bool(ab) and len(ab) > 100
        print(f"[5] JS   a_bogus len={len(ab)} sample={ab[:32]}...  {'OK' if ok else 'FAIL'}")
        return ok
    except Exception as e:  # noqa: BLE001
        print(f"[5] JS   自测异常: {type(e).__name__}: {e}")
        return False


def main() -> None:
    print("=== Douyin sign PoC ===")
    results = [_check_sm3(), _check_get(), _check_imapi_post(), _check_signer(), _check_js()]
    print("=== RESULT:", "ALL OK" if all(results) else "HAS FAILURE", "===")


if __name__ == "__main__":
    main()
