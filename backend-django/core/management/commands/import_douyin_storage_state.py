#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
导入宿主机导出的 Douyin Playwright storage_state.json 到当前账号。

典型用法：
    python manage.py import_douyin_storage_state <account_id> /app/tmp/douyin/<account_id>.json
"""
from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.douyin.douyin_account_model import DouyinAccount
from core.douyin.runtime.storage import save_storage_state


class Command(BaseCommand):
    help = "导入宿主机导出的 Douyin storage_state.json 到指定账号"

    def add_arguments(self, parser):
        parser.add_argument("account_id", help="DouyinAccount ID")
        parser.add_argument("storage_state_file", help="Playwright storage_state.json 文件路径")
        parser.add_argument(
            "--status",
            choices=["online", "pending"],
            default="online",
            help="导入后账号状态：online=在线，pending=待验证/待校验",
        )

    def handle(self, *args, **options):
        account_id = options["account_id"]
        storage_state_file = Path(options["storage_state_file"]).expanduser()
        target_status = 1 if options["status"] == "online" else 0

        account = DouyinAccount.objects.filter(id=account_id).first()
        if account is None:
            raise CommandError(f"账号不存在: {account_id}")

        if not storage_state_file.exists():
            raise CommandError(f"storage_state 文件不存在: {storage_state_file}")

        try:
            state = json.loads(storage_state_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"storage_state 不是有效 JSON: {exc}") from exc

        if not isinstance(state, dict):
            raise CommandError("storage_state 顶层必须是 JSON 对象")

        cookies = state.get("cookies") or []
        origins = state.get("origins") or []
        if not isinstance(cookies, list) or not isinstance(origins, list):
            raise CommandError("storage_state 结构非法：cookies/origins 必须是数组")

        rel_path = save_storage_state(account_id, state)
        now = timezone.now()
        account.storage_state_path = rel_path
        account.status = target_status
        account.last_login_at = now if target_status == 1 else account.last_login_at
        account.pending_verification_type = None
        account.pending_verification_at = None
        account.pending_verification_until = None
        account.save(update_fields=[
            "storage_state_path",
            "status",
            "last_login_at",
            "pending_verification_type",
            "pending_verification_at",
            "pending_verification_until",
            "sys_update_datetime",
        ])

        self.stdout.write(
            self.style.SUCCESS(
                "已导入登录态: "
                f"account={account_id} cookies={len(cookies)} origins={len(origins)} "
                f"status={'在线' if target_status == 1 else '待校验'} path={rel_path}"
            )
        )

