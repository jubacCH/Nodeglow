"""
Process-wide TTL cache for socket.getaddrinfo.

Every check cycle resolves the same handful of hostnames again: an integration
poll alone issues dozens of HTTP requests to one host, and nothing in the stack
caches lookups. That made Nodeglow the busiest DNS client in the network by far.

Wrapping socket.getaddrinfo catches sync code (requests, socket.create_connection)
and asyncio's loop.getaddrinfo, which calls socket.getaddrinfo in an executor.
uvloop resolves in C and bypasses it, so uvicorn must run with `--loop asyncio`.

TTL: env DNS_CACHE_TTL (seconds, default 60, 0 disables). Failed lookups are
cached for DNS_CACHE_NEGATIVE_TTL (default 10) so a dead name cannot cause a
query storm, but recovers quickly.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import socket
import threading
import time

log = logging.getLogger(__name__)

_MAX_ENTRIES = 2048

_original_getaddrinfo = socket.getaddrinfo
_cache: dict[tuple, tuple[float, object]] = {}
_lock = threading.Lock()
_installed = False
_stats = {"hits": 0, "misses": 0}


def _is_literal(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    try:
        ipaddress.ip_address(str(host).split("%", 1)[0])
        return True
    except ValueError:
        return False


def _make_cached(ttl: float, negative_ttl: float):
    def cached_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if _is_literal(host):
            return _original_getaddrinfo(host, port, family, type, proto, flags)
        key = (host, port, family, type, proto, flags)
        now = time.monotonic()
        with _lock:
            entry = _cache.get(key)
        if entry is not None and entry[0] > now:
            _stats["hits"] += 1
            value = entry[1]
            if isinstance(value, socket.gaierror):
                raise socket.gaierror(*value.args)
            return list(value)

        _stats["misses"] += 1
        try:
            result = _original_getaddrinfo(host, port, family, type, proto, flags)
        except socket.gaierror as exc:
            if negative_ttl > 0:
                _store(key, now + negative_ttl, exc)
            raise
        _store(key, now + ttl, tuple(result))
        return list(result)

    return cached_getaddrinfo


def _store(key: tuple, expires: float, value: object) -> None:
    with _lock:
        if len(_cache) >= _MAX_ENTRIES:
            now = time.monotonic()
            for k in [k for k, (exp, _) in _cache.items() if exp <= now]:
                del _cache[k]
            if len(_cache) >= _MAX_ENTRIES:
                _cache.clear()
        _cache[key] = (expires, value)


def install(ttl: float | None = None, negative_ttl: float | None = None) -> bool:
    """Replace socket.getaddrinfo with the cached version. Returns True if active."""
    global _installed
    if ttl is None:
        ttl = float(os.environ.get("DNS_CACHE_TTL", "60"))
    if negative_ttl is None:
        negative_ttl = float(os.environ.get("DNS_CACHE_NEGATIVE_TTL", "10"))
    if ttl <= 0:
        log.info("DNS cache disabled (DNS_CACHE_TTL=%s)", ttl)
        return False
    if _installed:
        return True
    socket.getaddrinfo = _make_cached(ttl, negative_ttl)
    _installed = True
    log.info("DNS cache active: ttl=%ss, negative_ttl=%ss", ttl, negative_ttl)
    return True


def uninstall() -> None:
    """Restore the original resolver and drop the cache (used by tests)."""
    global _installed
    socket.getaddrinfo = _original_getaddrinfo
    _installed = False
    clear()


def clear() -> None:
    with _lock:
        _cache.clear()
    _stats["hits"] = _stats["misses"] = 0


def stats() -> dict:
    with _lock:
        size = len(_cache)
    return {**_stats, "entries": size}
