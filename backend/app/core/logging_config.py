"""
Structured logging configuration for DukaPOS backend.

Outputs JSON logs in production (easy ingestion by Datadog, CloudWatch, etc.)
and pretty human-readable logs in development.

Usage:
    from app.core.logging_config import setup_logging
    setup_logging()   # call once at startup in main.py
"""

import logging
import logging.config
import json
import traceback
from datetime import datetime, timezone
from app.core.config import settings


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line (machine-parseable)."""

    def format(self, record: logging.LogRecord) -> str:
        log: dict = {
            "ts":      datetime.now(timezone.utc).isoformat(),
            "level":   record.levelname,
            "logger":  record.name,
            "msg":     record.getMessage(),
            "module":  record.module,
            "func":    record.funcName,
            "line":    record.lineno,
        }
        # Attach any extra kwargs passed to the logger call
        for key, val in record.__dict__.items():
            if key not in (
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
            ):
                log[key] = val

        if record.exc_info:
            log["exception"] = traceback.format_exception(*record.exc_info)

        return json.dumps(log, default=str)


def setup_logging() -> None:
    """Configure root logger. Call once at application startup."""
    log_level = "DEBUG" if settings.DEBUG else "INFO"

    if settings.DEBUG:
        # Development: human-readable coloured output
        fmt = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
        datefmt = "%H:%M:%S"
        handlers = {
            "console": {
                "class":     "logging.StreamHandler",
                "formatter": "dev",
                "stream":    "ext://sys.stdout",
            }
        }
        formatters = {
            "dev": {"format": fmt, "datefmt": datefmt}
        }
    else:
        # Production: JSON per line
        handlers = {
            "console": {
                "class":     "logging.StreamHandler",
                "formatter": "json",
                "stream":    "ext://sys.stdout",
            }
        }
        formatters = {
            "json": {"()": _JsonFormatter}
        }

    logging.config.dictConfig({
        "version":                  1,
        "disable_existing_loggers": False,
        "formatters":               formatters,
        "handlers":                 handlers,
        "root": {
            "level":    log_level,
            "handlers": ["console"],
        },
        # Silence noisy third-party loggers
        "loggers": {
            "uvicorn.access":    {"level": "WARNING"},
            "sqlalchemy.engine": {"level": "WARNING"},
            "passlib":           {"level": "WARNING"},
        },
    })
    logging.getLogger(__name__).info(
        "Logging configured",
        extra={"level": log_level, "debug": settings.DEBUG},
    )
