#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""bd-ticket 凭证来源审计（兼容保留历史命令名）。

抖音已不再通过 ``im/user_token/v2`` 返回 token / sdk_cert / ts_sign；这些字段由登录
响应的 ``bd-ticket-guard-server-data`` 响应头或同名 Cookie 下发。本命令只在本地审计
已保存凭证或待导入 Cookie，不发起网络请求，也不写回 storage。
"""
from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.douyin.douyin_account_model import DouyinAccount
from core.douyin.runtime.credential import (
    parse_bd_ticket_from_cookie,
    parse_cookie_header,
    parse_keys,
    parse_ticket_guard_server_data,
)
from core.douyin.runtime.storage import load_storage_state

_REQUIRED_SEND_FIELDS = ("ticket", "ts_sign", "client_cert", "private_key")


class Command(BaseCommand):
    help = "审计 bd-ticket 登录响应凭证（旧 user_token/v2 已停用；本命令不发网络请求）"

    def add_arguments(self, parser):
        parser.add_argument("account_id", nargs="?", default=None, help="已导入的 DouyinAccount ID")
        parser.add_argument("--cookie-header", default=None, help="待校验的 Cookie 整行")
        parser.add_argument("--cookie-file", default=None, help="从文件读取待校验 Cookie")
        parser.add_argument(
            "--server-data",
            default=None,
            help="bd-ticket-guard-server-data（base64/URL 编码或兼容 JSON）",
        )
        parser.add_argument("--keys-file", default=None, help="含 ec_privateKey 的 keys JSON 文件")
        parser.add_argument("--json", action="store_true", help="输出不含凭证值的 JSON 结果")

    def handle(self, *args, **options):
        account_id = options["account_id"]
        state: dict = {}
        sources: list[str] = []

        if account_id:
            if not DouyinAccount.objects.filter(id=account_id).exists():
                raise CommandError(f"账号不存在: {account_id}")
            state = load_storage_state(str(account_id)) or {}
            sources.append("storage")

        cookie_header = options["cookie_header"]
        if not cookie_header and options["cookie_file"]:
            cookie_header = Path(options["cookie_file"]).read_text(encoding="utf-8").strip()
        if not account_id and not cookie_header and not options["server_data"]:
            raise CommandError("请提供 account_id、--cookie-header/--cookie-file 或 --server-data")

        bd = dict(state.get("_bd_ticket") or {})
        if cookie_header:
            cookie_bd = parse_bd_ticket_from_cookie(parse_cookie_header(cookie_header))
            if cookie_bd:
                bd.update(cookie_bd)
                sources.append("login_response_cookie")
        if options["server_data"]:
            bd.update(parse_ticket_guard_server_data(options["server_data"]))
            sources.append("login_response_header")
        if options["keys_file"]:
            bd.update(parse_keys(Path(options["keys_file"]).read_text(encoding="utf-8")))
            sources.append("keys_file")

        present = {field: bool(str(bd.get(field) or "").strip()) for field in _REQUIRED_SEND_FIELDS}
        missing = [field for field, ok in present.items() if not ok]
        result = {
            "success": not missing,
            "network_request": False,
            "strategy": "login_response_reimport",
            "sources": sources,
            "fields_present": present,
            "missing": missing,
        }
        rendered = json.dumps(result, ensure_ascii=False, sort_keys=True)
        if options["json"]:
            self.stdout.write(rendered)
        else:
            self.stdout.write("bd-ticket 来源审计（不发起网络请求）")
            self.stdout.write(f"来源: {', '.join(sources) or '无'}")
            self.stdout.write(f"字段: {json.dumps(present, ensure_ascii=False, sort_keys=True)}")

        if missing:
            raise CommandError(
                "发送凭证不完整，缺少 " + ", ".join(missing)
                + "；请重新登录后用新版扩展导入 server_data + keys"
            )
        if not options["json"]:
            self.stdout.write(self.style.SUCCESS("发送凭证完整；旧 user_token/v2 路径未被调用"))
