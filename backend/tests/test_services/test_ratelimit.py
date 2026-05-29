"""Tests for the rate limiter on top of shared_state (in-memory backend).

Verifies the per-route decorator triggers at the threshold and resets after
the window, and that failed_auth_throttled bounds per-IP attempts. A
monkeypatchable clock is injected to control window expiry deterministically.
"""
import pytest
from fastapi import Request

import services.shared_state as ss
from ratelimit import (
    FAILED_AUTH_MAX_ATTEMPTS,
    FAILED_AUTH_WINDOW_SECONDS,
    failed_auth_throttled,
    rate_limit,
)


@pytest.fixture(autouse=True)
def _reset_state():
    ss.reset()
    yield
    ss.reset()


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float):
        self.t += seconds


def _make_request(ip: str = "1.2.3.4") -> Request:
    """Build a minimal ASGI Request with a client host."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "client": (ip, 12345),
        "query_string": b"",
    }
    return Request(scope)


async def test_rate_limit_triggers_at_threshold():
    clock = FakeClock()
    ss.set_clock(clock)

    @rate_limit(max_requests=3, window_seconds=60)
    async def handler(request: Request):
        return "ok"

    req = _make_request()
    assert await handler(request=req) == "ok"
    assert await handler(request=req) == "ok"
    assert await handler(request=req) == "ok"
    # 4th request within window is blocked
    resp = await handler(request=req)
    assert getattr(resp, "status_code", None) == 429


async def test_rate_limit_resets_after_window():
    clock = FakeClock()
    ss.set_clock(clock)

    @rate_limit(max_requests=2, window_seconds=60)
    async def handler(request: Request):
        return "ok"

    req = _make_request()
    assert await handler(request=req) == "ok"
    assert await handler(request=req) == "ok"
    assert getattr(await handler(request=req), "status_code", None) == 429

    clock.advance(61)
    # window has passed, allowed again
    assert await handler(request=req) == "ok"


async def test_rate_limit_is_per_ip():
    clock = FakeClock()
    ss.set_clock(clock)

    @rate_limit(max_requests=1, window_seconds=60)
    async def handler(request: Request):
        return "ok"

    assert await handler(request=_make_request("10.0.0.1")) == "ok"
    # different IP has its own bucket
    assert await handler(request=_make_request("10.0.0.2")) == "ok"
    # same IP again is blocked
    assert getattr(await handler(request=_make_request("10.0.0.1")), "status_code", None) == 429


async def test_failed_auth_throttle_bounds_per_ip():
    clock = FakeClock()
    ss.set_clock(clock)

    req = _make_request("9.9.9.9")
    # First MAX attempts are allowed (return False = not throttled)
    for _ in range(FAILED_AUTH_MAX_ATTEMPTS):
        assert failed_auth_throttled(req) is False
    # Next one crosses the limit
    assert failed_auth_throttled(req) is True


async def test_failed_auth_throttle_resets_after_window():
    clock = FakeClock()
    ss.set_clock(clock)

    req = _make_request("8.8.8.8")
    for _ in range(FAILED_AUTH_MAX_ATTEMPTS):
        assert failed_auth_throttled(req) is False
    assert failed_auth_throttled(req) is True

    clock.advance(FAILED_AUTH_WINDOW_SECONDS + 1)
    assert failed_auth_throttled(req) is False
