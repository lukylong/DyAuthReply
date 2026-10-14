#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把已公开 GitHub Release 的安装包/插件同步到本机下载目录（自托管分发，国内直连快）。

仓库已设为公开，无需任何 token。为规避国内服务器直连 github.com 慢/失败，默认
**依次尝试多个 GitHub 国内加速镜像，最后兜底直连**，任一成功即止。

默认只在「远端大小与本地不一致 / 本地缺文件」时才下载，避免无谓流量；--force 强制重下。

典型用法（在 backend 容器内或本机后端环境）：
  python manage.py sync_downloads                 # 自动多镜像回退
  python manage.py sync_downloads --force
  python manage.py sync_downloads --only macos,windows
  python manage.py sync_downloads --source-base "https://你的镜像/https://github.com/owner/repo/releases/download/client-latest"

Web 进程已每 10 分钟自动同步（core.license.download_sync），本命令仅用于手动补救。

源优先级：--source-base > 环境变量 DOWNLOAD_SYNC_SOURCE_BASE（均支持逗号分隔多个）
          > 内置镜像列表 + 直连兜底。
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from core.license.download_sync import sync_release


class Command(BaseCommand):
    help = "从公开 GitHub Release（多镜像回退）同步安装包/插件到 DOWNLOAD_LOCAL_DIR，并发布版本标记"

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="无论大小是否一致都重新下载")
        parser.add_argument(
            "--only", default="", help="只同步指定项，逗号分隔：macos,windows,extension（不更新版本标记）"
        )
        parser.add_argument(
            "--source-base",
            default="",
            help="覆盖下载源前缀（逗号分隔可配多个，按序回退）",
        )

    def handle(self, *args, **options):
        try:
            result = sync_release(
                force=options["force"], only=options["only"], source_base=options["source_base"]
            )
        except RuntimeError as exc:
            self.stderr.write(self.style.ERROR(f"同步失败：{exc}"))
            raise SystemExit(1)
        if result["status"] in {"unreachable", "busy"}:
            self.stderr.write(self.style.ERROR(f"未同步：{result}"))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS(f"完成：{result}"))
