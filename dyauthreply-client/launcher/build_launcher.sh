#!/usr/bin/env bash
# 构建 PyInstaller launcher 并复制到 Tauri externalBin 目录
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
python3 "$ROOT/scripts/client/build_launcher.py"
echo "下一步: cd dyauthreply-client/desktop && npm run tauri build"
