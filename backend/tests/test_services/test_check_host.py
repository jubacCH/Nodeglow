"""Tests for utils/ping.py — check_host with multi-port support."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from models.ping import PingHost


class FakeHost:
    """Minimal PingHost-like object for testing."""
    def __init__(self, hostname="example.com", check_type="icmp", port=None):
        self.hostname = hostname
        self.check_type = check_type
        self.port = port


@pytest.mark.asyncio
async def test_check_host_icmp_only():
    """ICMP-only host returns online status from ping."""
    from utils.ping import check_host

    host = FakeHost(check_type="icmp")
    with patch("utils.ping.ping_host", new_callable=AsyncMock, return_value=(True, 1.5)):
        online, port_error, latency, detail = await check_host(host)
    assert online is True
    assert port_error is False
    assert latency == 1.5
    assert detail == {"icmp": True}


@pytest.mark.asyncio
async def test_check_host_icmp_and_http():
    """ICMP + HTTP: online from ICMP, port_error when HTTP fails."""
    from utils.ping import check_host

    host = FakeHost(check_type="icmp,http")
    with (
        patch("utils.ping.ping_host", new_callable=AsyncMock, return_value=(True, 2.0)),
        patch("utils.ping.check_http", new_callable=AsyncMock, return_value=(False, None)),
    ):
        online, port_error, latency, detail = await check_host(host)
    assert online is True
    assert port_error is True
    assert detail["icmp"] is True
    assert detail["http"] is False


@pytest.mark.asyncio
async def test_check_host_multi_tcp_ports():
    """Multiple TCP ports: tcp:80 and tcp:443 checked separately."""
    from utils.ping import check_host

    host = FakeHost(check_type="icmp,tcp:80,tcp:443")
    with (
        patch("utils.ping.ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
        patch("utils.ping.check_tcp", new_callable=AsyncMock, side_effect=[
            (True, 5.0),   # tcp:80 ok
            (False, None),  # tcp:443 failed
        ]),
    ):
        online, port_error, latency, detail = await check_host(host)
    assert online is True
    assert port_error is True  # tcp:443 failed
    assert detail["icmp"] is True
    assert detail["tcp:80"] is True
    assert detail["tcp:443"] is False


@pytest.mark.asyncio
async def test_check_host_legacy_tcp_format():
    """Legacy 'tcp' (without port suffix) uses host.port."""
    from utils.ping import check_host

    host = FakeHost(check_type="icmp,tcp", port=8080)
    with (
        patch("utils.ping.ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
        patch("utils.ping.check_tcp", new_callable=AsyncMock, return_value=(True, 3.0)),
    ):
        online, port_error, latency, detail = await check_host(host)
    assert online is True
    assert port_error is False
    assert detail["tcp:8080"] is True


@pytest.mark.asyncio
async def test_check_host_offline_no_port_error():
    """When ICMP fails, port_error should be False even if services fail too."""
    from utils.ping import check_host

    host = FakeHost(check_type="icmp,https")
    with (
        patch("utils.ping.ping_host", new_callable=AsyncMock, return_value=(False, None)),
        patch("utils.ping.check_http", new_callable=AsyncMock, return_value=(False, None)),
    ):
        online, port_error, latency, detail = await check_host(host)
    assert online is False
    assert port_error is False  # offline hosts don't get port_error
