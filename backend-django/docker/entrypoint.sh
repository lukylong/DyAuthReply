#!/bin/bash
# zq-platform backend 启动脚本
# 按 ROLE 环境变量分发：web | scheduler | douyin-worker | migrate | shell
set -euo pipefail

ROLE="${1:-${ROLE:-web}}"

# -------------------------------------------------------------
# 等待依赖服务（PostgreSQL / Redis）
# -------------------------------------------------------------
wait_for() {
    local host="$1"
    local port="$2"
    local name="$3"
    local timeout="${4:-60}"
    echo "⏳ 等待 ${name} (${host}:${port}) ..."
    for _ in $(seq 1 "${timeout}"); do
        if nc -z "${host}" "${port}" 2>/dev/null; then
            echo "✅ ${name} 已就绪"
            return 0
        fi
        sleep 1
    done
    echo "❌ ${name} 在 ${timeout}s 内未就绪，退出" >&2
    exit 1
}

if [[ "${DATABASE_TYPE:-}" == "POSTGRESQL" && -n "${DATABASE_HOST:-}" ]]; then
    wait_for "${DATABASE_HOST}" "${DATABASE_PORT:-5432}" "PostgreSQL"
fi

if [[ -n "${REDIS_HOST:-}" ]]; then
    wait_for "${REDIS_HOST}" "${REDIS_PORT:-6379}" "Redis"
fi

# -------------------------------------------------------------
# 首次启动自动跑 migrate（只 web 角色执行，避免多进程并发抢锁）
# -------------------------------------------------------------
if [[ "${ROLE}" == "web" && "${AUTO_MIGRATE:-true}" == "true" ]]; then
    echo "🔧 执行数据库迁移 ..."
    python manage.py migrate --noinput || {
        echo "⚠️  migrate 失败，继续启动（可手动排查）" >&2
    }

    if [[ -f "db_init.json" && "${AUTO_LOADDATA:-false}" == "true" ]]; then
        echo "📦 加载初始化数据 db_init.json ..."
        python manage.py loaddata db_init.json || true
    fi
fi

# -------------------------------------------------------------
# 角色分发
# -------------------------------------------------------------
case "${ROLE}" in
    web)
        echo "🚀 启动 Django Web (uvicorn/ASGI) on :8000"
        exec python -m uvicorn application.asgi:application \
            --host 0.0.0.0 --port 8000 \
            --log-level "${UVICORN_LOG_LEVEL:-info}" \
            ${UVICORN_RELOAD:+--reload}
        ;;
    scheduler)
        echo "⏰ 启动 APScheduler 后台任务"
        exec python start_scheduler.py
        ;;
    douyin-worker)
        echo "🤖 启动抖音 worker（纯 HTTP 协议，无浏览器）"
        if [[ -f "start_douyin_worker.py" ]]; then
            exec python start_douyin_worker.py
        else
            echo "ℹ️  start_douyin_worker.py 尚未实现（等待 M2），进入 idle 模式"
            exec tail -f /dev/null
        fi
        ;;
    migrate)
        exec python manage.py migrate --noinput
        ;;
    shell)
        exec python manage.py shell
        ;;
    *)
        echo "未知 ROLE=${ROLE}，直接执行自定义命令：$*"
        exec "$@"
        ;;
esac
