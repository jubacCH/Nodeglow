"""Tests for utils/ping.py — check_host with multi-port support."""
from unittest.mock import AsyncMock, patch

import pytest



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
async def test_check_host_dns_token():
    """'dns:NAME' asks the host itself to resolve NAME; a failure is a port_error."""
    from utils.ping import check_host

    host = FakeHost(hostname="10.0.0.2", check_type="icmp,dns:internal.example.com")
    with (
        patch("utils.ping.ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
        patch("utils.ping.check_dns", new_callable=AsyncMock, return_value=(False, None)) as dns,
    ):
        online, port_error, latency, detail = await check_host(host)
    dns.assert_awaited_once_with("10.0.0.2", "internal.example.com")
    assert online is True
    assert port_error is True
    assert detail["dns:internal.example.com"] is False


class _FakeDnsServer:
    """Answers every query on a local UDP port with the given rcode / answer count."""
    def __init__(self, rcode=0, ancount=1, reply=True):
        self.rcode, self.ancount, self.reply = rcode, ancount, reply

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        import struct
        if not self.reply:
            return
        qid = struct.unpack("!H", data[:2])[0]
        flags = 0x8180 | self.rcode  # QR, RD, RA
        header = struct.pack("!HHHHHH", qid, flags, 1, self.ancount, 0, 0)
        self.transport.sendto(header + data[12:], addr)

    def error_received(self, exc):
        pass

    def connection_lost(self, exc):
        pass


async def _serve(proto):
    import asyncio
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(lambda: proto, local_addr=("127.0.0.1", 0))
    return transport, transport.get_extra_info("sockname")[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("rcode,ancount,expected", [
    (0, 1, True),    # NOERROR with an answer
    (0, 0, False),   # NOERROR but empty (NODATA)
    (3, 0, False),   # NXDOMAIN
    (2, 0, False),   # SERVFAIL
])
async def test_check_dns_against_local_server(rcode, ancount, expected):
    from utils.ping import check_dns

    transport, port = await _serve(_FakeDnsServer(rcode=rcode, ancount=ancount))
    try:
        ok, latency = await check_dns("127.0.0.1", "host.example.com", port=port, timeout=2)
    finally:
        transport.close()
    assert ok is expected
    assert (latency is not None) is expected


@pytest.mark.asyncio
async def test_check_dns_timeout():
    from utils.ping import check_dns

    transport, port = await _serve(_FakeDnsServer(reply=False))
    try:
        ok, latency = await check_dns("127.0.0.1", "host.example.com", port=port, timeout=0.3)
    finally:
        transport.close()
    assert (ok, latency) == (False, None)


def test_build_dns_query_wire_format():
    from utils.ping import build_dns_query

    q = build_dns_query("a.example.com.", 0x1234)
    assert q[:12] == bytes.fromhex("123401000001000000000000")
    assert q[12:] == b"\x01a\x07example\x03com\x00" + bytes.fromhex("00010001")


class _FakeProc:
    returncode = 0

    async def communicate(self):
        return b"64 bytes from 10.0.0.7: icmp_seq=1 ttl=64 time=0.42 ms\n", b""


@pytest.mark.asyncio
async def test_ping_host_pings_the_resolved_ip():
    """The name is resolved in-process (cacheable); ping only ever sees the IP."""
    import socket
    from utils import ping as ping_mod

    infos = [(socket.AF_INET, socket.SOCK_RAW, 0, "", ("10.0.0.7", 0))]
    with (
        patch("asyncio.base_events.BaseEventLoop.getaddrinfo", new_callable=AsyncMock, return_value=infos),
        patch("utils.ping.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=_FakeProc()) as spawn,
    ):
        ok, latency = await ping_mod.ping_host("host.example.com")
    assert (ok, latency) == (True, 0.42)
    assert spawn.call_args.args[-1] == "10.0.0.7"


@pytest.mark.asyncio
async def test_ping_host_unresolvable_name_is_down_without_spawning():
    import socket
    from utils import ping as ping_mod

    with (
        patch("asyncio.base_events.BaseEventLoop.getaddrinfo", new_callable=AsyncMock,
              side_effect=socket.gaierror(socket.EAI_NONAME, "unknown")),
        patch("utils.ping.asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn,
    ):
        assert await ping_mod.ping_host("missing.example.com") == (False, None)
    spawn.assert_not_called()


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
