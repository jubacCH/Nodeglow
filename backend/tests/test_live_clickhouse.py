"""Smoke tests against a real ClickHouse server.

The rest of the suite mocks ClickHouse, so a client library change that breaks
the actual connection (clickhouse-connect 1.x needing the aiohttp extra for its
async client, 2026-10-10) passed CI and failed in production. These tests run
only when NODEGLOW_TEST_CLICKHOUSE_URL points at a server — CI starts one in the
"ClickHouse (live)" job:

    NODEGLOW_TEST_CLICKHOUSE_URL=http://default:@localhost:8123/default pytest tests/test_live_clickhouse.py
"""
import importlib
import inspect
import os
import uuid

import pytest

LIVE_URL = os.environ.get("NODEGLOW_TEST_CLICKHOUSE_URL", "")

pytestmark = pytest.mark.skipif(not LIVE_URL, reason="NODEGLOW_TEST_CLICKHOUSE_URL not set")


@pytest.fixture
async def ch(monkeypatch):
    """The production client module, pointed at the live server."""
    import services.clickhouse_client as mod

    monkeypatch.setenv("CLICKHOUSE_URL", LIVE_URL)
    mod = importlib.reload(mod)
    yield mod
    if mod._client is not None:
        closed = mod._client.close()  # sync in 0.8, a coroutine in later versions
        if inspect.isawaitable(closed):
            await closed
    importlib.reload(mod)


async def test_connects_and_queries(ch):
    rows = await ch.query("SELECT 1 AS one, version() AS v")
    assert rows[0]["one"] == 1
    assert rows[0]["v"]


async def test_insert_and_read_back(ch):
    client = await ch.get_client()
    table = f"nodeglow_live_{uuid.uuid4().hex[:8]}"
    await client.command(f"CREATE TABLE {table} (host_id UInt32, latency Float64) ENGINE = Memory")
    try:
        await client.insert(table, [[1, 1.5], [2, 2.5]], column_names=["host_id", "latency"])
        rows = await ch.query(f"SELECT host_id, latency FROM {table} ORDER BY host_id")
        assert [(r["host_id"], r["latency"]) for r in rows] == [(1, 1.5), (2, 2.5)]
    finally:
        await client.command(f"DROP TABLE IF EXISTS {table}")
