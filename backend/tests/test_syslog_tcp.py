"""Syslog TCP listener: a persistent sender that goes quiet keeps its connection.

rsyslog's omfwd holds one TCP connection open. When the server closed it after
a quiet minute, the next send failed (omfwd error 2027) and that message was
lost. These tests drive SyslogTCPHandler over a real socket.
"""
from __future__ import annotations

import asyncio
import socket
from unittest.mock import AsyncMock, patch

import pytest

from services import syslog

LINE = b"<14>Nov  1 08:00:00 paperless app: hello after a pause\n"


async def _serve():
    async def connected(reader, writer):
        await syslog.SyslogTCPHandler(reader, writer).handle()
    server = await asyncio.start_server(connected, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


@pytest.fixture
def listener():
    enqueue = AsyncMock()
    with patch.object(syslog, "_enqueue", enqueue), \
         patch.object(syslog, "_listener_accepts", return_value=True), \
         patch.object(syslog, "_resolve_host_id", return_value=None):
        yield enqueue


async def _wait_for(predicate, timeout=2.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            return False
        await asyncio.sleep(0.02)
    return True


async def test_quiet_connection_stays_open_by_default(listener):
    assert syslog._TCP_IDLE_TIMEOUT == 0
    server, port = await _serve()
    try:
        _, writer = await asyncio.open_connection("127.0.0.1", port)
        # Quiet longer than the configured-timeout test below needs to close.
        await asyncio.sleep(0.3)
        writer.write(LINE)
        await writer.drain()
        assert await _wait_for(lambda: listener.await_count == 1)
        assert listener.await_args.args[0]["message"].endswith("hello after a pause")
        writer.close()
    finally:
        server.close()
        await server.wait_closed()


async def test_idle_timeout_closes_when_configured(listener):
    with patch.object(syslog, "_TCP_IDLE_TIMEOUT", 0.1):
        server, port = await _serve()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            # The server closes the quiet connection: the client reads EOF.
            assert await asyncio.wait_for(reader.read(), timeout=2) == b""
            writer.close()
        finally:
            server.close()
            await server.wait_closed()
    listener.assert_not_awaited()


async def test_keepalive_is_enabled(listener):
    seen = {}
    real = syslog._enable_keepalive

    def spy(writer):
        real(writer)
        sock = writer.get_extra_info("socket")
        seen["keepalive"] = sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE)

    with patch.object(syslog, "_enable_keepalive", spy):
        server, port = await _serve()
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            assert await _wait_for(lambda: "keepalive" in seen)
            writer.close()
        finally:
            server.close()
            await server.wait_closed()
    assert seen["keepalive"] != 0
