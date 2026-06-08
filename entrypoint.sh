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

echo "[1/4] Starting Docker daemon (DinD) via supervisor ..."
/usr/bin/supervisord -c /etc/supervisor/conf.d/wolfhost.conf &
SUPERVISOR_PID=$!

echo "[2/4] Waiting for Docker daemon to respond ..."
WAIT_MAX=30
for i in $(seq 1 ${WAIT_MAX}); do
    if docker info --format '{{.ServerVersion}}' >/dev/null 2>&1; then
        DOCKER_VER=$(docker info --format '{{.ServerVersion}}' 2>/dev/null)
        echo "       Docker ${DOCKER_VER} is ready (${i}s)"
        break
    fi
    if [ "${i}" -eq "${WAIT_MAX}" ]; then
        echo "ERROR: Docker daemon failed to start after ${WAIT_MAX}s"
        cat "${LOG_DIR}/dockerd.log" 2>/dev/null | tail -20 || true
        exit 1
    fi
    sleep 1
done

echo "[3/4] Ensuring estidafa_bot_net network exists ..."
if docker network inspect estidafa_bot_net >/dev/null 2>&1; then
    echo "       Network estidafa_bot_net already exists"
else
    docker network create estidafa_bot_net --driver bridge \
        --subnet=172.21.0.0/16 \
        --gateway=172.21.0.1 \
        --label "wolfhost.managed=true"
    echo "       Network estidafa_bot_net created (172.21.0.0/16)"
fi

echo "[4/4] Verifying base container images ..."
for img in python:3.10-alpine php:8.2-alpine; do
    if docker image inspect "${img}" >/dev/null 2>&1; then
        echo "       ${img} ✓"
    else
        echo "       Pulling ${img} ..."
        docker pull "${img}" 2>&1 &
    fi
done
wait

echo ""
echo "🐺 Wolf Host is ready — FastAPI running on port 7860"
echo ""

wait ${SUPERVISOR_PID}
