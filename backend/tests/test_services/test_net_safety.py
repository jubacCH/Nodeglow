"""SSRF guards: URL validation with DNS resolution + connect-time checks."""
import socket
from unittest.mock import patch

import httpcore
import pytest

import notifications
from utils import net_safety
from utils.net_safety import (
    GuardedAsyncTransport,
    UnsafeTargetError,
    _GuardedBackend,
    is_safe_url,
    safe_async_client,
    validate_host,
)


def _fake_getaddrinfo(mapping):
    def fake(host, port, *a, **kw):
        answer = mapping.get(host)
        if callable(answer):
            answer = answer()
        if not answer:
            raise socket.gaierror("no such host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0)) for ip in answer]
    return fake


# ── Literal checks ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", [
    "169.254.169.254", "168.63.129.16", "[fd00:ec2::254]", "0.0.0.0", "127.0.0.1",
    "localhost", "db", "clickhouse", "updater", "nodeglow", "frontend",
    "metadata.google.internal", "[::1]", "224.0.0.1",
])
def test_validate_host_blocks_literals(value):
    assert validate_host(value, resolve=False) is not None


@pytest.mark.parametrize("url", [
    "https://10.0.0.5/hook", "http://192.168.1.10:8080/x", "http://172.16.0.1/",
    "https://hooks.example.com/abc",
])
def test_rfc1918_and_public_urls_stay_allowed(url):
    assert is_safe_url(url, resolve=False)


@pytest.mark.parametrize("url", [
    "ftp://example.com/", "file:///etc/passwd", "http:///nohost", "",
    "http://169.254.169.254/latest/meta-data/", "http://[fd00:ec2::254]/",
    "http://168.63.129.16/", "http://db:5432/", "http://localhost./",
])
def test_unsafe_urls_rejected(url):
    assert not is_safe_url(url, resolve=False)


# ── Webhook URL check resolves DNS ──────────────────────────────────────────


def test_notifications_is_safe_url_resolves_names():
    mapping = {"evil.example": ["127.0.0.1"], "meta.example": ["169.254.169.254"],
               "lan.example": ["192.168.1.20"]}
    with patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo(mapping)):
        assert not notifications._is_safe_url("https://evil.example/hook")
        assert not notifications._is_safe_url("http://meta.example/")
        assert notifications._is_safe_url("http://lan.example/hook")
        assert notifications._is_safe_url("https://offline.example/hook")


# ── Connect-time guard ──────────────────────────────────────────────────────


class _RecordingBackend(httpcore.AsyncNetworkBackend):
    def __init__(self):
        self.hosts = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.hosts.append(host)
        raise httpcore.ConnectError("recorded, not connecting")

    async def sleep(self, seconds):
        return None


async def test_backend_connects_to_the_checked_ip():
    inner = _RecordingBackend()
    backend = _GuardedBackend(inner)
    with patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo({"nas.lan": ["192.168.1.20"]})):
        with pytest.raises(httpcore.ConnectError, match="recorded"):
            await backend.connect_tcp("nas.lan", 443)
    assert inner.hosts == ["192.168.1.20"]


@pytest.mark.parametrize("host,answer", [
    ("rebind.example", ["127.0.0.1"]),
    ("mixed.example", ["10.0.0.1", "169.254.169.254"]),
    ("169.254.169.254", None),
    ("db", None),
])
async def test_backend_refuses_blocked_targets(host, answer):
    inner = _RecordingBackend()
    backend = _GuardedBackend(inner)
    mapping = {host: answer} if answer else {}
    with patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo(mapping)):
        with pytest.raises(httpcore.ConnectError, match="Blocked outbound connection"):
            await backend.connect_tcp(host, 80)
    assert inner.hosts == []


async def test_safe_client_blocks_rebound_name():
    with patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo({"rebind.example": ["127.0.0.1"]})):
        async with safe_async_client(timeout=2) as client:
            with pytest.raises(UnsafeTargetError):
                await client.get("http://rebind.example/")


def test_safe_client_passes_verify_to_transport_and_disables_redirects():
    client = safe_async_client(verify=False, timeout=2)
    assert isinstance(client._transport, GuardedAsyncTransport)
    assert client.follow_redirects is False
    assert isinstance(client._transport._pool._network_backend, _GuardedBackend)


async def test_webhook_send_revalidates_at_connect_time():
    """DNS rebinding: the pre-send check sees a public address, the connection
    would go to loopback — the connect-time guard must stop it."""
    calls = {"n": 0}

    def answer():
        calls["n"] += 1
        return ["93.184.216.34"] if calls["n"] == 1 else ["127.0.0.1"]

    with patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo({"hook.example": answer})):
        with pytest.raises(UnsafeTargetError):
            await notifications._send_webhook("http://hook.example/x", "", "t", "m")
    assert calls["n"] >= 2


async def test_webhook_send_blocks_unsafe_url_before_sending():
    with patch.object(net_safety, "safe_async_client") as client_factory:
        await notifications._send_webhook("http://169.254.169.254/", "", "t", "m")
        await notifications._send_discord("http://localhost/hook", "t", "m")
    client_factory.assert_not_called()


def test_webhook_client_does_not_follow_redirects():
    assert notifications._webhook_client().follow_redirects is False
