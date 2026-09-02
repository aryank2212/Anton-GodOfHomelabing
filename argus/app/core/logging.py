"""Structured JSON logging shared across Argus.

Every log line is emitted as a single JSON object and carries an optional
``request_id`` (set by the request middleware). Collectors and engines pass
extra context (``collector``, ``source``, ``content_id``, ``entity_id``,
``hypothesis_id``, ...) via ``extra=`` and it is merged into the payload
automatically.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_RESERVED = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
    "request_id",
}


def _is_serializable(value: object) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line (structured logging)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        payload.update(
            {
                key: value
                for key, value in record.__dict__.items()
                if key not in _RESERVED and _is_serializable(value)
            }
        )
        payload.setdefault("request_id", request_id_var.get())
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Configure the root logger with a single JSON stream handler."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access", "httpx", "feedparser"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
