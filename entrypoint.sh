#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/app"
LOG_DIR="${APP_DIR}/logs"
mkdir -p "${LOG_DIR}"

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║        🐺 Wolf Host — استضافة الذب هوست           ║"
echo "║     Autonomous Bot Hosting for Arab Developers   ║"
echo "║     Developer: الذئب الأبيض 🐺                    ║"
echo "║     Telegram:  @j49_c                            ║"
echo "║     Channel:   @O5O6J                            ║"
echo "║     X:         https://x.com/wolfhost_1          ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# Try storage drivers in order of preference
start_dockerd() {
    local driver=$1
    echo "       Trying storage driver: ${driver}"
    dockerd \
        --tls=false \
        --host=unix:///var/run/docker.sock \
        --storage-driver=${driver} \
        --log-level=warn \
        --iptables=true \
        --ip-forward=true \
        --bridge=none \
        > "${LOG_DIR}/dockerd.log" 2>&1 &
    echo $!
}

wait_for_docker() {
    local pid=$1
    local max=$2
    for i in $(seq 1 ${max}); do
        if docker info --format '{{.ServerVersion}}' >/dev/null 2>&1; then
            echo $(docker info --format '{{.ServerVersion}}' 2>/dev/null)
            return 0
        fi
        if ! kill -0 $pid 2>/dev/null; then
            return 1
        fi
        sleep 1
    done
    return 1
}

DOCKER_PID=""
DOCKER_VER=""

echo "[1/4] Starting Docker daemon (DinD) ..."

# Try overlay2 first, fall back to vfs
for driver in overlay2 vfs; do
    DOCKER_PID=$(start_dockerd $driver)
    DOCKER_VER=$(wait_for_docker $DOCKER_PID 20 || echo "")
    if [ -n "$DOCKER_VER" ]; then
        echo "       Docker ${DOCKER_VER} is ready (driver: ${driver})"
        break
    fi
    kill $DOCKER_PID 2>/dev/null || true
    sleep 1
    echo "       Driver ${driver} failed, trying next..."
done

if [ -z "$DOCKER_VER" ]; then
    echo "ERROR: Docker daemon failed to start"
    echo ""
    echo "═══════════════ DOCKER LOGS ═══════════════"
    cat "${LOG_DIR}/dockerd.log" 2>/dev/null | tail -30 || true
    echo "═══════════════════════════════════════════"
    echo ""
    echo "TIP: Enable --privileged=true in HF Space Settings > Docker"
    echo ""
    echo "Starting FastAPI without Docker (limited mode)..."
fi

echo "[2/4] Ensuring estidafa_bot_net network exists ..."
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
    if docker network inspect estidafa_bot_net >/dev/null 2>&1; then
        echo "       Network estidafa_bot_net already exists"
    else
        docker network create estidafa_bot_net --driver bridge \
            --subnet=172.21.0.0/16 \
            --gateway=172.21.0.1 \
            --label "wolfhost.managed=true" 2>/dev/null || true
        echo "       Network created"
    fi

    echo "[3/4] Pulling base images ..."
    for img in python:3.10-alpine php:8.2-alpine; do
        if docker image inspect "${img}" >/dev/null 2>&1; then
            echo "       ${img} ✓"
        else
            echo "       Pulling ${img} ..."
            docker pull "${img}" 2>&1 &
        fi
    done
    wait
fi

echo "[4/4] Starting FastAPI application ..."
echo ""
echo "🐺 Wolf Host is ready — port 7860"
echo ""

cd "${APP_DIR}"
exec /venv/bin/uvicorn main:app \
    --host 0.0.0.0 \
    --port 7860 \
    --log-level info \
    --workers 1 \
    --no-access-log \
    --proxy-headers \
    --forwarded-allow-ips='*'
