"""The syslog write path: batching, lock discipline, back-pressure."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from services import syslog


@pytest.fixture(autouse=True)
def _clean_state():
    syslog._buffer = []
    syslog._flush_event = None
    syslog._udp_queue = None
    yield
    syslog._buffer = []
    syslog._flush_event = None
    syslog._udp_queue = None


def _msg(i: int = 0) -> dict:
    return {"message": f"hello {i}", "severity": 6, "source_ip": "10.0.0.1", "host_id": None}


async def test_insert_runs_outside_the_buffer_lock():
    """Receivers must keep appending while ClickHouse is slow."""
    release = asyncio.Event()
    lock_held_during_insert = []

    async def slow_insert(rows):
        lock_held_during_insert.append(syslog._buffer_lock.locked())
        await release.wait()

    with patch("services.clickhouse_client.insert_batch", new=slow_insert):
        syslog._buffer = [_msg(1), _msg(2)]
        flush = asyncio.create_task(syslog._flush_buffer())
        await asyncio.sleep(0)  # let the flush reach the insert
        await asyncio.sleep(0)

        t0 = time.monotonic()
        await asyncio.wait_for(syslog._enqueue(_msg(3)), timeout=1)
        assert time.monotonic() - t0 < 0.5
        assert len(syslog._buffer) == 1  # appended while the insert was pending

        release.set()
        await flush

    assert lock_held_during_insert == [False]


async def test_full_batch_wakes_the_flusher_instead_of_inserting_inline():
    syslog._flush_event = asyncio.Event()
    insert = AsyncMock()
    with patch("services.clickhouse_client.insert_batch", new=insert), \
         patch.object(syslog, "_BUFFER_SIZE", 3):
        for i in range(3):
            await syslog._enqueue(_msg(i))

    assert syslog._flush_event.is_set()
    insert.assert_not_awaited()
    assert len(syslog._buffer) == 3


async def test_without_a_flusher_a_full_batch_is_written_inline():
    insert = AsyncMock()
    with patch("services.clickhouse_client.insert_batch", new=insert), \
         patch.object(syslog, "_BUFFER_SIZE", 2):
        await syslog._enqueue(_msg(1))
        await syslog._enqueue(_msg(2))

    insert.assert_awaited_once()
    assert len(insert.await_args.args[0]) == 2
    assert syslog._buffer == []


async def test_failed_insert_puts_the_batch_back_in_front():
    with patch("services.clickhouse_client.insert_batch",
               new=AsyncMock(side_effect=RuntimeError("ch down"))):
        syslog._buffer = [_msg(1), _msg(2)]
        await syslog._flush_buffer()

    assert [m["message"] for m in syslog._buffer] == ["hello 1", "hello 2"]


async def test_received_at_is_stamped_at_enqueue_time():
    syslog._flush_event = asyncio.Event()
    await syslog._enqueue(_msg(1))
    assert syslog._buffer[0]["received_at"] is not None


async def test_udp_overflow_is_dropped_and_counted_without_spawning_tasks():
    from services.metrics import SYSLOG_MESSAGES_DROPPED

    syslog._udp_queue = asyncio.Queue(maxsize=1)
    proto = syslog.SyslogUDPProtocol()
    before = SYSLOG_MESSAGES_DROPPED.labels(reason="queue_full")._value.get()
    tasks_before = len(asyncio.all_tasks())

    with patch.object(syslog, "_syslog_rate_ok", return_value=True), \
         patch.object(syslog, "_global_rate_ok", return_value=True):
        proto.datagram_received(b"<14>hello one", ("10.0.0.9", 514))
        proto.datagram_received(b"<14>hello two", ("10.0.0.9", 514))

    assert syslog._udp_queue.qsize() == 1
    assert SYSLOG_MESSAGES_DROPPED.labels(reason="queue_full")._value.get() == before + 1
    assert len(asyncio.all_tasks()) == tasks_before


async def test_resolve_many_runs_concurrently_and_caps_each_lookup():
    def slow_resolver(name):
        time.sleep(0.3)
        return f"ip-of-{name}"

    with patch.object(syslog, "_DNS_TIMEOUT", 5.0):
        t0 = time.monotonic()
        out = await syslog._resolve_many([f"h{i}" for i in range(6)], slow_resolver)
        elapsed = time.monotonic() - t0
    assert out == {f"h{i}": f"ip-of-h{i}" for i in range(6)}
    assert elapsed < 1.2  # serially this would be 1.8 s

    def hangs(name):
        time.sleep(1.0)
        return "late"

    with patch.object(syslog, "_DNS_TIMEOUT", 0.1):
        out = await syslog._resolve_many(["slow"], hangs)
    assert out == {"slow": None}


async def test_start_and_stop_manage_every_background_task():
    with patch.object(syslog, "_refresh_host_cache", new=AsyncMock()), \
         patch("services.log_intelligence.load_template_cache", new=AsyncMock()), \
         patch("services.clickhouse_client.insert_batch", new=AsyncMock()):
        await syslog.start_syslog_server(udp_port=0, tcp_port=0)
        tasks = [syslog._flush_task, syslog._cache_task, syslog._rdns_task, syslog._udp_task]
        assert all(t is not None for t in tasks)
        await syslog.stop_syslog_server()
        await asyncio.sleep(0)

    assert all(t.cancelled() or t.done() for t in tasks)
    assert syslog._udp_queue is None
