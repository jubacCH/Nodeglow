"""The heartbeat to an external monitor, and what is known about it.

Nodeglow reports its own problems through itself. A stopped scheduler, a
database that is gone or a host that is off silences those reports too, so
somebody outside has to notice the silence: a dead-man service that is
requested once a minute.

Where it is configured: under Settings, System (the setting ``heartbeat_url``,
stored encrypted), or with ``NODEGLOW_HEARTBEAT_URL`` in the environment. The
setting wins. Either holds the check URL, or ``off`` to run without a monitor
on purpose.

This module also keeps the outcome of the requests, so that an installation
without such a monitor, or with one that rejects the requests, says so
instead of looking healthy.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from urllib.parse import urlparse

log = logging.getLogger("nodeglow.heartbeat")

ENV = "NODEGLOW_HEARTBEAT_URL"
SETTING = "heartbeat_url"

# Values that mean "no external monitor, and that is meant".
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


def _mode_of(raw: str) -> tuple[str, str]:
    raw = (raw or "").strip()
    if not raw:
        return "unset", ""
    if raw.lower() in OFF_VALUES:
        return "off", ""
    return "set", raw


def setting() -> tuple[str, str]:
    """(mode, url) from the environment alone: "unset", "off" or "set"."""
    return _mode_of(os.getenv(ENV, ""))


async def resolve(db) -> tuple[str, str, str]:
    """(mode, url, source): the setting saved in the UI, else the environment.

    ``source`` is "setting", "environment" or "none".
    """
    stored = ""
    try:
        from models.settings import get_setting

        stored = (await get_setting(db, SETTING, "") or "").strip()
    except Exception as exc:  # noqa: BLE001
        # The environment still applies when the setting cannot be read.
        log.debug("Heartbeat setting could not be read: %s", type(exc).__name__)
    if stored:
        return (*_mode_of(stored), "setting")
    mode, url = setting()
    return mode, url, ("environment" if mode != "unset" else "none")


def url_problem(url: str) -> str | None:
    """Why ``url`` cannot be a check URL, or None.

    A monitor inside the own network (an Uptime Kuma next door) is a normal
    case, so private addresses are allowed. Refused are targets that are
    never a monitor: cloud metadata services, link-local and unspecified
    addresses, and Nodeglow's own services.
    """
    import ipaddress

    from utils.net_safety import INTERNAL_HOSTS, METADATA_HOSTS, METADATA_IPS, resolve_all

    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return "This is not a valid URL"
    if parsed.scheme not in ("http", "https"):
        return "The URL must start with https:// or http://"
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        return "The URL has no host"
    if host in INTERNAL_HOSTS:
        return "Nodeglow's own services cannot watch Nodeglow"
    if host in METADATA_HOSTS:
        return "Cloud metadata endpoints are not allowed"

    def reason(addr) -> str | None:
        if getattr(addr, "ipv4_mapped", None) is not None:
            addr = addr.ipv4_mapped
        if addr in METADATA_IPS:
            return "Cloud metadata endpoints are not allowed"
        if addr.is_unspecified or addr.is_link_local or addr.is_multicast:
            return "This address cannot be a monitor"
        if addr.is_loopback:
            return "A monitor on this host stops together with Nodeglow"
        return None

    try:
        return reason(ipaddress.ip_address(host))
    except ValueError:
        pass
    for addr in resolve_all(host):
        if (found := reason(addr)):
            return found
    return None


def describe_error(exc: Exception) -> str:
    """An error text without the URL: the check URL is the credential."""
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if code is not None:
        return f"the monitor answered HTTP {code}"
    return type(exc).__name__


async def request(url: str) -> str | None:
    """Request the check URL once. None when it answered, else the error."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            (await client.get(url)).raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return describe_error(exc)
    return None


def record_ok(now: float) -> None:
    _state.last_ok = now
    _state.last_error = None
    _state.failures = 0


def record_failure(error: str) -> None:
    _state.last_error = error[:200]
    _state.failures += 1


def reset() -> None:
    """Forget what was recorded (a changed setting, tests)."""
    global _state
    _state = _State()


def status(now: float, mode: str | None = None, source: str | None = None) -> dict:
    """What the System status page and the settings show. Never the URL."""
    if mode is None:
        mode, _ = setting()
        source = "environment" if mode != "unset" else "none"
    return {
        "mode": mode,
        "source": source,
        "last_ok_seconds_ago": None if _state.last_ok is None else max(0, round(now - _state.last_ok)),
        "failures_in_a_row": _state.failures,
        "last_error": _state.last_error,
    }


def problems(mode: str | None = None) -> list[dict]:
    """What the self-check reports: key, title, severity, summary."""
    if mode is None:
        mode, _ = setting()
    if mode == "unset":
        return [{
            "key": "heartbeat:unset",
            "title": "Nobody is told when Nodeglow itself stops",
            "severity": "warning",
            "summary": (
                "No external monitor is set up. When the scheduler stops, the database is gone "
                "or the host is off, every alert stops with it, this one included. Under "
                "Settings, System, External monitor: enter the check URL of a dead-man service, "
                "or choose to run without one to close this."
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
                "although it runs, or it no longer watches at all. Check the URL under "
                "Settings, System, External monitor."
            ),
        }]
    return []
