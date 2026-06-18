#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# 在目标服务器上执行：拉取镜像并滚动更新 Compose 服务
#
# 必需环境变量：
#   DEPLOY_BACKEND_IMAGE   例如 ghcr.io/owner/repo/backend:abc123
#
# 可选：
#   DEPLOY_PATH            项目目录，默认 /opt/DyAuthReply
#   COMPOSE_FILES          默认 docker-compose.yml:docker-compose.deploy.yml
#   RUN_MIGRATE            true|false，默认 true
#   DEPLOY_SCHEDULER       true|false，默认 false
#   DEPLOY_WORKERS         true|false，默认 false
#   GHCR_USER / GHCR_TOKEN 私有镜像仓库登录（拉取 ghcr.io 时）
# -----------------------------------------------------------------------------
set -euo pipefail

DEPLOY_PATH="${DEPLOY_PATH:-/opt/DyAuthReply}"
COMPOSE_FILES="${COMPOSE_FILES:-docker-compose.yml:docker-compose.deploy.yml}"
RUN_MIGRATE="${RUN_MIGRATE:-true}"
DEPLOY_SCHEDULER="${DEPLOY_SCHEDULER:-false}"
DEPLOY_WORKERS="${DEPLOY_WORKERS:-false}"

if [[ -z "${DEPLOY_BACKEND_IMAGE:-}" ]]; then
  echo "❌ 请设置 DEPLOY_BACKEND_IMAGE" >&2
  exit 1
fi

cd "${DEPLOY_PATH}"
export DEPLOY_BACKEND_IMAGE

if [[ -n "${LICENSE_LEASE_PRIVATE_KEY:-}" ]]; then
  export LICENSE_LEASE_PRIVATE_KEY
fi
if [[ -n "${LICENSE_LEASE_PRIVATE_KEY_B64:-}" ]]; then
  export LICENSE_LEASE_PRIVATE_KEY_B64
fi
if [[ -n "${LICENSE_LEASE_PUBLIC_KEY:-}" ]]; then
  export LICENSE_LEASE_PUBLIC_KEY
fi
if [[ -n "${LICENSE_LEASE_PUBLIC_KEY_B64:-}" ]]; then
  export LICENSE_LEASE_PUBLIC_KEY_B64
fi
if [[ -n "${LICENSE_LEASE_TTL_MINUTES:-}" ]]; then
  export LICENSE_LEASE_TTL_MINUTES
fi
if [[ -n "${LICENSE_LEASE_RENEW_SKEW_SECONDS:-}" ]]; then
  export LICENSE_LEASE_RENEW_SKEW_SECONDS
fi
if [[ -n "${ENABLE_HOSTED_ACCOUNT_SCHEDULER_JOBS:-}" ]]; then
  export ENABLE_HOSTED_ACCOUNT_SCHEDULER_JOBS
fi

compose() {
  docker compose -f "${COMPOSE_FILES}" "$@"
}

echo "==> 部署镜像: ${DEPLOY_BACKEND_IMAGE}"
echo "==> 工作目录: ${DEPLOY_PATH}"

if [[ -n "${GHCR_TOKEN:-}" && -n "${GHCR_USER:-}" ]]; then
  echo "==> 登录 ghcr.io ..."
  echo "${GHCR_TOKEN}" | docker login ghcr.io -u "${GHCR_USER}" --password-stdin
elif [[ -n "${DOCKER_PASSWORD:-}" && -n "${DOCKER_USERNAME:-}" && -n "${DOCKER_REGISTRY:-}" ]]; then
  echo "==> 登录镜像仓库 ${DOCKER_REGISTRY} ..."
  echo "${DOCKER_PASSWORD}" | docker login "${DOCKER_REGISTRY}" -u "${DOCKER_USERNAME}" --password-stdin
fi

echo "==> 拉取镜像 ..."
compose pull backend || true

if [[ "${DEPLOY_SCHEDULER}" == "true" ]]; then
  compose --profile hosted pull scheduler || true
fi

if [[ "${DEPLOY_WORKERS}" == "true" ]]; then
  compose pull douyin-worker-0 douyin-worker-1 douyin-worker-2 || true
fi

echo "==> 启动基础设施 ..."
compose up -d postgres redis

echo "==> 等待数据库就绪 ..."
for _ in $(seq 1 30); do
  if compose exec -T postgres pg_isready -U "${POSTGRES_USER:-zq}" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "==> 更新 backend ..."
compose up -d --no-build backend

if [[ "${DEPLOY_SCHEDULER}" == "true" ]]; then
  echo "==> 更新 scheduler ..."
  compose --profile hosted up -d --no-build scheduler
else
  echo "==> 跳过 scheduler 部署（DEPLOY_SCHEDULER=false）"
fi

if [[ "${RUN_MIGRATE}" == "true" ]]; then
  echo "==> 数据库迁移 ..."
  compose exec -T backend python manage.py migrate --noinput
fi

if [[ "${DEPLOY_WORKERS}" == "true" ]]; then
  echo "==> 更新抖音 worker（3 分片）..."
  compose --profile douyin up -d --no-build --remove-orphans \
    douyin-worker-0 douyin-worker-1 douyin-worker-2
else
  echo "==> 跳过抖音 worker 部署（DEPLOY_WORKERS=false）"
fi

echo "==> 服务状态"
compose ps
compose --profile hosted ps
compose --profile douyin ps

echo "✅ 部署完成"
