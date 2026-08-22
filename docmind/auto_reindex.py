"""知识库自动重建调度器：上传/编辑/删除文档后自动触发增量重建。

设计：
- 防抖合并：变更后等待 REINDEX_DEBOUNCE 秒，窗口内的后续变更重置计时，
  只执行一次重建（用户连传 10 个文件 = 1 次增量重建，而非 10 次）
- 复用既有重建链路（assistants_api._do_reindex）：任务页可见进度，
  重建成功后自动清答案缓存
- 单库单线程：同一 KB 不会并发重建（重建链路自带任务记录与全局锁兜底）
"""
import logging
import threading

from docmind import config, store

logger = logging.getLogger(__name__)

# 防抖窗口（秒）：窗口内连续变更合并为一次重建。
# 取 60s：够用户批量拖完文件，又不至于传完等太久
REINDEX_DEBOUNCE = int(getattr(config, "REINDEX_DEBOUNCE", "60"))

_timers: dict[str, threading.Timer] = {}
_lock = threading.Lock()


def _run_reindex(kb_id: str) -> None:
    """后台执行增量重建（带任务记录，进度在知识库任务页可见）"""
    try:
        from docmind.assistants_api import _do_reindex
        task_id = store.create_ingest_task(kb_id, "*", "auto-reindex", "running",
                                           "检测到文档变更，自动重建索引…", "system")
        _do_reindex(kb_id, task_id)
        logger.info(f"自动重建完成 kb={kb_id} task#{task_id}")
    except Exception as e:  # noqa: BLE001 - 自动重建失败不影响主流程，可手动重建兜底
        logger.warning(f"自动重建失败 kb={kb_id}: {e}")


def _fire_and_clean(kb_id: str) -> None:
    """Timer 回调：先从待触发表移除自己，再执行重建"""
    with _lock:
        _timers.pop(kb_id, None)
    _run_reindex(kb_id)


def schedule_reindex(kb_id: str, delay: int | None = None) -> None:
    """调度一次防抖重建：窗口内重复调用只触发一次执行"""
    if not kb_id:
        return
    with _lock:
        timer = _timers.pop(kb_id, None)
        if timer is not None:
            timer.cancel()
        timer = threading.Timer(delay or REINDEX_DEBOUNCE,
                                _fire_and_clean, args=(kb_id,))
        timer.daemon = True
        timer.start()
        _timers[kb_id] = timer
        logger.info(f"已调度自动重建 kb={kb_id}（{delay or REINDEX_DEBOUNCE}s 防抖窗口）")


def pending_kbs() -> list[str]:
    """当前有防抖计时的知识库（测试/观测用）"""
    with _lock:
        return list(_timers)
