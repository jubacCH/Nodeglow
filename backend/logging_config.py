"""Process-wide logging setup.

Environment:

``LOG_LEVEL``  ``DEBUG`` | ``INFO`` (default) | ``WARNING`` | ``ERROR`` | ``CRITICAL``
``LOG_FORMAT`` ``text`` (default, human-readable) | ``json`` (one object per line,
               for Loki/Elastic/Datadog and friends)

Both formats carry the request ID set by :mod:`request_id` (``-`` outside a
request). Uvicorn's own loggers (``uvicorn``, ``uvicorn.error``,
``uvicorn.access``) are routed through the same handler, so the access log has
the same shape and the same request ID as the application log.

Credential values are replaced by ``[redacted]`` in every record, the access
log included (:class:`RedactSecretsFilter`): ``token``, the OAuth token names,
``client_secret`` and ``code_verifier`` wherever they appear as
``name=value``; ``code`` and ``state`` on the single sign-on paths, whose
callback carries them in its query string.

Call :func:`configure_logging` once, as early as possible in main.py: uvicorn
installs its own config before it imports the app, and this replaces it.
Stdlib only — no extra dependency for the JSON formatter.
"""
from __future__ import annotations

import json
import logging
import logging.config
import os
import re
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


# Values that are credentials wherever they appear as ``name=value`` (a query
# string, a form body, an exception text): the installers' install token, the
# OAuth token names, the PKCE verifier, the client secret.
_ALWAYS_SECRET = re.compile(
    r"(?<![\w-])((?:token|id_token|access_token|refresh_token|client_secret|code_verifier)=)"
    r"[^&\s\"',;]*",
    re.IGNORECASE,
)
# Values that are credentials only on the single sign-on paths: ``code`` and
# ``state`` are ordinary words elsewhere (``?state=open&code=500``).
_SSO_PATH = "/api/auth/sso"
_SSO_SECRET = re.compile(
    r"(?<![\w-])((?:code|state|session_state)=)[^&\s\"',;]*",
    re.IGNORECASE,
)
# Argument types that are formatted to look for secrets: URLs (httpx logs
# ``httpx.URL`` objects). Anything else is left alone, so no object's
# ``__str__`` runs twice or runs where logging would not have run it.
_URL_TYPE_NAMES = frozenset({"URL"})


def redact_query(text: str) -> str:
    """``text`` with credential values replaced by ``[redacted]``."""
    if "=" not in text:
        return text
    text = _ALWAYS_SECRET.sub(lambda m: m.group(1) + "[redacted]", text)
    if _SSO_PATH in text:
        text = _SSO_SECRET.sub(lambda m: m.group(1) + "[redacted]", text)
    return text


def _redact_arg(value):
    if isinstance(value, str):
        return redact_query(value)
    if type(value).__name__ in _URL_TYPE_NAMES:
        try:
            text = str(value)
        except Exception:  # noqa: BLE001 - logging must not break the caller
            return value
        redacted = redact_query(text)
        return redacted if redacted != text else value
    return value


class RedactSecretsFilter(logging.Filter):
    """Replace credential values by ``[redacted]`` before a record is
    written: in the message, its arguments (uvicorn's access log passes the
    path as one), the exception text and stack, and ``extra=`` fields.

    Not covered: the text of an argument object other than a URL (it is not
    formatted here) — a secret inside such an object's ``str()`` would be
    written as it is. Never raises: on any problem the record goes out as it
    came in."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            self._redact(record)
        except Exception:  # noqa: BLE001 - logging must not break the caller
            pass
        return True

    @staticmethod
    def _redact(record: logging.LogRecord) -> None:
        if isinstance(record.msg, str):
            record.msg = redact_query(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_redact_arg(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: _redact_arg(v) for k, v in record.args.items()}
        if record.exc_info and not record.exc_text:
            # Formatter.format() uses exc_text when it is set; so does
            # JsonFormatter below.
            try:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            except Exception:  # noqa: BLE001
                record.exc_text = None
        if isinstance(record.exc_text, str):
            record.exc_text = redact_query(record.exc_text)
        if isinstance(record.stack_info, str):
            record.stack_info = redact_query(record.stack_info)
        for key, value in list(vars(record).items()):
            if key not in _RESERVED and not key.startswith("_") and isinstance(value, str):
                setattr(record, key, redact_query(value))


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
        if record.exc_text:
            payload["exc"] = record.exc_text
        elif record.exc_info:
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
        "filters": {"request_id": {"()": RequestIdFilter},
                    "redact_secrets": {"()": RedactSecretsFilter}},
        "formatters": {
            "text": {"format": TEXT_FORMAT},
            "json": {"()": JsonFormatter},
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
                "formatter": fmt,
                "filters": ["redact_secrets", "request_id"],
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
