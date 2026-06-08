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

# Detect available storage driver
STORAGE_DRIVER="overlay2"
if ! mount | grep -q overlay; then
    STORAGE_DRIVER="vfs"
    echo "       [!] overlay not available, using vfs storage driver"
fi

echo "[1/4] Starting Docker daemon (DinD) ..."
dockerd \
    --tls=false \
    --host=unix:///var/run/docker.sock \
    --storage-driver=${STORAGE_DRIVER} \
    --log-level=warn \
    --iptables=true \
    --ip-forward=true \
    --bridge=none \
    > "${LOG_DIR}/dockerd.log" 2>&1 &

DOCKER_PID=$!
echo "       Docker PID: ${DOCKER_PID} (driver: ${STORAGE_DRIVER})"

echo "[2/4] Waiting for Docker daemon to respond ..."
WAIT_MAX=45
for i in $(seq 1 ${WAIT_MAX}); do
    if docker info --format '{{.ServerVersion}}' >/dev/null 2>&1; then
        DOCKER_VER=$(docker info --format '{{.ServerVersion}}' 2>/dev/null)
        echo "       Docker ${DOCKER_VER} is ready (${i}s)"
        break
    fi
    if [ "${i}" -eq "${WAIT_MAX}" ]; then
        echo "ERROR: Docker daemon failed to start after ${WAIT_MAX}s"
        echo ""
        echo "═══════════════ DOCKER LOGS ═══════════════"
        cat "${LOG_DIR}/dockerd.log" 2>/dev/null | tail -40 || true
        echo "═══════════════════════════════════════════"
        echo ""
        echo "TIP: Hugging Face Spaces need --privileged=true enabled."
        echo "Go to: Settings > Docker > --privileged=true"
        echo ""
        echo "Also check that DATABASE_URL is set as a Space secret."
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
        --label "wolfhost.managed=true" || true
    echo "       Network created"
fi

echo "[4/4] Pulling base images ..."
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
echo "🐺 Wolf Host is ready — FastAPI on port 7860"
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
