#!/usr/bin/env python3
"""DocMind 定时备份（backup sidecar 容器入口，每日 BACKUP_HOUR 点执行）。

为什么不用宿主机 crontab/launchd：macOS TCC 保护 ~/Documents，launchd
拉起的进程无权读取项目文件（实测 Operation not permitted）；Docker Desktop
的文件共享已获授权，sidecar 在容器内执行完全绕开该问题，且随 compose
部署到任何机器都成立（Linux 服务器无需额外配 cron）。

备份内容（与 scripts/backup.sh 对齐）：
  chat.db / cache.db   sqlite3 在线备份 API（WAL 安全，一致性快照）
  uploads/avatars/kb_docs  用户数据——会话消息引用这些文件，丢失即永久死链
  trace_log.jsonl      调用链日志
不备份（可重建）：index/ 向量缓存、ocr/tts/preview 缓存、语义缓存

启动即补跑：距上次备份超过 24h（含首次）先备一次再进入每日循环——
覆盖「Mac 睡眠错过 03:00」「部署新环境立即有备份点」两种场景。
"""
import os
import shutil
import sqlite3
import sys
import tarfile
import time
from datetime import datetime, timedelta
from pathlib import Path

SRC = Path(os.getenv("BACKUP_SOURCE", "/source"))          # docmind-data 卷（只读挂载）
OUT = Path(os.getenv("BACKUP_OUT", "/app/data/backups"))   # 宿主机 ./data/backups
KNOWLEDGE_DIR = Path(os.getenv("BACKUP_KNOWLEDGE_DIR", "/app/docs/knowledge"))
RETENTION_DAYS = int(os.getenv("BACKUP_RETENTION_DAYS", "14"))
BACKUP_HOUR = int(os.getenv("BACKUP_HOUR", "3"))
STALE_HOURS = 24

# 卷内直接拷贝的目录/文件（无一致性要求，cp 即可）
COPY_ITEMS = ["uploads", "avatars", "kb_docs", "trace_log.jsonl"]
# SQLite 库：必须走在线备份 API，直接拷 .db 会漏掉 WAL 里未合并的事务
SQLITE_DBS = ["chat.db", "cache.db"]


def backup_once() -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    work = OUT / ts
    work.mkdir(parents=True, exist_ok=True)

    for name in SQLITE_DBS:
        src, dst = SRC / name, work / name
        if not src.exists():
            print(f"[backup] 跳过 {name}（不存在）", flush=True)
            continue
        s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        d = sqlite3.connect(dst)
        with d:
            s.backup(d)
        s.close()
        d.close()
        print(f"[backup] 已备份 {name}（{dst.stat().st_size // 1024}KB）", flush=True)

    for item in COPY_ITEMS:
        p = SRC / item
        if not p.exists():
            continue
        if p.is_dir():
            shutil.copytree(p, work / item)
        else:
            shutil.copy2(p, work / item)
        print(f"[backup] 已备份 {item}/", flush=True)

    if KNOWLEDGE_DIR.is_dir():
        with tarfile.open(work / "knowledge.tar.gz", "w:gz") as tf:
            tf.add(KNOWLEDGE_DIR, arcname="knowledge")
        print("[backup] 已备份 docs/knowledge", flush=True)

    archive = OUT / f"docmind-backup-{ts}.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(work, arcname=ts)
    shutil.rmtree(work)

    cutoff = time.time() - RETENTION_DAYS * 86400
    removed = 0
    for old in OUT.glob("docmind-backup-*.tar.gz"):
        if old.stat().st_mtime < cutoff:
            old.unlink()
            removed += 1
    print(f"[backup] 完成: {archive.name} "
          f"({archive.stat().st_size // 1024}KB，清理过期 {removed} 份)", flush=True)
    return archive


def last_backup_age_hours() -> float:
    archives = sorted(OUT.glob("docmind-backup-*.tar.gz"),
                      key=lambda p: p.stat().st_mtime)
    if not archives:
        return float("inf")
    return (time.time() - archives[-1].stat().st_mtime) / 3600


def next_run_at() -> datetime:
    """下一个 BACKUP_HOUR 点（已过今天该时刻则顺延到明天）"""
    now = datetime.now()
    run = now.replace(hour=BACKUP_HOUR, minute=0, second=0, microsecond=0)
    if run <= now:
        run += timedelta(days=1)
    return run


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if "--once" in sys.argv:          # 手动立即备份：docker exec <backup容器> python3 -u scripts/backup_scheduler.py --once
        backup_once()
        return
    age = last_backup_age_hours()
    if age >= STALE_HOURS:
        print(f"[backup] 距上次备份 {age:.1f}h（>={STALE_HOURS}h），启动即补跑", flush=True)
        backup_once()
    run = next_run_at()
    print(f"[backup] 调度启动：每日 {BACKUP_HOUR:02d}:00 备份，下次 {run:%Y-%m-%d %H:%M}",
          flush=True)
    while True:
        time.sleep(max(1.0, (run - datetime.now()).total_seconds()))
        try:
            backup_once()
        except Exception as e:  # noqa: BLE001 - 备份失败不影响主服务，次日重试
            print(f"[backup] 失败（次日重试）: {e}", flush=True)
        run = next_run_at()


if __name__ == "__main__":
    main()
