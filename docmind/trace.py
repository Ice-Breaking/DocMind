"""调用链追踪：Langfuse / 本地 JSONL 双后端。

设计要点（面试可讲）：
- 配置了 LANGFUSE_PUBLIC_KEY/SECRET_KEY → 上报 Langfuse（云或自托管均可）
- 未配置 → 自动降级写本地 JSONL（data/trace_log.jsonl），零依赖也能看链路
- 追踪失败绝不影响主链路：所有上报均包在 try/except 里
- 用 contextmanager 统一两种后端的 span 生命周期
- 日志轮转：单文件 50MB，保留最近 5 个归档

查看本地日志：python scripts/view_traces.py
"""
import contextlib
import json
import logging
import os
import time
import uuid
from logging.handlers import RotatingFileHandler

from docmind import config
from docmind.pii import mask_pii

logger = logging.getLogger(__name__)

# 日志轮转配置
_MAX_BYTES = 50 * 1024 * 1024  # 50MB
_BACKUP_COUNT = 5  # 保留最近 5 个归档
_log_handler = None


def _try_init_langfuse():
    if not (config.LANGFUSE_PUBLIC_KEY and config.LANGFUSE_SECRET_KEY):
        return None
    try:
        from langfuse import Langfuse

        client = Langfuse(
            public_key=config.LANGFUSE_PUBLIC_KEY,
            secret_key=config.LANGFUSE_SECRET_KEY,
            host=config.LANGFUSE_HOST,
        )
        logger.info(f"Langfuse 追踪已启用 → {config.LANGFUSE_HOST}")
        return client
    except Exception as e:  # noqa: BLE001 - SDK 初始化失败降级本地日志
        logger.warning(f"Langfuse 初始化失败，降级为本地 JSONL 日志: {e}")
        return None


_langfuse = _try_init_langfuse()


def _mask_record(record: dict) -> dict:
    """Recursively mask PII in string values."""
    masked = {}
    for key, value in record.items():
        if isinstance(value, str):
            masked[key] = mask_pii(value)
        elif isinstance(value, dict):
            masked[key] = _mask_record(value)
        elif isinstance(value, list):
            masked[key] = [mask_pii(v) if isinstance(v, str) else v for v in value]
        else:
            masked[key] = value
    return masked


def _append_jsonl(record: dict) -> None:
    """追加日志到 JSONL 文件，支持自动轮转（50MB/文件，保留5个归档）"""
    global _log_handler
    try:
        os.makedirs(os.path.dirname(config.TRACE_LOG_PATH), exist_ok=True)

        # 懒初始化日志轮转 handler
        if _log_handler is None:
            _log_handler = RotatingFileHandler(
                config.TRACE_LOG_PATH,
                maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding='utf-8'
            )

        # 使用 handler 写入（自动轮转）
        _log_handler.emit(
            logging.LogRecord(
                name="trace",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg=json.dumps(record, ensure_ascii=False, default=str),
                args=(),
                exc_info=None
            )
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"追踪日志写入失败: {e}")


@contextlib.contextmanager
def span(name: str, kind: str = "span", **meta):
    """追踪一段执行（LLM 调用 / 工具执行）。

    yield 一个可变字典，调用方在执行后回填 output/usage：
        with span("llm-chat", kind="generation", model=m, input=msgs) as ctx:
            resp = ...
            ctx["output"] = ...
    kind='generation' 在 Langfuse 后端映射为 Generation 节点（带成本统计）。
    """
    data = dict(meta)
    start = time.time()

    if _langfuse is not None:
        try:
            lf_span = _langfuse.start_as_current_span(
                name=name,
                model=data.get("model") if kind == "generation" else None,
                input=data.get("input"),
            )
            try:
                yield data
            except Exception:
                lf_span.update(level="ERROR")
                raise
            finally:
                try:
                    update = {"output": data.get("output")}
                    if kind == "generation" and data.get("usage"):
                        update["usage_details"] = data["usage"]
                    lf_span.update(**update)
                except Exception:  # noqa: BLE001
                    pass
                lf_span.end()
            return
        except Exception as e:  # noqa: BLE001 - Langfuse 故障不影响主链路
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            logger.warning(f"Langfuse 上报失败（本次降级 JSONL）: {e}")

    # 本地 JSONL 后端（未配置 Langfuse 或降级）
    record = {
        "id": uuid.uuid4().hex[:12],
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "name": name,
        "kind": kind,
    }
    try:
        yield data
        record["status"] = "ok"
    except Exception:
        record["status"] = "error"
        raise
    finally:
        record["duration_ms"] = round((time.time() - start) * 1000)
        record.update({k: v for k, v in data.items() if v is not None})
        _append_jsonl(_mask_record(record))
