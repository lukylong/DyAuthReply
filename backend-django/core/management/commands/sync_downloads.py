#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把已公开 GitHub Release 的安装包/插件同步到本机下载目录（自托管分发，国内直连快）。

仓库已设为公开，故无需任何 token，直接走 releases/latest/download 公链拉取。
默认仅在「远端大小与本地不一致 / 本地缺文件」时才下载，避免无谓流量；--force 强制重下。

典型用法（在 backend 容器内或本机后端环境）：
  python manage.py sync_downloads
  python manage.py sync_downloads --force
  python manage.py sync_downloads --only macos,windows

配合 cron 定时拉取（每天 03:00）：
  0 3 * * * cd /app && python manage.py sync_downloads >> /app/logs/sync_downloads.log 2>&1

下载源默认：https://github.com/<DOWNLOAD_DIST_REPO>/releases/latest/download/<文件名>
可用 --source-base 或环境变量 DOWNLOAD_SYNC_SOURCE_BASE 覆盖。
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "从公开 GitHub Release 同步安装包/插件到 DOWNLOAD_LOCAL_DIR"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="无论大小是否一致都重新下载",
        )
        parser.add_argument(
            "--only",
            default="",
            help="只同步指定项，逗号分隔：macos,windows,extension",
        )
        parser.add_argument(
            "--source-base",
            default="",
            help="覆盖下载源前缀（默认取公开仓 releases/latest/download）",
        )

    def _source_base(self, override: str) -> str:
        base = (
            override
            or os.environ.get("DOWNLOAD_SYNC_SOURCE_BASE", "")
            or f"https://github.com/{settings.DOWNLOAD_DIST_REPO}/releases/latest/download"
        )
        return base.rstrip("/")

    def _targets(self, only: str) -> list[tuple[str, str]]:
        mapping = {
            "macos": settings.DOWNLOAD_MACOS_FILE,
            "windows": settings.DOWNLOAD_WINDOWS_FILE,
            "extension": settings.DOWNLOAD_EXTENSION_FILE,
        }
        keys = [k.strip() for k in only.split(",") if k.strip()] if only else list(mapping)
        result: list[tuple[str, str]] = []
        for key in keys:
            if key not in mapping:
                self.stderr.write(self.style.WARNING(f"忽略未知项：{key}"))
                continue
            result.append((key, mapping[key]))
        return result

    def _remote_size(self, client: httpx.Client, url: str) -> int | None:
        try:
            resp = client.head(url)
            if resp.status_code >= 400:
                resp = client.get(url, headers={"Range": "bytes=0-0"})
            size = resp.headers.get("Content-Range")
            if size and "/" in size:
                return int(size.split("/")[-1])
            length = resp.headers.get("Content-Length")
            return int(length) if length else None
        except (httpx.HTTPError, ValueError):
            return None

    def _download(self, client: httpx.Client, url: str, dest: Path) -> int:
        tmp = dest.with_suffix(dest.suffix + ".part")
        written = 0
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                    fh.write(chunk)
                    written += len(chunk)
        tmp.replace(dest)
        return written

    def handle(self, *args, **options):
        local_dir = Path(settings.DOWNLOAD_LOCAL_DIR)
        local_dir.mkdir(parents=True, exist_ok=True)
        base = self._source_base(options["source_base"])
        targets = self._targets(options["only"])
        force = options["force"]

        self.stdout.write(f"下载源：{base}")
        self.stdout.write(f"目标目录：{local_dir}")

        synced = skipped = failed = 0
        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            for key, filename in targets:
                url = f"{base}/{filename}"
                dest = local_dir / filename
                remote_size = self._remote_size(client, url)
                local_size = dest.stat().st_size if dest.is_file() else -1

                if (
                    not force
                    and dest.is_file()
                    and remote_size is not None
                    and remote_size == local_size
                ):
                    self.stdout.write(f"  [跳过] {filename}（已是最新 {local_size}B）")
                    skipped += 1
                    continue

                try:
                    written = self._download(client, url, dest)
                    self.stdout.write(
                        self.style.SUCCESS(f"  [同步] {filename} <- {url}（{written}B）")
                    )
                    synced += 1
                except httpx.HTTPError as exc:
                    self.stderr.write(self.style.ERROR(f"  [失败] {filename}：{exc}"))
                    failed += 1

        summary = f"完成：同步 {synced}，跳过 {skipped}，失败 {failed}"
        if failed:
            self.stderr.write(self.style.ERROR(summary))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS(summary))
