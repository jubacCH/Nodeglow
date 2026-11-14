"""The heartbeat to an external monitor, and what is known about it.

Nodeglow reports its own problems through itself. A stopped scheduler, a
database that is gone or a host that is off silences those reports too, so
somebody outside has to notice the silence: a dead-man service that is
requested once a minute (``NODEGLOW_HEARTBEAT_URL``).

This module keeps the outcome of those requests, so that an installation
without such a monitor, or with one that rejects the requests, says so
instead of looking healthy.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

ENV = "NODEGLOW_HEARTBEAT_URL"

# Values of the setting that mean "no external monitor, and that is meant".
OFF_VALUES = frozenset({"off", "none", "disabled", "false", "0"})

# Requests that must fail in a row before it is a problem. One a minute: a
# restart of the monitor or a short network fault is not worth an incident.
FAILURES_IN_A_ROW = 5


@dataclass
class _State:
    last_ok: float | None = None
    last_error: str | None = None
    failures: int = 0


_state = _State()


def setting() -> tuple[str, str]:
    """(mode, url): mode is "unset", "off" or "set"."""
    raw = os.getenv(ENV, "").strip()
    if not raw:
        return "unset", ""
    if raw.lower() in OFF_VALUES:
        return "off", ""
    return "set", raw


def record_ok(now: float) -> None:
    _state.last_ok = now
    _state.last_error = None
    _state.failures = 0


def record_failure(error: str) -> None:
    _state.last_error = error[:200]
    _state.failures += 1


def describe_error(exc: Exception) -> str:
    """An error text without the URL: the check URL is the credential."""
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if code is not None:
        return f"the monitor answered HTTP {code}"
    return type(exc).__name__


def reset() -> None:
    """Forget what was recorded (tests)."""
    global _state
    _state = _State()


def status(now: float) -> dict:
    """What the System status page shows."""
    mode, _ = setting()
    return {
        "mode": mode,
        "last_ok_seconds_ago": None if _state.last_ok is None else max(0, round(now - _state.last_ok)),
        "failures_in_a_row": _state.failures,
        "last_error": _state.last_error,
    }


def problems() -> list[dict]:
    """What the self-check reports: key, title, severity, summary."""
    mode, _ = setting()
    if mode == "unset":
        return [{
            "key": "heartbeat:unset",
            "title": "Nobody is told when Nodeglow itself stops",
            "severity": "warning",
            "summary": (
                "No external monitor is set up. When the scheduler stops, the database is gone "
                "or the host is off, every alert stops with it, this one included. Set "
                f"{ENV} to the check URL of a dead-man service (docs/OPERATIONS.md, \"Who tells "
                "you when Nodeglow itself is down\"), or to \"off\" to accept that and close this."
            ),
        }]
    if mode == "set" and _state.failures >= FAILURES_IN_A_ROW:
        return [{
            "key": "heartbeat:failing",
            "title": "The heartbeat to the external monitor is failing",
            "severity": "critical",
            "summary": (
                f"The last {_state.failures} requests to the external monitor failed: "
                f"{_state.last_error or 'no error text'}. It will report Nodeglow as down "
                f"although it runs, or it no longer watches at all. Check {ENV}."
            ),
        }]
    return []
