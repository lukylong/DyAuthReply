#!/usr/bin/env python3
"""Build PyInstaller launcher and copy to Tauri externalBin directory."""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "launcher.spec"
DIST_DIR = ROOT / "dist"
BINARIES_DIR = ROOT / "dyauthreply-client" / "desktop" / "src-tauri" / "binaries"
SIGN_JS_DIR = ROOT / "backend-django" / "core" / "douyin" / "runtime" / "transport" / "sign" / "js"

RUST_TARGET_NAMES: dict[str, str] = {
    "aarch64-apple-darwin": "launcher-aarch64-apple-darwin",
    "x86_64-apple-darwin": "launcher-x86_64-apple-darwin",
    "x86_64-pc-windows-msvc": "launcher-x86_64-pc-windows-msvc.exe",
    "i686-pc-windows-msvc": "launcher-i686-pc-windows-msvc.exe",
    "aarch64-unknown-linux-gnu": "launcher-aarch64-unknown-linux-gnu",
    "x86_64-unknown-linux-gnu": "launcher-x86_64-unknown-linux-gnu",
}


def launcher_target_name(rust_target: str | None = None) -> str:
    if rust_target:
        name = RUST_TARGET_NAMES.get(rust_target)
        if name:
            return name
        raise SystemExit(f"ERROR: unsupported rust target for launcher sidecar: {rust_target}")

    system = sys.platform
    machine = platform.machine().lower()

    if system == "darwin":
        arch = "aarch64" if machine in {"arm64", "aarch64"} else "x86_64"
        return f"launcher-{arch}-apple-darwin"
    if system == "win32":
        if machine in {"x86", "i386", "i686"}:
            return "launcher-i686-pc-windows-msvc.exe"
        return "launcher-x86_64-pc-windows-msvc.exe"

    arch = "aarch64" if machine in {"arm64", "aarch64"} else "x86_64"
    return f"launcher-{arch}-unknown-linux-gnu"


def launcher_source_path() -> Path:
    if sys.platform == "win32":
        return DIST_DIR / "launcher.exe"
    return DIST_DIR / "launcher"


def ensure_node_available() -> None:
    if not shutil.which("node"):
        raise SystemExit("ERROR: Node.js not found; launcher build requires Node for signing runtime")


def npm_executable() -> str:
    for name in ("npm.cmd", "npm.exe", "npm") if sys.platform == "win32" else ("npm",):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit("ERROR: npm not found; launcher build requires npm for signing runtime deps")


def ensure_sign_js_deps() -> None:
    """Install jsrsasign into sign/js/node_modules before PyInstaller bundles datas."""
    jsrsasign = SIGN_JS_DIR / "node_modules" / "jsrsasign"
    if jsrsasign.is_dir():
        print(f"sign/js deps ready: {jsrsasign}")
        return
    if not (SIGN_JS_DIR / "package-lock.json").is_file():
        raise SystemExit(f"ERROR: missing sign/js package-lock.json under {SIGN_JS_DIR}")
    print(f"Installing sign/js npm deps under {SIGN_JS_DIR} ...")
    subprocess.run(
        [npm_executable(), "ci", "--no-audit", "--no-fund"],
        cwd=SIGN_JS_DIR,
        check=True,
    )
    if not jsrsasign.is_dir():
        raise SystemExit("ERROR: jsrsasign still missing after npm ci in sign/js")


def build_launcher(rust_target: str | None = None) -> None:
    ensure_node_available()
    ensure_sign_js_deps()
    node_path = shutil.which("node")
    print(f"Node: {subprocess.check_output(['node', '--version'], text=True).strip()} @ {node_path}")
    if rust_target:
        print(f"Rust target: {rust_target}")
    print("Building launcher with PyInstaller...")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm"],
        cwd=ROOT,
        check=True,
    )

    src = launcher_source_path()
    if not src.is_file():
        raise SystemExit(f"ERROR: PyInstaller output missing: {src}")

    dest = BINARIES_DIR / launcher_target_name(rust_target)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    if sys.platform != "win32":
        dest.chmod(dest.stat().st_mode | 0o111)

    size_mb = dest.stat().st_size / (1024 * 1024)
    print(f"launcher copied to {dest} ({size_mb:.1f} MB)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build PyInstaller launcher sidecar for Tauri")
    parser.add_argument(
        "--rust-target",
        default=None,
        help="Rust target triple (e.g. x86_64-apple-darwin, i686-pc-windows-msvc)",
    )
    args = parser.parse_args()
    build_launcher(args.rust_target)


if __name__ == "__main__":
    main()
