#!/bin/bash
# DocMind 自动备份脚本（Docker 部署形态）
# Usage: ./scripts/backup.sh [backup_dir]   # 默认 data/backups（已 gitignore）
# 定时：macOS 用 launchd（见 scripts/com.docmind.backup.plist），Linux 用 crontab：
#   0 3 * * * /path/to/scripts/backup.sh /path/to/data/backups
#
# 备份内容：
#   chat.db/cache.db   SQLite 在线备份（WAL 安全，VACUUM-free 一致性快照）
#   uploads/avatars    用户上传图片与头像——会话消息引用这些文件，
#                      丢失即永久死链（实测教训：e2e 附件被清理后审计页破图）
#   kb_docs            知识库入库文档副本
#   trace.db           调用链日志（SQLite；trace_log.jsonl 仅为迁移前遗留）
#   docs/knowledge     知识库语料（bind mount 源）
# 不备份（可重建）：index/ 向量缓存、*_cache.db、ocr/tts/preview 缓存

set -euo pipefail

# launchd 环境的 PATH 极简（/usr/bin:/bin），找不到 docker；补齐常见安装位置
export PATH="/usr/local/bin:/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

# 配置
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${1:-${PROJECT_DIR}/data/backups}"
RETENTION_DAYS=14
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_PATH="${BACKUP_DIR}/${TIMESTAMP}"

# 容器名自动解析：compose 命名为 <项目前缀>-docmind-1（如 chat-1-docmind-1），
# 硬编码 "docmind" 在 compose 部署下必失败（实测踩坑）
CONTAINER=$(docker ps --filter "name=docmind" --filter "status=running" \
    --format '{{.Names}}' | head -1)
if [ -z "${CONTAINER}" ]; then
    echo "[backup] 错误: 找不到运行中的 docmind 容器" >&2
    exit 1
fi

echo "[backup] ${TIMESTAMP} 开始备份（容器: ${CONTAINER}）..."
mkdir -p "${BACKUP_PATH}"

# 1. SQLite 在线备份（WAL 模式安全）
echo "[backup] 备份数据库..."
docker exec "${CONTAINER}" python3 -c "
import sqlite3, os
os.makedirs('/tmp/backup', exist_ok=True)
for db in ['data/chat.db', 'data/cache.db', 'data/trace.db']:
    if os.path.exists(db):
        src = sqlite3.connect(db)
        dst = sqlite3.connect(f'/tmp/backup/{os.path.basename(db)}')
        src.backup(dst)
        src.close(); dst.close()
        print(f'  已备份 {db}')
"
docker cp "${CONTAINER}:/tmp/backup/chat.db" "${BACKUP_PATH}/chat.db" 2>/dev/null || true
docker cp "${CONTAINER}:/tmp/backup/cache.db" "${BACKUP_PATH}/cache.db" 2>/dev/null || true
docker exec "${CONTAINER}" rm -rf /tmp/backup

# 2. 用户数据：上传图片/头像/入库文档副本（消息引用，丢失即死链）
echo "[backup] 备份用户数据（uploads/avatars/kb_docs）..."
for d in uploads avatars kb_docs; do
    docker cp "${CONTAINER}:/app/data/${d}" "${BACKUP_PATH}/${d}" 2>/dev/null \
        && echo "  已备份 ${d}/" || echo "  跳过 ${d}/（不存在）"
done

# 3. trace 日志（SQLite 已在上面在线备份；JSONL 仅为迁移前遗留，存在则顺带拷走）
docker cp "${CONTAINER}:/app/data/trace_log.jsonl" "${BACKUP_PATH}/trace_log.jsonl" 2>/dev/null \
    && echo "  已备份 trace_log.jsonl" || true

# 4. 知识库语料（bind mount，宿主机直读）
if [ -d "${PROJECT_DIR}/docs/knowledge" ]; then
    tar -czf "${BACKUP_PATH}/knowledge.tar.gz" -C "${PROJECT_DIR}/docs" knowledge/
    echo "  已备份 docs/knowledge"
fi

# 5. 打包 + 清理中间目录
tar -czf "${BACKUP_DIR}/docmind-backup-${TIMESTAMP}.tar.gz" -C "${BACKUP_DIR}" "${TIMESTAMP}"
rm -rf "${BACKUP_PATH}"

# 6. 按天保留清理
find "${BACKUP_DIR}" -name "docmind-backup-*.tar.gz" -mtime +${RETENTION_DAYS} -delete

BACKUP_FILE="${BACKUP_DIR}/docmind-backup-${TIMESTAMP}.tar.gz"
echo "[backup] 完成: ${BACKUP_FILE} ($(du -h "${BACKUP_FILE}" | cut -f1))"
