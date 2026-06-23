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

配合 cron 定时拉取（每天 03:00）：
  0 3 * * * cd /app && python manage.py sync_downloads >> /app/logs/sync_downloads.log 2>&1

源优先级：--source-base > 环境变量 DOWNLOAD_SYNC_SOURCE_BASE（均支持逗号分隔多个）
          > 内置镜像列表 + 直连兜底。
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand

# GitHub 国内加速镜像前缀（包裹完整 github 链接）。会随时间变动，按需增删。
DEFAULT_MIRRORS = (
    "https://ghfast.top",
    "https://gh-proxy.com",
    "https://ghproxy.net",
)
# 公开下载仓滚动 release 的 tag（CI 同步到该 tag）。
DIST_RELEASE_TAG = "client-latest"


class Command(BaseCommand):
    help = "从公开 GitHub Release（多镜像回退）同步安装包/插件到 DOWNLOAD_LOCAL_DIR"

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="无论大小是否一致都重新下载")
        parser.add_argument(
            "--only", default="", help="只同步指定项，逗号分隔：macos,windows,extension"
        )
        parser.add_argument(
            "--source-base",
            default="",
            help="覆盖下载源前缀（逗号分隔可配多个，按序回退）",
        )

    def _source_bases(self, override: str) -> list[str]:
        # 显式指定（命令行或环境变量）优先，逗号分隔多个
        explicit = override or os.environ.get("DOWNLOAD_SYNC_SOURCE_BASE", "")
        if explicit.strip():
            return [b.strip().rstrip("/") for b in explicit.split(",") if b.strip()]

        repo = settings.DOWNLOAD_DIST_REPO
        gh_release = f"https://github.com/{repo}/releases/download/{DIST_RELEASE_TAG}"
        bases = [f"{m.rstrip('/')}/{gh_release}" for m in DEFAULT_MIRRORS]
        # 直连兜底（latest/download，走重定向）
        bases.append(f"https://github.com/{repo}/releases/latest/download")
        return bases

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
            crange = resp.headers.get("Content-Range")
            if crange and "/" in crange:
                return int(crange.split("/")[-1])
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
        if written <= 0:
            tmp.unlink(missing_ok=True)
            raise httpx.HTTPError("empty body")
        tmp.replace(dest)
        return written

    def handle(self, *args, **options):
        local_dir = Path(settings.DOWNLOAD_LOCAL_DIR)
        local_dir.mkdir(parents=True, exist_ok=True)
        bases = self._source_bases(options["source_base"])
        targets = self._targets(options["only"])
        force = options["force"]

        self.stdout.write(f"目标目录：{local_dir}")
        self.stdout.write("候选源：\n  " + "\n  ".join(bases))

        synced = skipped = failed = 0
        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            for _key, filename in targets:
                dest = local_dir / filename
                local_size = dest.stat().st_size if dest.is_file() else -1

                # 取远端大小（从首个可用源），用于幂等判断
                remote_size = None
                for base in bases:
                    remote_size = self._remote_size(client, f"{base}/{filename}")
                    if remote_size is not None:
                        break

                if (
                    not force
                    and dest.is_file()
                    and remote_size is not None
                    and remote_size == local_size
                ):
                    self.stdout.write(f"  [跳过] {filename}（已是最新 {local_size}B）")
                    skipped += 1
                    continue

                ok = False
                for base in bases:
                    url = f"{base}/{filename}"
                    try:
                        written = self._download(client, url, dest)
                        self.stdout.write(
                            self.style.SUCCESS(f"  [同步] {filename} <- {base}（{written}B）")
                        )
                        synced += 1
                        ok = True
                        break
                    except httpx.HTTPError as exc:
                        self.stdout.write(self.style.WARNING(f"  [重试] {base} 失败：{exc}"))
                if not ok:
                    self.stderr.write(self.style.ERROR(f"  [失败] {filename}：所有源均不可用"))
                    failed += 1

        summary = f"完成：同步 {synced}，跳过 {skipped}，失败 {failed}"
        if failed:
            self.stderr.write(self.style.ERROR(summary))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS(summary))
