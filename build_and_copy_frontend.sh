#!/bin/bash
# -----------------------------------------------------------------------------
# 前端构建并同步至 Django 后端目录（本地发布入口）
# CI 使用 scripts/deploy/ci-build-frontend.sh
# -----------------------------------------------------------------------------
set -e
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
exec bash "${SCRIPT_DIR}/scripts/deploy/ci-build-frontend.sh"
