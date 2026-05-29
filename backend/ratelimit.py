"""Rate limiter for FastAPI, backed by shared_state.

Counters live in :mod:`services.shared_state`, which transparently uses Redis
when ``REDIS_URL`` is configured (shared across workers/nodes, survives the
window across restarts) and an identical in-memory sliding window otherwise.
With no ``REDIS_URL`` the behaviour is exactly the same as the original
in-process limiter.

All public names and signatures are unchanged so existing decorated routes and
``auth.py`` keep working without modification.
"""
from functools import wraps

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

from services import shared_state


class RateLimiter:
    """Sliding-window rate limiter keyed by client IP.

    Thin wrapper over :mod:`services.shared_state`. Retained for backward
    compatibility: the check-then-record semantics are identical to before — a
    key at or above ``max_requests`` is limited and the over-limit attempt is
    NOT recorded.
    """

    def is_limited(self, key: str, max_requests: int, window_seconds: int) -> bool:
        """Check if key has exceeded max_requests in the last window_seconds."""
        if shared_state.window_count_sync(key, window_seconds) >= max_requests:
            return True
        shared_state.incr_window_sync(key, window_seconds)
        return False


# Global instance (kept for any external importers).
_limiter = RateLimiter()


def _get_client_ip(request: Request) -> str:
    """Extract client IP from the direct connection (ignore X-Forwarded-For to prevent spoofing)."""
    return request.client.host if request.client else "unknown"


# ── Failed-auth throttle (IP-scoped, username-independent) ────────────────────
#
# The per-username lockout in auth.py only bounds attempts against a *single*
# account. An attacker can spray many usernames from one IP and stay under that
# limit. This throttle caps total failed-auth attempts per source IP regardless
# of the username, so credential-spraying from one host is bounded.
#
# Counters are stored via shared_state: with REDIS_URL set the limit is shared
# across workers/nodes and survives restarts within the window; otherwise it is
# in-memory and per-process (fine for single-worker homelab deployments).
_failed_auth_limiter = RateLimiter()

# Max failed auth attempts per IP within the window before throttling.
FAILED_AUTH_MAX_ATTEMPTS = 20
FAILED_AUTH_WINDOW_SECONDS = 300  # 5 minutes


def failed_auth_throttled(request: Request) -> bool:
    """Return True if this source IP has too many recent failed-auth attempts.

    Call this BEFORE attempting authentication. It both checks and records the
    attempt against the IP, so only call it on the auth path (one count per
    login request). Returns True once the IP is over the limit.

    Synchronous by design — call sites invoke it without ``await``.
    """
    ip = _get_client_ip(request)
    return _failed_auth_limiter.is_limited(
        f"failed_auth:{ip}", FAILED_AUTH_MAX_ATTEMPTS, FAILED_AUTH_WINDOW_SECONDS
    )


_429_HTML = (
    '<html><body style="background:#0b0d14;color:#e2e8f0;font-family:sans-serif;'
    'display:flex;align-items:center;justify-content:center;height:100vh;">'
    '<div style="text-align:center"><p style="font-size:3rem;margin:0">429</p>'
    '<p style="color:#94a3b8">Too many requests. Please try again later.</p>'
    '<a href="/" style="color:#3b82f6;font-size:.875rem">&larr; Back</a></div></body></html>'
)


def rate_limit(max_requests: int = 5, window_seconds: int = 60, html: bool = False):
    """Decorator for FastAPI route handlers. Returns 429 when limit exceeded."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            request = kwargs.get("request")
            if not request:
                for arg in args:
                    if isinstance(arg, Request):
                        request = arg
                        break
            if request:
                ip = _get_client_ip(request)
                key = f"{func.__module__}.{func.__name__}:{ip}"
                # Check-then-record, matching the original semantics: an
                # over-limit attempt is rejected and NOT counted.
                if await shared_state.window_count(key, window_seconds) >= max_requests:
                    if html:
                        return HTMLResponse(_429_HTML, status_code=429)
                    return JSONResponse(
                        {"error": "Too many requests. Please try again later."},
                        status_code=429,
                    )
                await shared_state.incr_window(key, window_seconds)
            return await func(*args, **kwargs)
        return wrapper
    return decorator
