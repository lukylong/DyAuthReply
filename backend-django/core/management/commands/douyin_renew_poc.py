#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
bd-ticket 自动续期 PoC（验证驱动，零副作用）：用现有有效 cookie(+ 私钥) 调
`creator.douyin.com/aweme/v1/creator/im/user_token/v2`，验证抖音是否允许仅凭
cookie/私钥**刷新** ts_sign / sdk_cert / token，从而免去反复手动重导 web_protect/keys。

本命令**只发请求、只打印响应、不写回任何 storage**，用于判定续期是否可行：
  - 若响应里出现 ts_sign / sdk_cert / token / ticket 等可刷新字段 → PoC 候选通过，
    再人工核对字段语义后，才在 health 里接入自动续期（见 plan 的 p2-renew-integrate）。
  - 若返回 401/风控/无相关字段 → PoC 不通过，保持「探活告警 + 引导重导入」保底方案。

典型用法：
  A) 用已导入账号验证：
     python manage.py douyin_renew_poc <account_id> --raw
  B) 用抓包 Cookie 头验证：
     python manage.py douyin_renew_poc --cookie-header "sessionid=...; ..." --ua "Mozilla/5.0 ..." --raw
  其它：
     --method GET                 # 默认 POST；端点真实方法不确定时可切换试探
     --body-file payload.json     # 自定义请求体（默认空 {}）
     --url <full_url>             # 覆盖默认端点，便于试探其它续期端点
     --proxy http://127.0.0.1:9090 / --insecure   # 配合 Proxyman 抓包对照
"""
from __future__ import annotations

import asyncio
import json

from django.core.management.base import BaseCommand, CommandError

from core.douyin.douyin_account_model import DouyinAccount

_DEFAULT_URL = "https://creator.douyin.com/aweme/v1/creator/im/user_token/v2"
# 续期响应里「可能」出现的、值得关注的刷新字段（命中即视为 PoC 候选通过）
_RENEW_KEYS = ("ts_sign", "sdk_cert", "client_cert", "token", "ticket", "user_token")


def _parse_cookie_header(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (raw or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        if name.strip():
            out[name.strip()] = value.strip()
    return out


def _find_renew_fields(obj, found: set[str]) -> None:
    """递归扫描 JSON，记录命中的续期相关字段名。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in _RENEW_KEYS and v:
                found.add(k)
            _find_renew_fields(v, found)
    elif isinstance(obj, list):
        for it in obj:
            _find_renew_fields(it, found)


class Command(BaseCommand):
    help = "bd-ticket 自动续期 PoC：调 user_token/v2 验证能否用 cookie 刷新续期凭证（只读，不写回）"

    def add_arguments(self, parser):
        parser.add_argument("account_id", nargs="?", default=None,
                            help="DouyinAccount ID（需已导入有效 cookie）；给 --cookie-header 时可省略")
        parser.add_argument("--cookie-header", default=None, help="抓包复制的 Cookie 整行")
        parser.add_argument("--cookie-file", default=None, help="从文件读取 Cookie 整行")
        parser.add_argument("--ua", default=None, help="User-Agent（建议从抓包请求头复制）")
        parser.add_argument("--proxy", default=None, help="代理地址，如 http://127.0.0.1:9090")
        parser.add_argument("--insecure", action="store_true", help="跳过 TLS 校验（配合 Proxyman MITM）")
        parser.add_argument("--method", choices=["POST", "GET"], default="POST", help="请求方法（默认 POST）")
        parser.add_argument("--url", default=_DEFAULT_URL, help="续期端点（默认 user_token/v2）")
        parser.add_argument("--body-file", default=None, help="自定义请求体 JSON 文件（默认空 {}）")
        parser.add_argument("--raw", action="store_true", help="打印响应前 600 字节")

    def handle(self, *args, **options):
        account_id = options["account_id"]
        cookie_header = options["cookie_header"]
        if not cookie_header and options["cookie_file"]:
            from pathlib import Path
            cookie_header = Path(options["cookie_file"]).read_text(encoding="utf-8").strip()

        if account_id:
            account = DouyinAccount.objects.filter(id=account_id).first()
            if account is None:
                raise CommandError(f"账号不存在: {account_id}")
            if options["ua"]:
                account.user_agent = options["ua"]
            if options["proxy"]:
                account.proxy_url = options["proxy"]
        else:
            if not cookie_header:
                raise CommandError("未提供 account_id 时必须传 --cookie-header / --cookie-file")
            from types import SimpleNamespace
            account = SimpleNamespace(id="adhoc-renew-poc", user_agent=options["ua"] or "",
                                      proxy_url=options["proxy"] or "", sec_uid="")

        body = b"{}"
        if options["body_file"]:
            from pathlib import Path
            body = Path(options["body_file"]).read_bytes()

        cookies = _parse_cookie_header(cookie_header) if cookie_header else None
        ok = asyncio.run(self._run(account, options, cookies, body))
        if not ok:
            raise CommandError("PoC 未确认续期可行（详见上方输出）")

    async def _run(self, account, options, cookies, body: bytes) -> bool:
        from core.douyin.runtime.transport.http_protocol import _BASE_CREATOR_JSON_HEADERS
        from core.douyin.runtime.transport.js_sign_provider import JsSignProvider
        from core.douyin.runtime.transport.sign_types import SignerUnavailable

        provider = JsSignProvider(verify_tls=not options["insecure"])
        await provider.start(account)
        if cookies:
            provider.set_cookies(cookies)
        if not provider.is_ready or not (await provider.get_cookies()):
            self.stdout.write(self.style.ERROR("provider 未就绪或无 cookie，无法验证"))
            return False

        url = options["url"]
        method = options["method"]
        self.stdout.write(f"→ {method} {url} account={account.id} body_len={len(body)}")
        try:
            resp = await provider.signed_fetch(
                method=method,
                url=url,
                body=None if method == "GET" else body,
                headers=_BASE_CREATOR_JSON_HEADERS,
            )
        except SignerUnavailable as e:
            self.stdout.write(self.style.ERROR(f"signed_fetch 失败: {e}"))
            return False
        finally:
            await provider.stop(account)

        self.stdout.write(f"← HTTP {resp.status} content_len={len(resp.content)} "
                          f"content_type={resp.headers.get('content-type', '?')}")
        if options["raw"]:
            self.stdout.write(f"  text[:600]={resp.text[:600]!r}")

        if resp.status in (401, 403):
            self.stdout.write(self.style.WARNING(
                f"🔴 PoC 不通过：HTTP {resp.status}（登录态/权限被拒）→ 无法仅凭 cookie 续期，保持告警保底。"))
            return False
        if not resp.ok:
            self.stdout.write(self.style.WARNING(
                f"🟡 PoC 存疑：HTTP {resp.status}（可能端点/方法不对）→ 可换 --method / --url 试探。"))
            return False

        payload = None
        try:
            payload = json.loads(resp.text)
        except Exception:  # noqa: BLE001
            self.stdout.write(self.style.WARNING("响应非 JSON，无法判定续期字段（可加 --raw 人工核对）"))
            return False

        status_code = payload.get("status_code") if isinstance(payload, dict) else None
        found: set[str] = set()
        _find_renew_fields(payload, found)
        self.stdout.write(f"  status_code={status_code} 命中续期字段={sorted(found) or '无'}")

        if found:
            self.stdout.write(self.style.SUCCESS(
                "✅ PoC 候选通过：响应含可刷新字段 " + ", ".join(sorted(found)) +
                "。\n   下一步请人工核对字段语义，再接入 health 自动续期（p2-renew-integrate）。"))
            return True

        self.stdout.write(self.style.WARNING(
            "🟡 PoC 未确认：HTTP 2xx 但响应无 ts_sign/sdk_cert/token 等续期字段 → "
            "该端点可能不负责续期，保持告警保底；可用 --raw 查看完整响应再判断。"))
        return False
