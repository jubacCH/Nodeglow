"""Process-wide logging setup.

Environment:

``LOG_LEVEL``  ``DEBUG`` | ``INFO`` (default) | ``WARNING`` | ``ERROR`` | ``CRITICAL``
``LOG_FORMAT`` ``text`` (default, human-readable) | ``json`` (one object per line,
               for Loki/Elastic/Datadog and friends)

Both formats carry the request ID set by :mod:`request_id` (``-`` outside a
request). Uvicorn's own loggers (``uvicorn``, ``uvicorn.error``,
``uvicorn.access``) are routed through the same handler, so the access log has
the same shape and the same request ID as the application log.

Call :func:`configure_logging` once, as early as possible in main.py: uvicorn
installs its own config before it imports the app, and this replaces it.
Stdlib only — no extra dependency for the JSON formatter.
"""
from __future__ import annotations

import json
import logging
import logging.config
import os
from datetime import datetime, timezone

from request_id import get_request_id

DEFAULT_LEVEL = "INFO"
TEXT_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] [%(request_id)s] %(message)s"

_VALID_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
# Libraries that log every single operation at INFO (apscheduler: every job
# run, every 30 s; httpx: every outgoing request). Held at WARNING unless
# LOG_LEVEL=DEBUG, so INFO stays readable.
_CHATTY_LOGGERS = ("apscheduler", "httpx", "httpcore")

# Attributes every LogRecord has; anything else was passed via ``extra=`` and
# is worth keeping in the JSON output.
_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message", "asctime", "request_id", "color_message", "taskName",
}


class RequestIdFilter(logging.Filter):
    """Stamp ``record.request_id`` from the request contextvar."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", None):
            record.request_id = get_request_id() or "-"
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, msg, request_id, extras."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", None) or get_request_id(),
        }
        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def _resolve_level(level: str | None) -> str:
    value = (level or os.getenv("LOG_LEVEL") or DEFAULT_LEVEL).strip().upper()
    return value if value in _VALID_LEVELS else DEFAULT_LEVEL


def _resolve_format(fmt: str | None) -> str:
    value = (fmt or os.getenv("LOG_FORMAT") or "text").strip().lower()
    return value if value in ("text", "json") else "text"


def build_config(level: str | None = None, fmt: str | None = None) -> dict:
    """Return the dictConfig for the given (or env-provided) level and format."""
    level = _resolve_level(level)
    fmt = _resolve_format(fmt)
    loggers = {
        name: {"handlers": [], "propagate": True, "level": "NOTSET"}
        for name in _UVICORN_LOGGERS
    }
    for name in _CHATTY_LOGGERS:
        loggers[name] = {"level": "NOTSET" if level == "DEBUG" else "WARNING"}
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"request_id": {"()": RequestIdFilter}},
        "formatters": {
            "text": {"format": TEXT_FORMAT},
            "json": {"()": JsonFormatter},
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
                "formatter": fmt,
                "filters": ["request_id"],
            },
        },
        "root": {"level": level, "handlers": ["console"]},
        # uvicorn: drop its own handlers and let records propagate to root, so
        # they get the same format and request ID. Level is inherited.
        "loggers": loggers,
    }


def configure_logging(level: str | None = None, fmt: str | None = None) -> dict:
    """Apply the logging configuration. Safe to call more than once."""
    config = build_config(level, fmt)
    logging.config.dictConfig(config)
    return config
