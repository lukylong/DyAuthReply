#!/bin/bash
# zq-platform backend 启动脚本
# 按 ROLE 环境变量分发：web | scheduler | douyin-worker | migrate | shell
set -euo pipefail

ROLE="${1:-${ROLE:-web}}"

start_visual_stack() {
    local display="${DISPLAY:-:99}"
    local display_num="${display#:}"
    local geometry="${DOUYIN_XVFB_GEOMETRY:-1440x900x24}"
    local novnc_port="${DOUYIN_NOVNC_PORT:-6080}"
    local vnc_port="${DOUYIN_VNC_PORT:-5900}"
    local vnc_password="${DOUYIN_VNC_PASSWORD:-}"

    pkill -f "Xvfb ${display}" 2>/dev/null || true
    pkill -f "x11vnc -display ${display}" 2>/dev/null || true
    pkill -f "websockify --web=/usr/share/novnc/ ${novnc_port}" 2>/dev/null || true
    pkill fluxbox 2>/dev/null || true
    rm -f "/tmp/.X${display_num}-lock" "/tmp/.X11-unix/X${display_num}"

    echo "🖥️  启动虚拟桌面 DISPLAY=${display} geometry=${geometry}"
    Xvfb "${display}" -screen 0 "${geometry}" -ac +extension RANDR >/tmp/xvfb.log 2>&1 &
    export DISPLAY="${display}"
    sleep 2
    if ! pgrep -f "Xvfb ${display}" >/dev/null; then
        echo "❌ Xvfb 启动失败"
        cat /tmp/xvfb.log || true
        exit 1
    fi

    fluxbox >/tmp/fluxbox.log 2>&1 &

    if [[ -n "${vnc_password}" ]]; then
        mkdir -p /tmp/x11vnc
        x11vnc -storepasswd "${vnc_password}" /tmp/x11vnc/passwd >/tmp/x11vnc-passwd.log 2>&1
        x11vnc -display "${display}" -rfbport "${vnc_port}" -rfbauth /tmp/x11vnc/passwd -forever -shared >/tmp/x11vnc.log 2>&1 &
    else
        x11vnc -display "${display}" -rfbport "${vnc_port}" -nopw -forever -shared >/tmp/x11vnc.log 2>&1 &
    fi

    websockify --web=/usr/share/novnc/ "${novnc_port}" "127.0.0.1:${vnc_port}" >/tmp/novnc.log 2>&1 &
    echo "🌐 noVNC 已就绪: http://<host>:${novnc_port}/vnc.html"
}

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
        echo "🤖 启动抖音 worker（M2 里程碑，占位）"
        if [[ "${DOUYIN_ENABLE_VIRTUAL_DISPLAY:-true}" == "true" ]]; then
            start_visual_stack
        fi
        # TODO(M2): 换为 `exec python start_douyin_worker.py`
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
