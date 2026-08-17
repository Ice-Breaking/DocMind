#!/bin/bash
# DocMind automated backup script
# Usage: ./scripts/backup.sh [backup_dir]
# Recommended: Add to crontab for daily backups
#   0 2 * * * /path/to/scripts/backup.sh /path/to/backups

set -euo pipefail

# Configuration
BACKUP_DIR="${1:-./backups}"
CONTAINER_NAME="docmind"
RETENTION_DAYS=14
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_PATH="${BACKUP_DIR}/${TIMESTAMP}"

echo "[backup] Starting backup at ${TIMESTAMP}..."

# Create backup directory
mkdir -p "${BACKUP_PATH}"

# 1. SQLite online backup (safe for WAL mode)
echo "[backup] Backing up SQLite databases..."
docker exec "${CONTAINER_NAME}" python -c "
import sqlite3, os
os.makedirs('/tmp/backup', exist_ok=True)
for db in ['data/chat.db', 'data/cache.db']:
    if os.path.exists(db):
        src = sqlite3.connect(db)
        dst = sqlite3.connect(f'/tmp/backup/{os.path.basename(db)}')
        src.backup(dst)
        src.close()
        dst.close()
        print(f'  backed up {db}')
"

# Copy backup files from container
docker cp "${CONTAINER_NAME}:/tmp/backup/chat.db" "${BACKUP_PATH}/chat.db" 2>/dev/null || true
docker cp "${CONTAINER_NAME}:/tmp/backup/cache.db" "${BACKUP_PATH}/cache.db" 2>/dev/null || true

# Clean up temp files in container
docker exec "${CONTAINER_NAME}" rm -rf /tmp/backup

# 2. Backup trace log
echo "[backup] Backing up trace log..."
docker cp "${CONTAINER_NAME}:/app/data/trace_log.jsonl" "${BACKUP_PATH}/trace_log.jsonl" 2>/dev/null || true

# 3. Backup knowledge base (bind mount, directly accessible)
echo "[backup] Backing up knowledge base..."
if [ -d "./docs/knowledge" ]; then
    tar -czf "${BACKUP_PATH}/knowledge.tar.gz" -C ./docs knowledge/
    echo "  knowledge base archived"
fi

# 4. Create compressed archive
echo "[backup] Creating archive..."
tar -czf "${BACKUP_DIR}/docmind-backup-${TIMESTAMP}.tar.gz" -C "${BACKUP_DIR}" "${TIMESTAMP}"
rm -rf "${BACKUP_PATH}"

# 5. Cleanup old backups (keep last N days)
echo "[backup] Cleaning up backups older than ${RETENTION_DAYS} days..."
find "${BACKUP_DIR}" -name "docmind-backup-*.tar.gz" -mtime +${RETENTION_DAYS} -delete

# Summary
BACKUP_FILE="${BACKUP_DIR}/docmind-backup-${TIMESTAMP}.tar.gz"
BACKUP_SIZE=$(du -h "${BACKUP_FILE}" | cut -f1)
echo "[backup] Complete: ${BACKUP_FILE} (${BACKUP_SIZE})"
