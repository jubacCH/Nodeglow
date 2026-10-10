"""Single-holder lease used to elect one process to run scheduled jobs (enterprise).

Redis-backed when ``REDIS_URL`` is configured (sharing the core's lazily
created client, ``services.shared_state.redis_client``); an in-memory backend
covers the single-process case and the tests.

Unlike the core rate limiter this MUST NOT fail open: silently granting
leadership to every process on a Redis error would cause split-brain
(duplicate scheduled jobs). On a Redis error :func:`try_acquire_leader`
raises and the caller keeps its current state.
"""
from __future__ import annotations

import logging
import threading
import time

from services import shared_state

log = logging.getLogger("nodeglow.ee.leader_lock")

# Atomic "acquire if free/expired, or renew if I already hold it" + "release if
# I hold it", as Lua so the check-and-set is a single round trip.
_ACQUIRE_LUA = (
    "local v = redis.call('GET', KEYS[1]) "
    "if v == false or v == ARGV[1] then "
    "  redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[2]) return 1 "
    "else return 0 end"
)
_RELEASE_LUA = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then "
    "  return redis.call('DEL', KEYS[1]) else return 0 end"
)

# Injectable monotonic clock (tests replace it).
_clock = time.monotonic
_lock = threading.Lock()
# In-memory leases: {lock_name: (holder_id, expiry_time)}.
_leaders: dict[str, tuple[str, float]] = {}


def set_clock(fn) -> None:
    """Inject a custom monotonic-style clock (test helper)."""
    global _clock
    _clock = fn


def reset() -> None:
    """Clear the in-memory leases and restore the real clock (test helper)."""
    global _clock, _leaders
    with _lock:
        _leaders = {}
    _clock = time.monotonic


def _mem_acquire_leader(lock_name: str, instance_id: str, ttl_seconds: int) -> bool:
    now = _clock()
    with _lock:
        cur = _leaders.get(lock_name)
        if cur is None or cur[1] <= now or cur[0] == instance_id:
            _leaders[lock_name] = (instance_id, now + ttl_seconds)
            return True
        return False


def _mem_release_leader(lock_name: str, instance_id: str) -> None:
    with _lock:
        cur = _leaders.get(lock_name)
        if cur is not None and cur[0] == instance_id:
            del _leaders[lock_name]


async def try_acquire_leader(lock_name: str, instance_id: str, ttl_seconds: int) -> bool:
    """Acquire or renew leadership of ``lock_name`` for ``instance_id``.

    Returns True if this instance holds the lease (just acquired or renewed),
    False if another live instance holds it. Raises on a Redis error so the
    caller can preserve its current leadership state (never fail open).
    """
    if shared_state.redis_url():
        client = shared_state.redis_client()
        res = await client.eval(_ACQUIRE_LUA, 1, lock_name, instance_id, int(ttl_seconds * 1000))
        return bool(int(res))
    return _mem_acquire_leader(lock_name, instance_id, ttl_seconds)


async def release_leader(lock_name: str, instance_id: str) -> None:
    """Release ``lock_name`` if held by ``instance_id`` (best effort)."""
    if shared_state.redis_url():
        try:
            client = shared_state.redis_client()
            await client.eval(_RELEASE_LUA, 1, lock_name, instance_id)
        except Exception as e:
            log.warning("leader release failed: %s", e)
        return
    _mem_release_leader(lock_name, instance_id)
