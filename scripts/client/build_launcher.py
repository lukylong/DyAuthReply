#!/usr/bin/env python3
"""Build PyInstaller launcher and copy to Tauri externalBin directory."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "launcher.spec"
DIST_DIR = ROOT / "dist"
BINARIES_DIR = ROOT / "dyauthreply-client" / "desktop" / "src-tauri" / "binaries"


def launcher_target_name() -> str:
    system = sys.platform
    machine = platform.machine().lower()

    if system == "darwin":
        arch = "aarch64" if machine in {"arm64", "aarch64"} else "x86_64"
        return f"launcher-{arch}-apple-darwin"
    if system == "win32":
        return "launcher-x86_64-pc-windows-msvc.exe"

    arch = "aarch64" if machine in {"arm64", "aarch64"} else "x86_64"
    return f"launcher-{arch}-unknown-linux-gnu"


def launcher_source_path() -> Path:
    if sys.platform == "win32":
        return DIST_DIR / "launcher.exe"
    return DIST_DIR / "launcher"


def ensure_node_available() -> None:
    if not shutil.which("node"):
        raise SystemExit("错误: 构建机未安装 Node.js，launcher 将无法内嵌签名运行时")


def build_launcher() -> None:
    ensure_node_available()
    node_path = shutil.which("node")
    print(f"Node: {subprocess.check_output(['node', '--version'], text=True).strip()} @ {node_path}")
    print("PyInstaller 打包 launcher...")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm"],
        cwd=ROOT,
        check=True,
    )

    src = launcher_source_path()
    if not src.is_file():
        raise SystemExit(f"错误: PyInstaller 产物不存在: {src}")

    dest = BINARIES_DIR / launcher_target_name()
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    if sys.platform != "win32":
        dest.chmod(dest.stat().st_mode | 0o111)

    size_mb = dest.stat().st_size / (1024 * 1024)
    print(f"✅ launcher → {dest} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    build_launcher()
