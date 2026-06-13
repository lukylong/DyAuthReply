#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
关键闸门验证：用 LocalSignProvider（纯 Python 本地签名 + httpx 直发，无浏览器）
对**只读** imapi 接口发一次真实请求，回答"imapi 是否接受本地 a_bogus+msToken 签名"。

只读、零副作用：默认打 get_by_user（拉收件箱增量），不发任何消息。

典型用法：
  A) 用已导入账号的 cookie 验证：
    python manage.py douyin_local_sign_check <account_id>

  B) 直接用抓包复制的 Cookie 头验证（无需建账号/导入文件，最快）：
    python manage.py douyin_local_sign_check \
        --cookie-header "ttwid=...; sessionid=...; msToken=...; odin_tt=..." \
        --ua "Mozilla/5.0 (...) Chrome/124.0.0.0 Safari/537.36" --raw

  其它：
    --endpoint list_conversations   # 换只读接口
    --proxy http://127.0.0.1:9090   # 指定代理（可走 Proxyman 便于对照抓包）
    --raw                           # 打印响应前 200 字节

从 Proxyman 拿 Cookie 头：随便点开一条 creator.douyin.com / imapi.douyin.com 的请求，
复制其请求头里的 Cookie 整行，粘到 --cookie-header 即可。UA 同理从请求头复制最准。

结果判读：
  - HTTP 200 + protobuf 可解码 + status_code==0  → 本地签名被接受（绿灯，可灰度）
  - HTTP 4xx/200 但返回 JSON 错误页 / status_code!=0 / 风控  → 本地签名不被接受：
      多半是缺 X-Argus（mssdk）头或公共参数未对齐 → 走方案 A（imapi 留浏览器签名）
      或补 mssdk。详见 local_sign_provider.py 模块说明。
"""
from __future__ import annotations

import asyncio

from django.core.management.base import BaseCommand, CommandError

from core.douyin.douyin_account_model import DouyinAccount


def _parse_cookie_header(raw: str) -> dict[str, str]:
    """把 "a=1; b=2; c=3" 形式的 Cookie 头解析成 name → value。"""
    out: dict[str, str] = {}
    for part in (raw or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        if name:
            out[name] = value.strip()
    return out


class Command(BaseCommand):
    help = "用 LocalSignProvider 对只读 imapi 接口发真实请求，验证本地签名是否被接受"

    def add_arguments(self, parser):
        parser.add_argument(
            "account_id",
            nargs="?",
            default=None,
            help="DouyinAccount ID（需已导入有效 cookie）；提供 --cookie-header 时可省略",
        )
        parser.add_argument(
            "--cookie-header",
            default=None,
            help="直接传抓包复制的 Cookie 整行（无需账号/导入文件）",
        )
        parser.add_argument(
            "--cookie-file",
            default=None,
            help="从文件读取 Cookie 整行（避免 shell 转义问题）",
        )
        parser.add_argument("--ua", default=None, help="User-Agent（建议从抓包请求头复制）")
        parser.add_argument("--proxy", default=None, help="代理地址，如 http://127.0.0.1:9090")
        parser.add_argument(
            "--insecure",
            action="store_true",
            help="跳过 TLS 校验（容器出站被 Proxyman MITM 时用，避免假阴性）",
        )
        parser.add_argument(
            "--endpoint",
            choices=["get_by_user", "list_conversations"],
            default="get_by_user",
            help="验证用的只读接口（默认 get_by_user）",
        )
        parser.add_argument(
            "--raw",
            action="store_true",
            help="打印响应前 200 字节（hex）便于排查",
        )
        parser.add_argument(
            "--backend",
            choices=["local", "js"],
            default="js",
            help="签名后端：local=纯Python abogus；js=dy_ab.js+bd-ticket-guard（默认，推荐）",
        )

    def handle(self, *args, **options):
        account_id = options["account_id"]
        cookie_header = options["cookie_header"]
        if not cookie_header and options["cookie_file"]:
            from pathlib import Path as _P

            cookie_header = _P(options["cookie_file"]).read_text(encoding="utf-8").strip()
        endpoint_key = options["endpoint"]
        show_raw = options["raw"]

        # 模式 A：真账号；模式 B：临时账号 + 抓包 Cookie 头
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
                raise CommandError("未提供 account_id 时，必须传 --cookie-header 或 --cookie-file")
            from types import SimpleNamespace

            account = SimpleNamespace(
                id="adhoc-verify",
                user_agent=options["ua"] or "",
                proxy_url=options["proxy"] or "",
                sec_uid="",
            )

        cookies = _parse_cookie_header(cookie_header) if cookie_header else None
        result = asyncio.run(
            self._run(
                account, endpoint_key, show_raw, cookies, options["insecure"],
                options["backend"],
            )
        )
        if not result:
            raise CommandError("验证未通过（详见上方日志）")

    async def _run(
        self, account, endpoint_key: str, show_raw: bool, cookies=None, insecure: bool = False,
        backend: str = "js",
    ) -> bool:
        from core.douyin.runtime.transport.http_protocol import (
            _BASE_IM_HEADERS,
            _ENDPOINTS,
        )
        from core.douyin.runtime.transport.sign_provider import SignerUnavailable
        from core.douyin.runtime.transport.wire import (
            decode_get_by_user_response,
            decode_list_conversations_response,
            encode_get_by_user_request,
            encode_list_conversations_request,
        )

        if backend == "js":
            from core.douyin.runtime.transport.js_sign_provider import JsSignProvider
            provider = JsSignProvider(verify_tls=not insecure)
            self.stdout.write("签名后端 = js（JsSignProvider，dy_ab.js + bd-ticket-guard）")
        else:
            from core.douyin.runtime.transport.local_sign_provider import LocalSignProvider
            provider = LocalSignProvider(verify_tls=not insecure)
            self.stdout.write("签名后端 = local（LocalSignProvider，纯 Python abogus）")
        await provider.start(account)
        if cookies:
            provider.set_cookies(cookies)
            self.stdout.write(f"已注入抓包 Cookie：{len(cookies)} 项")
        if not provider.is_ready:
            self.stdout.write(self.style.ERROR("LocalSignProvider 未就绪（cookie/httpx 问题）"))
            return False
        if not (await provider.get_cookies()):
            self.stdout.write(self.style.ERROR("无可用 cookie，无法验证（请用 --cookie-header 或先导入账号登录态）"))
            return False

        try:
            endpoint = _ENDPOINTS[endpoint_key]
            if endpoint_key == "get_by_user":
                body, seq_id = encode_get_by_user_request(cursor_us=0, limit=20)
            else:
                body, seq_id = encode_list_conversations_request(limit=20)

            self.stdout.write(
                f"→ POST {endpoint['url']} account={account.id} "
                f"seq_id={seq_id} body_len={len(body)}"
            )

            try:
                resp = await provider.signed_fetch(
                    method=endpoint["method"],
                    url=endpoint["url"],
                    body=body,
                    headers=_BASE_IM_HEADERS,
                )
            except SignerUnavailable as e:
                self.stdout.write(self.style.ERROR(f"signed_fetch 失败: {e}"))
                return False

            self.stdout.write(
                f"← HTTP {resp.status} content_len={len(resp.content)} "
                f"content_type={resp.headers.get('content-type', '?')}"
            )
            if show_raw:
                self.stdout.write(f"  raw[:200]={resp.content[:200].hex()}")
                self.stdout.write(f"  text[:200]={resp.text[:200]!r}")

            if not resp.ok:
                self.stdout.write(self.style.ERROR(f"HTTP 非 2xx（status={resp.status}）→ 本地签名未被接受"))
                return False
            if not resp.content:
                self.stdout.write(self.style.ERROR("响应 body 为空 → 本地签名未被接受"))
                return False

            try:
                if endpoint_key == "get_by_user":
                    decoded = decode_get_by_user_response(resp.content)
                else:
                    decoded = decode_list_conversations_response(resp.content)
            except Exception as e:  # noqa: BLE001
                self.stdout.write(
                    self.style.ERROR(
                        f"protobuf 解码失败（多半返回的是 JSON 错误页）: {type(e).__name__}: {e}"
                    )
                )
                return False

            status_code = getattr(decoded, "status_code", None)
            self.stdout.write(f"  decoded.status_code={status_code}")
            if status_code == 0:
                self.stdout.write(
                    self.style.SUCCESS(
                        "✅ 绿灯：HTTP 200 + protobuf 可解码 + status_code==0 → "
                        "imapi 接受本地签名，可进入灰度。"
                    )
                )
                return True

            # 区分「签名/参数被拒」与「签名 OK 但登录态(cookie)无效」：
            # 服务端已回 HTTP 200 + protobuf 业务响应（而非验证码/风控页/JSON 错误页），
            # 说明请求已通过网关签名校验、进入业务层；若错误指向 session，则是 cookie
            # 无效而非签名问题——这种情况换真实 cookie 即可绿灯。
            blob = ((resp.text or "") + resp.content.decode("utf-8", "replace")).lower()
            if any(k in blob for k in ("session", "login", "登录", "登陆", "token")):
                self.stdout.write(
                    self.style.WARNING(
                        f"🟡 半绿灯：HTTP 200 + protobuf 业务响应，status_code={status_code}，"
                        "错误指向登录态(session)。\n"
                        "   → 说明【签名 + 公共参数已被 imapi 接受】，仅当前 cookie 无效。"
                        "用真实有效 cookie 重试即可绿灯。"
                    )
                )
                return False

            self.stdout.write(
                self.style.WARNING(
                    f"⚠️ status_code={status_code}!=0 且非登录态错误 → 签名被拒/风控，"
                    "或公共参数未对齐，走方案 A 或补 mssdk。"
                )
            )
            return False
        finally:
            await provider.stop(account)
