#!/bin/bash
# Validate the final macOS Rust bundle, not the removed Python launcher.
set -euo pipefail

APP_PATH=${1:-src-tauri/target/release/bundle/macos/D助手.app}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
[ -d "$APP_PATH" ]
[ -x "$APP_PATH/Contents/MacOS/dyauthreply" ]
[ -x "$APP_PATH/Contents/MacOS/dy-agent" ]
codesign --verify --deep --strict "$APP_PATH"
node "$ROOT/scripts/client/audit_native_bundle.mjs" "$APP_PATH"
printf '%s\n' 'NATIVE_MACOS_BUNDLE_VERIFY_PASS'
