"""Unit tests for the shared_state sliding-window counter (in-memory backend).

These tests never touch a live Redis. A monkeypatchable clock is injected so
window expiry/pruning is verified deterministically without sleeping.
"""
import pytest

import services.shared_state as ss


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset the in-memory store and clock before and after each test."""
    ss.reset()
    yield
    ss.reset()


class FakeClock:
    """Monotonic clock stand-in with manual time control."""

    def __init__(self, start: float = 1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float):
        self.t += seconds


async def test_incr_window_counts_up():
    clock = FakeClock()
    ss.set_clock(clock)
    assert await ss.incr_window("k", 60) == 1
    assert await ss.incr_window("k", 60) == 2
    assert await ss.incr_window("k", 60) == 3


async def test_window_count_is_readonly():
    clock = FakeClock()
    ss.set_clock(clock)
    await ss.incr_window("k", 60)
    await ss.incr_window("k", 60)
    # read-only does not increment
    assert await ss.window_count("k", 60) == 2
    assert await ss.window_count("k", 60) == 2


async def test_separate_keys_independent():
    clock = FakeClock()
    ss.set_clock(clock)
    await ss.incr_window("a", 60)
    await ss.incr_window("a", 60)
    await ss.incr_window("b", 60)
    assert await ss.window_count("a", 60) == 2
    assert await ss.window_count("b", 60) == 1


async def test_entries_prune_after_window():
    clock = FakeClock()
    ss.set_clock(clock)
    await ss.incr_window("k", 60)  # at t=1000
    clock.advance(30)
    await ss.incr_window("k", 60)  # at t=1030 -> count 2
    assert await ss.window_count("k", 60) == 2
    clock.advance(31)  # now t=1061, first entry (1000) is older than 60s
    # only the t=1030 entry remains in window (1001..1061)
    assert await ss.window_count("k", 60) == 1
    clock.advance(60)  # t=1121, all pruned
    assert await ss.window_count("k", 60) == 0


async def test_fail_open_on_redis_error(monkeypatch, caplog):
    """When a Redis client is configured but raises, we fall open to memory."""
    clock = FakeClock()
    ss.set_clock(clock)

    class BoomClient:
        def pipeline(self, *a, **k):
            raise RuntimeError("redis down")

        async def incr(self, *a, **k):
            raise RuntimeError("redis down")

    # Force the redis path and inject a failing client.
    monkeypatch.setattr(ss, "_redis_url", lambda: "redis://localhost:6379/0")
    monkeypatch.setattr(ss, "_get_redis", lambda: BoomClient())

    # Should not raise; falls back to the in-memory counter.
    assert await ss.incr_window("k", 60) == 1
    assert await ss.incr_window("k", 60) == 2


async def test_reset_clears_state():
    clock = FakeClock()
    ss.set_clock(clock)
    await ss.incr_window("k", 60)
    await ss.incr_window("k", 60)
    assert await ss.window_count("k", 60) == 2
    ss.reset()
    ss.set_clock(clock)
    assert await ss.window_count("k", 60) == 0
