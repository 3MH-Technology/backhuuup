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

echo "[1/2] Starting FastAPI application ..."
echo "[2/2] Ready — port 7860"
echo ""
echo "🐺 Wolf Host is running!"
echo "   Developer: الذئب الأبيض 🐺"
echo "   Dashboard: /"
echo "   API:       /api/"
echo ""

cd "${APP_DIR}"
exec python -m uvicorn main:app \
    --host 0.0.0.0 \
    --port 7860 \
    --log-level info \
    --workers 1 \
    --no-access-log \
    --proxy-headers \
    --forwarded-allow-ips='*'
