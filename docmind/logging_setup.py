"""Structured logging configuration for DocMind."""
import logging
import sys

from docmind.pii import mask_pii

_CONFIGURED = False


class PIIMaskFilter(logging.Filter):
    def filter(self, record):
        if isinstance(record.msg, str):
            record.msg = mask_pii(record.msg)
        if record.args:
            # Mask string arguments too
            if isinstance(record.args, tuple):
                record.args = tuple(
                    mask_pii(a) if isinstance(a, str) else a for a in record.args
                )
        return True


def setup_logging(level: str = "INFO") -> None:
    """Configure root logger with structured format. Call once at startup."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(PIIMaskFilter())
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s|%(levelname)s|%(name)s|%(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(handler)

    # Suppress noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("gradio").setLevel(logging.WARNING)
