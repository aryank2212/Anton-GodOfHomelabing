import os
import json
import logging
import logging.handlers
from pathlib import Path
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "request_id"):
            log_entry["request_id"] = record.request_id
        if hasattr(record, "user_id"):
            log_entry["user_id"] = record.user_id
        if hasattr(record, "duration_ms"):
            log_entry["duration_ms"] = record.duration_ms
        return json.dumps(log_entry)


def setup_logging(config: dict):
    log_config = config.get("logging", {})
    level = getattr(logging, log_config.get("level", "INFO").upper(), logging.INFO)
    log_format = log_config.get("format", "json")

    root = logging.getLogger()
    root.setLevel(level)

    for handler in root.handlers[:]:
        root.removeHandler(handler)

    log_file = log_config.get("file")
    if log_file:
        log_dir = os.path.dirname(log_file)
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        rotation = log_config.get("rotation", "daily")
        max_bytes = log_config.get("max_bytes", 10485760)
        backup_count = log_config.get("backup_count", 7)

        if rotation == "daily":
            handler = logging.handlers.TimedRotatingFileHandler(
                log_file, when="midnight", interval=1, backupCount=backup_count
            )
        else:
            handler = logging.handlers.RotatingFileHandler(
                log_file, maxBytes=max_bytes, backupCount=backup_count
            )
        handler.setLevel(level)

        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(level)

        if log_format == "json":
            formatter = JSONFormatter()
        else:
            formatter = logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
            )

        handler.setFormatter(formatter)
        stream_handler.setFormatter(formatter)
        root.addHandler(handler)
        root.addHandler(stream_handler)
    else:
        handler = logging.StreamHandler()
        handler.setLevel(level)
        if log_format == "json":
            handler.setFormatter(JSONFormatter())
        else:
            handler.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
            )
        root.addHandler(handler)

    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("sentence_transformers").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
