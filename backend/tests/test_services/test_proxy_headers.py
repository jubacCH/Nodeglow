"""The backend sees real client IPs behind the frontend proxy.

Without --proxy-headers every request came from the frontend container, so
the per-IP login throttle was one global counter and the audit log had a
single address for everyone. These tests pin the Dockerfile configuration and
check what uvicorn makes of it.
"""
import re
from pathlib import Path

import pytest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"


def _dockerfile():
    return DOCKERFILE.read_text(encoding="utf-8")


def _default_allow_ips() -> str:
    m = re.search(r'^ENV FORWARDED_ALLOW_IPS="([^"]+)"', _dockerfile(), re.M)
    assert m, "Dockerfile must set a FORWARDED_ALLOW_IPS default"
    return m.group(1)


def test_uvicorn_runs_with_proxy_headers():
    cmd = next(line for line in _dockerfile().splitlines() if line.startswith("CMD "))
    assert '"--proxy-headers"' in cmd


async def _client_seen(peer: str, xff: str | None) -> str:
    seen = {}

    async def app(scope, receive, send):
        seen["client"] = scope["client"][0]

    mw = ProxyHeadersMiddleware(app, trusted_hosts=_default_allow_ips())
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    await mw({"type": "http", "client": (peer, 1234), "headers": headers, "scheme": "http"},
             None, None)
    return seen["client"]


@pytest.mark.parametrize("peer,xff,expected", [
    # The frontend container (compose network) forwards the real client.
    ("172.18.0.5", "10.10.20.33", "10.10.20.33"),
    # A reverse proxy in front appended the real client after a spoofed value.
    ("172.18.0.5", "6.6.6.6, 10.10.20.33", "10.10.20.33"),
    # No header: the peer itself.
    ("172.18.0.5", None, "172.18.0.5"),
    # A LAN host talking to the backend directly cannot choose its address.
    ("10.10.20.40", "1.2.3.4", "10.10.20.40"),
    ("192.168.1.10", "1.2.3.4", "192.168.1.10"),
])
async def test_trusted_peers(peer, xff, expected):
    assert await _client_seen(peer, xff) == expected
