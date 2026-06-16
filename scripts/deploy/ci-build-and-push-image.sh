#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# CI：构建前端 + 构建并推送 backend Docker 镜像
#
# 环境变量（在 Gitee Go 流水线变量中配置）：
#   DOCKER_REGISTRY      例如 registry.cn-hangzhou.aliyuncs.com
#   DOCKER_REPOSITORY    例如 lianshenglong/zq-backend
#   DOCKER_USERNAME
#   DOCKER_PASSWORD
#   VITE_GLOB_API_URL    可选，默认 /basic-api/
#   IMAGE_TAG            可选，默认 git commit 短 SHA
# -----------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

: "${DOCKER_REGISTRY:?请设置 DOCKER_REGISTRY}"
: "${DOCKER_REPOSITORY:?请设置 DOCKER_REPOSITORY}"
: "${DOCKER_USERNAME:?请设置 DOCKER_USERNAME}"
: "${DOCKER_PASSWORD:?请设置 DOCKER_PASSWORD}"

export VITE_GLOB_API_URL="${VITE_GLOB_API_URL:-/basic-api/}"
IMAGE_TAG="${IMAGE_TAG:-$(git -C "${PROJECT_ROOT}" rev-parse --short=12 HEAD)}"
DEPLOY_BACKEND_IMAGE="${DOCKER_REGISTRY}/${DOCKER_REPOSITORY}:${IMAGE_TAG}"

echo "==> 1/3 构建前端"
bash "${SCRIPT_DIR}/ci-build-frontend.sh"

echo "==> 2/3 登录镜像仓库 ${DOCKER_REGISTRY}"
echo "${DOCKER_PASSWORD}" | docker login "${DOCKER_REGISTRY}" -u "${DOCKER_USERNAME}" --password-stdin

echo "==> 3/3 构建并推送镜像 ${DEPLOY_BACKEND_IMAGE}"
docker build -t "${DEPLOY_BACKEND_IMAGE}" "${PROJECT_ROOT}/backend-django"
docker push "${DEPLOY_BACKEND_IMAGE}"

echo "${DEPLOY_BACKEND_IMAGE}" > "${PROJECT_ROOT}/.deploy-image-tag"
echo "✅ 镜像已推送: ${DEPLOY_BACKEND_IMAGE}"
