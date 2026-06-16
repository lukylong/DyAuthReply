#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# 前端生产构建并同步到 backend-django/dist（CI / 本地发布共用）
#
# 环境变量（可选，覆盖 .env.production）：
#   VITE_GLOB_API_URL   例如 /basic-api/ 或 https://域名/basic-api/
#   VITE_BASE           默认 /manage/
# -----------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
WEB_DIR="${PROJECT_ROOT}/web"
DIST_SRC="${WEB_DIR}/apps/web-ele/dist"
DIST_DST="${PROJECT_ROOT}/backend-django/dist"

echo "🚀 构建前端 (web-ele)..."
cd "${WEB_DIR}"

if ! command -v pnpm >/dev/null 2>&1; then
  echo "❌ 未找到 pnpm，请先安装 Node.js >= 20 与 pnpm >= 9" >&2
  exit 1
fi

pnpm install --frozen-lockfile
pnpm run build:ele

if [[ ! -d "${DIST_SRC}" ]] || [[ ! -f "${DIST_SRC}/index.html" ]]; then
  echo "❌ 构建失败：未找到 ${DIST_SRC}/index.html" >&2
  exit 1
fi

echo "📦 同步产物 → backend-django/dist"
rm -rf "${DIST_DST}"
cp -a "${DIST_SRC}" "${DIST_DST}"

echo "✅ 前端构建完成: ${DIST_DST}"
ls -la "${DIST_DST}" | head -5
