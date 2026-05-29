"""Shared, optionally-Redis-backed sliding-window counters.

This module provides the primitive that the rate limiter is built on. It has
two interchangeable backends:

* **Redis** — used when ``REDIS_URL`` is configured. A single client is created
  lazily on first use (never at import time, never at network-connect time at
  import). Counters are shared across processes/workers and survive restarts
  for the duration of the window, giving correct limits under multi-worker /
  HA deployments.
* **In-memory** — used when ``REDIS_URL`` is unset, OR as a *fail-open*
  fallback whenever a Redis operation errors. This path reproduces the exact
  sliding-window behaviour of the original in-process rate limiter, so
  single-node deployments behave identically to before.

The sliding window is approximated with fixed bucketing in Redis (INCR on a
window-keyed counter + EXPIRE), and with pruned timestamp lists in memory. Both
expose the same logical contract: ``incr_window`` records one hit and returns
the number of hits observed in the current window; ``window_count`` reads the
current count without recording.

Nothing here connects to anything at import time.
"""
import logging
import threading
import time

import config

log = logging.getLogger(__name__)

# Injectable clock — tests replace this with a deterministic fake. Defaults to a
# monotonic clock, matching the original limiter's use of time.monotonic().
_clock = time.monotonic

# In-memory store: {key: [timestamps]}. Guarded by a lock because it may be
# touched from worker threads (the sync Redis bridge) as well as the event loop.
_lock = threading.Lock()
_hits: dict[str, list[float]] = {}
_last_cleanup = 0.0

# Lazily-created singleton Redis client.
_redis_client = None
_redis_lock = threading.Lock()


def set_clock(fn):
    """Inject a custom monotonic-style clock (test helper)."""
    global _clock
    _clock = fn


def reset():
    """Clear all in-memory state and reset the clock (test helper)."""
    global _hits, _last_cleanup, _clock, _redis_client
    with _lock:
        _hits = {}
        _last_cleanup = 0.0
    _clock = time.monotonic
    _redis_client = None


def _redis_url() -> str:
    """Return the configured Redis URL, or empty string. Indirection for tests."""
    return getattr(config, "REDIS_URL", "") or ""


def _get_redis():
    """Lazily create and return the singleton redis.asyncio client.

    Importing redis here (not at module import) keeps the module import-safe
    even when redis-py is absent and ``REDIS_URL`` is unset.
    """
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    with _redis_lock:
        if _redis_client is None:
            import redis.asyncio as aioredis

            _redis_client = aioredis.from_url(
                _redis_url(), encoding="utf-8", decode_responses=True
            )
    return _redis_client


# ── In-memory primitives (the canonical, original behaviour) ──────────────────


def _mem_incr(key: str, window_seconds: int) -> int:
    """Record one hit for key and return the in-window count (incl. this hit)."""
    global _last_cleanup
    now = _clock()
    with _lock:
        # Periodic cleanup every 5 minutes, mirroring the original limiter.
        if now - _last_cleanup > 300:
            _cleanup_locked(now, window_seconds)
        cutoff = now - window_seconds
        hits = [t for t in _hits.get(key, []) if t > cutoff]
        hits.append(now)
        _hits[key] = hits
        return len(hits)


def _mem_count(key: str, window_seconds: int) -> int:
    """Return the in-window count for key without recording a hit."""
    now = _clock()
    cutoff = now - window_seconds
    with _lock:
        hits = [t for t in _hits.get(key, []) if t > cutoff]
        # Persist the pruned list so memory does not grow unbounded.
        if key in _hits:
            _hits[key] = hits
        return len(hits)


def _cleanup_locked(now: float, default_window: int):
    """Remove stale keys to prevent unbounded memory growth. Caller holds lock."""
    global _last_cleanup
    _last_cleanup = now
    cutoff = now - default_window * 2
    stale = [k for k, v in _hits.items() if not v or v[-1] < cutoff]
    for k in stale:
        del _hits[k]


# ── Redis primitives ──────────────────────────────────────────────────────────


async def _redis_incr(key: str, window_seconds: int) -> int:
    """INCR a window-bucketed counter and (re)set its TTL atomically."""
    client = _get_redis()
    bucket = int(_clock() // window_seconds)
    rkey = f"sw:{key}:{bucket}"
    pipe = client.pipeline()
    pipe.incr(rkey)
    pipe.expire(rkey, window_seconds)
    incr_result, _ = await pipe.execute()
    return int(incr_result)


async def _redis_count(key: str, window_seconds: int) -> int:
    """Read the current window bucket counter (0 if absent)."""
    client = _get_redis()
    bucket = int(_clock() // window_seconds)
    rkey = f"sw:{key}:{bucket}"
    val = await client.get(rkey)
    return int(val) if val is not None else 0


# ── Public async API ──────────────────────────────────────────────────────────


async def incr_window(key: str, window_seconds: int) -> int:
    """Record one hit for ``key`` and return the count in the current window.

    Uses Redis when configured; on any Redis error logs a warning and FAILS
    OPEN to the in-memory backend so requests are never broken.
    """
    if _redis_url():
        try:
            return await _redis_incr(key, window_seconds)
        except Exception as e:  # fail open
            log.warning("shared_state: Redis incr failed, falling back to memory: %s", e)
    return _mem_incr(key, window_seconds)


async def window_count(key: str, window_seconds: int) -> int:
    """Return the current in-window count for ``key`` without recording a hit."""
    if _redis_url():
        try:
            return await _redis_count(key, window_seconds)
        except Exception as e:  # fail open
            log.warning("shared_state: Redis count failed, falling back to memory: %s", e)
    return _mem_count(key, window_seconds)


# ── Synchronous bridge ────────────────────────────────────────────────────────
#
# A few call sites (the failed-auth throttle) are synchronous and already run
# inside the event loop, so they cannot ``await``. For the in-memory backend the
# operations are plain synchronous functions, so we call them directly — this is
# the common single-node case and is exactly what happened before. When Redis is
# configured we run the coroutine to completion on a private loop in a worker
# thread, which keeps the public signature synchronous without blocking on the
# already-running loop.


def _run_coro_blocking(coro):
    """Run an async coroutine to completion from a synchronous caller.

    Always executes on a dedicated event loop in a worker thread so it is safe
    even when called from within a running event loop.
    """
    import asyncio

    result: list = []
    error: list = []

    def _runner():
        loop = asyncio.new_event_loop()
        try:
            result.append(loop.run_until_complete(coro))
        except Exception as e:  # pragma: no cover - surfaced to caller
            error.append(e)
        finally:
            loop.close()

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join()
    if error:
        raise error[0]
    return result[0]


def incr_window_sync(key: str, window_seconds: int) -> int:
    """Synchronous variant of :func:`incr_window`.

    In-memory path runs inline. Redis path bridges to async with fail-open.
    """
    if _redis_url():
        try:
            return _run_coro_blocking(_redis_incr(key, window_seconds))
        except Exception as e:  # fail open
            log.warning("shared_state: Redis incr (sync) failed, falling back to memory: %s", e)
    return _mem_incr(key, window_seconds)


def window_count_sync(key: str, window_seconds: int) -> int:
    """Synchronous variant of :func:`window_count`."""
    if _redis_url():
        try:
            return _run_coro_blocking(_redis_count(key, window_seconds))
        except Exception as e:  # fail open
            log.warning("shared_state: Redis count (sync) failed, falling back to memory: %s", e)
    return _mem_count(key, window_seconds)
