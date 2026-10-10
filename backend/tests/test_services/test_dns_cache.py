"""Tests for utils/dns_cache.py — process-wide getaddrinfo TTL cache."""
import asyncio
import socket

import pytest

from utils import dns_cache

_RESULT = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))]


@pytest.fixture
def fake_resolver(monkeypatch):
    """Count calls to the real resolver and serve a fixed answer."""
    calls = []

    def fake(host, port, family=0, type=0, proto=0, flags=0):
        calls.append(host)
        if host == "missing.example.com":
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return list(_RESULT)

    # Registered first so it is undone last: socket.getaddrinfo ends up as it was.
    monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)
    dns_cache.uninstall()
    monkeypatch.setattr(dns_cache, "_original_getaddrinfo", fake)
    yield calls
    dns_cache.uninstall()


def test_repeated_lookups_hit_the_cache(fake_resolver):
    assert dns_cache.install(ttl=60, negative_ttl=10)
    for _ in range(5):
        assert socket.getaddrinfo("host.example.com", 443) == _RESULT
    assert fake_resolver == ["host.example.com"]
    assert dns_cache.stats()["hits"] == 4


def test_entries_expire_after_ttl(fake_resolver, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(dns_cache.time, "monotonic", lambda: now[0])
    dns_cache.install(ttl=60, negative_ttl=10)
    socket.getaddrinfo("host.example.com", 443)
    now[0] += 59
    socket.getaddrinfo("host.example.com", 443)
    now[0] += 2
    socket.getaddrinfo("host.example.com", 443)
    assert fake_resolver == ["host.example.com", "host.example.com"]


def test_failures_are_cached_briefly(fake_resolver, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(dns_cache.time, "monotonic", lambda: now[0])
    dns_cache.install(ttl=60, negative_ttl=10)
    for _ in range(3):
        with pytest.raises(socket.gaierror):
            socket.getaddrinfo("missing.example.com", 80)
    assert fake_resolver == ["missing.example.com"]
    now[0] += 11
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("missing.example.com", 80)
    assert len(fake_resolver) == 2


def test_ip_literals_bypass_the_cache(fake_resolver):
    dns_cache.install(ttl=60, negative_ttl=10)
    socket.getaddrinfo("10.0.0.5", 443)
    socket.getaddrinfo("10.0.0.5", 443)
    assert fake_resolver == ["10.0.0.5", "10.0.0.5"]
    assert dns_cache.stats()["entries"] == 0


def test_different_ports_are_separate_entries(fake_resolver):
    dns_cache.install(ttl=60, negative_ttl=10)
    socket.getaddrinfo("host.example.com", 443)
    socket.getaddrinfo("host.example.com", 8006)
    assert len(fake_resolver) == 2


def test_ttl_zero_disables(fake_resolver):
    before = socket.getaddrinfo
    assert dns_cache.install(ttl=0) is False
    assert socket.getaddrinfo is before


@pytest.mark.asyncio
async def test_asyncio_loop_resolution_uses_the_cache(fake_resolver):
    """loop.getaddrinfo (used by httpx/anyio on the asyncio loop) goes through the cache."""
    dns_cache.install(ttl=60, negative_ttl=10)
    loop = asyncio.get_running_loop()
    for _ in range(3):
        await loop.getaddrinfo("host.example.com", 443)
    assert fake_resolver == ["host.example.com"]
