"""The heartbeat's metric row: flat keys (Rust agent) and nested (legacy Python agent)."""
from unittest.mock import patch

import pytest

import services.shared_state as ss
from tests.test_routers.test_probe_commands import _probe


@pytest.fixture(autouse=True)
def _reset_limits():
    ss.reset()
    yield
    ss.reset()


async def _stored_row(client, token, **body):
    stored = []

    async def capture(rows):
        stored.extend(rows)

    # A patch scoped to the call, restored before the client's own patches.
    with patch("services.clickhouse_client.insert_agent_metrics", capture):
        resp = await client.post("/api/agent/report", json={"hostname": "h1", **body},
                                 headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert len(stored) == 1
    return stored[0]


async def test_flat_rust_report_reaches_the_metric_row(auth_client):
    client, sf = auth_client
    _, token = await _probe(client, sf, is_probe=False)
    row = await _stored_row(
        client, token, cpu_pct=12.5, mem_pct=42.0, mem_used_mb=3400, mem_total_mb=8000,
        load_1=0.5, load_5=0.4, load_15=0.3, rx_bytes=1000, tx_bytes=2000,
        disks=[{"mount": "/", "pct": 60.0}], uptime_s=99)
    assert row["mem_pct"] == 42.0
    assert row["mem_used_mb"] == 3400 and row["mem_total_mb"] == 8000
    assert (row["load_1"], row["load_5"], row["load_15"]) == (0.5, 0.4, 0.3)
    assert row["rx_bytes"] == 1000 and row["tx_bytes"] == 2000
    assert row["cpu_pct"] == 12.5 and row["disk_pct"] == 60.0


async def test_nested_legacy_report_still_reaches_the_metric_row(auth_client):
    client, sf = auth_client
    _, token = await _probe(client, sf, is_probe=False)
    row = await _stored_row(
        client, token, cpu_pct=1.0,
        memory={"pct": 55.0, "used_mb": 100, "total_mb": 200},
        load={"load_1": 1.5, "load_5": 1.4, "load_15": 1.3},
        network={"rx_bytes": 7, "tx_bytes": 8})
    assert row["mem_pct"] == 55.0 and row["mem_used_mb"] == 100 and row["mem_total_mb"] == 200
    assert (row["load_1"], row["load_5"], row["load_15"]) == (1.5, 1.4, 1.3)
    assert row["rx_bytes"] == 7 and row["tx_bytes"] == 8


async def test_flat_keys_win_over_nested_and_zero_is_kept(auth_client):
    client, sf = auth_client
    _, token = await _probe(client, sf, is_probe=False)
    row = await _stored_row(client, token, mem_pct=0.0, memory={"pct": 55.0},
                            load_1=0.0, load={"load_1": 9.0})
    assert row["mem_pct"] == 0.0 and row["load_1"] == 0.0
