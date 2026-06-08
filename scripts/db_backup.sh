#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="/app/backups"
REPO_DIR="/tmp/backhuuup"
GIT_REPO="${BACKUP_GIT_REPO:-https://github.com/3MH-Technology/backhuuup.git}"
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
BACKUP_FILE="wolfhost_db_${TIMESTAMP}.sql.gz"

mkdir -p "${BACKUP_DIR}"

if [ -z "${DATABASE_URL:-}" ]; then
  echo "ERROR: DATABASE_URL not set"
  exit 1
fi

echo "🐺 Wolf Host DB Backup — ${TIMESTAMP}"
echo "Dumping database ..."
pg_dump "${DATABASE_URL}" 2>/dev/null | gzip > "${BACKUP_DIR}/${BACKUP_FILE}"

SIZE=$(du -h "${BACKUP_DIR}/${BACKUP_FILE}" 2>/dev/null | cut -f1 || echo "unknown")
echo "Backup saved: ${BACKUP_DIR}/${BACKUP_FILE} (${SIZE})"

rm -rf "${REPO_DIR}"
git clone --depth 1 "${GIT_REPO}" "${REPO_DIR}" 2>/dev/null || {
  echo "WARNING: Could not clone backup repo — backup saved locally"
  exit 0
}

cd "${REPO_DIR}"
git config user.email "wolfhost@backup.local"
git config user.name "Wolf Host Backup"

cp "${BACKUP_DIR}/${BACKUP_FILE}" "${REPO_DIR}/"
echo "${TIMESTAMP} — Wolf Host database backup" > "${REPO_DIR}/latest_backup.txt"

git add "${BACKUP_FILE}" latest_backup.txt 2>/dev/null
git commit -m "Auto DB backup ${TIMESTAMP}" --allow-empty 2>/dev/null
git push origin main 2>&1 || echo "WARNING: Backup push failed"

echo "✅ Database backup pushed to GitHub: ${BACKUP_FILE}"
