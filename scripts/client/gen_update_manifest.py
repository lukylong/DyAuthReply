#!/usr/bin/env python3
"""生成 tauri-plugin-updater 的 latest.json 基准文件及多镜像变体。

镜像加速原理（见 task design「1. Tauri Updater + 镜像加速」）：
- tauri updater 的二进制下载地址来自所选 manifest 的 `platforms.*.url`，单 manifest 不支持多源。
- 因此每个镜像各生成一份 latest.json 变体，其 `url` 前缀指向该镜像；客户端按速度竞速选 manifest。
- minisign `signature` 对任何镜像下载的包都校验，镜像不可信也安全。

镜像 slug 规则必须与客户端（Rust `mirror_slug` / 前端默认清单）保持一致：
去掉协议头后保留字母数字并小写，例如 `https://ghproxy.net/` -> `ghproxynet`，变体文件名 `latest-ghproxynet.json`。
GitHub 原站（https://github.com/）不加前缀，输出 `latest.json`。

用法示例：
    python scripts/client/gen_update_manifest.py \
        --version 0.1.16 \
        --pub-date 2026-06-26T00:00:00Z \
        --notes "修复若干问题" \
        --windows-url https://github.com/lukylong/DyAuthReply-dist/releases/latest/download/DAssistant-windows-x64-setup.exe \
        --windows-sig-file DAssistant-windows-x64-setup.exe.sig \
        --macos-url https://github.com/lukylong/DyAuthReply-dist/releases/latest/download/DAssistant-macos-aarch64.app.tar.gz \
        --macos-sig-file DAssistant-macos-aarch64.app.tar.gz.sig \
        --mirrors "https://ghproxy.net/,https://gh-proxy.com/,https://ghfast.top/,https://github.com/" \
        --out-dir dist-manifests
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path


def mirror_slug(mirror: str) -> str:
    """与客户端一致：去协议头后保留字母数字并小写。"""
    s = mirror.strip()
    s = re.sub(r"^https?://", "", s)
    return re.sub(r"[^a-z0-9]", "", s.lower())


def is_origin(mirror: str) -> bool:
    m = mirror.strip().rstrip("/")
    return m in ("", "https://github.com", "http://github.com")


def with_mirror(url: str, mirror: str) -> str:
    """前缀型代理：镜像 URL = <mirror> + 完整 github 资源 URL。原站不加前缀。"""
    if is_origin(mirror):
        return url
    prefix = mirror.strip()
    if not prefix.endswith("/"):
        prefix += "/"
    return f"{prefix}{url}"


def read_sig(path: str | None) -> str:
    if not path:
        return ""
    return Path(path).read_text(encoding="utf-8").strip()


def build_manifest(version: str, notes: str, pub_date: str, platforms: dict) -> dict:
    return {
        "version": version,
        "notes": notes,
        "pub_date": pub_date,
        "platforms": platforms,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", required=True)
    ap.add_argument("--pub-date", required=True)
    ap.add_argument("--notes", default="")
    ap.add_argument("--windows-url", default="")
    ap.add_argument("--windows-sig-file", default="")
    ap.add_argument("--macos-url", default="")
    ap.add_argument("--macos-sig-file", default="")
    ap.add_argument(
        "--mirrors",
        required=True,
        help="逗号分隔的镜像前缀清单（含原站 https://github.com/ 兜底）",
    )
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    win_sig = read_sig(args.windows_sig_file)
    mac_sig = read_sig(args.macos_sig_file)

    if not args.windows_url and not args.macos_url:
        print("错误：至少需要 --windows-url 或 --macos-url 之一")
        return 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    mirrors = [m.strip() for m in args.mirrors.split(",") if m.strip()]
    written = []

    for mirror in mirrors:
        platforms: dict = {}
        if args.windows_url and win_sig:
            platforms["windows-x86_64"] = {
                "signature": win_sig,
                "url": with_mirror(args.windows_url, mirror),
            }
        if args.macos_url and mac_sig:
            platforms["darwin-aarch64"] = {
                "signature": mac_sig,
                "url": with_mirror(args.macos_url, mirror),
            }
        if not platforms:
            continue

        manifest = build_manifest(args.version, args.notes, args.pub_date, platforms)
        filename = "latest.json" if is_origin(mirror) else f"latest-{mirror_slug(mirror)}.json"
        target = out_dir / filename
        target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(str(target))
        print(f"已生成 {target}")

    if not written:
        print("错误：没有生成任何 manifest（检查 url / sig 是否齐全）")
        return 1

    # 始终确保存在原站 latest.json（即便镜像清单未显式包含原站）
    origin_path = out_dir / "latest.json"
    if str(origin_path) not in written:
        platforms = {}
        if args.windows_url and win_sig:
            platforms["windows-x86_64"] = {"signature": win_sig, "url": args.windows_url}
        if args.macos_url and mac_sig:
            platforms["darwin-aarch64"] = {"signature": mac_sig, "url": args.macos_url}
        manifest = build_manifest(args.version, args.notes, args.pub_date, platforms)
        origin_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已补充原站 {origin_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
