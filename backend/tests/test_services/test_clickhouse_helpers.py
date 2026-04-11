"""Unit tests for the ClickHouse read-helper API in services.clickhouse_client.

These tests do NOT spin up a real ClickHouse — they monkeypatch the bottom
two primitives (`query` and `query_scalar`) and assert that:

1. The helper functions issue exactly one query each (no N+1).
2. The SQL fragment they produce contains the right table and the right
   ClickHouse-specific functions (argMax, sumIf, groupArray, etc).
3. The parameters dict carries through correctly with the right types.
4. The dict-shape transformations (e.g. {host_id: row_dict}, uptime
   percentage calculation, offline detection) are correct.

This catches the most common refactoring bugs without needing a CH
instance. Real SQL correctness is left to integration tests or a
testcontainers run.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from services import clickhouse_client as ch


# ── Helpers ─────────────────────────────────────────────────────────────────


class _FakeQuery:
    """Records calls and returns programmed responses.

    Instantiate, push responses with .returns(...), then patch
    `services.clickhouse_client.query` to point at .__call__.
    """

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self._responses: list = []

    def returns(self, *responses):
        self._responses.extend(responses)
        return self

    async def __call__(self, sql, params=None):
        self.calls.append((sql, dict(params or {})))
        if self._responses:
            return self._responses.pop(0)
        return []

    @property
    def last_sql(self) -> str:
        return self.calls[-1][0] if self.calls else ""

    @property
    def last_params(self) -> dict:
        return self.calls[-1][1] if self.calls else {}


@pytest.fixture
def fake_query(monkeypatch):
    q = _FakeQuery()
    monkeypatch.setattr(ch, "query", q)
    return q


# ── ping_checks helpers ─────────────────────────────────────────────────────


async def test_get_latest_ping_per_host_argmax_and_keys(fake_query):
    fake_query.returns([
        {
            "host_id": 1,
            "_ts": datetime(2026, 4, 10, 12, 0, 0),
            "success": 1,
            "latency_ms": 12.5,
            "host_name": "router-01",
        },
        {
            "host_id": 2,
            "_ts": datetime(2026, 4, 10, 12, 0, 0),
            "success": 0,
            "latency_ms": None,
            "host_name": "switch-02",
        },
    ])

    result = await ch.get_latest_ping_per_host([1, 2, 3])

    assert len(fake_query.calls) == 1
    sql = fake_query.last_sql.lower()
    assert "from ping_checks" in sql
    assert "argmax" in sql
    assert "group by host_id" in sql
    assert fake_query.last_params == {"hids": [1, 2, 3]}

    assert set(result.keys()) == {1, 2}
    assert result[1]["host_name"] == "router-01"
    assert result[2]["success"] == 0


async def test_get_latest_ping_per_host_no_filter(fake_query):
    fake_query.returns([])
    await ch.get_latest_ping_per_host()
    sql = fake_query.last_sql.lower()
    assert "where" not in sql  # global query, no host_id filter


async def test_get_ping_uptime_aggregation(fake_query):
    fake_query.returns([
        {"host_id": 1, "total": 100, "ok": 99, "avg_latency": 12.3},
        {"host_id": 2, "total": 60, "ok": 60, "avg_latency": 4.5},
        {"host_id": 3, "total": 0,  "ok": 0,  "avg_latency": None},
    ])

    result = await ch.get_ping_uptime([1, 2, 3], hours=24)

    sql = fake_query.last_sql.lower()
    assert "from ping_checks" in sql
    assert "sumif" in sql
    assert "tointervalhour" in sql
    assert fake_query.last_params == {"h": 24, "hids": [1, 2, 3]}

    assert result[1]["uptime_pct"] == 99.0
    assert result[2]["uptime_pct"] == 100.0
    assert result[3]["uptime_pct"] is None  # zero-total → no percentage
    assert result[1]["avg_latency"] == 12.3


async def test_get_ping_history(fake_query):
    fake_query.returns([
        {"timestamp": datetime(2026, 4, 10, 12, 0), "success": 1, "latency_ms": 11.0},
        {"timestamp": datetime(2026, 4, 10, 12, 1), "success": 0, "latency_ms": None},
    ])
    rows = await ch.get_ping_history(host_id=42, hours=6)

    assert fake_query.last_params == {"hid": 42, "h": 6}
    sql = fake_query.last_sql.lower()
    assert "where host_id =" in sql
    assert "order by timestamp asc" in sql
    assert len(rows) == 2


async def test_get_offline_hosts_since_returns_only_all_failed(fake_query):
    # Simulated CH response: only host 5 has all-zero recent results
    fake_query.returns([{"host_id": 5}])
    result = await ch.get_offline_hosts_since([1, 5, 9], min_failures=3)

    assert result == [5]
    sql = fake_query.last_sql.lower()
    assert "grouparray" in sql
    assert "arraysum" in sql
    assert fake_query.last_params == {"hids": [1, 5, 9], "n": 3}


async def test_get_ping_status_transitions(fake_query):
    fake_query.returns([
        {"timestamp": datetime(2026, 4, 10, 12, 0), "success": 0, "latency_ms": None, "prev_success": 1},
        {"timestamp": datetime(2026, 4, 10, 10, 0), "success": 1, "latency_ms": 12.0, "prev_success": None},
    ])

    result = await ch.get_ping_status_transitions(host_id=7, hours=6)

    assert len(fake_query.calls) == 1
    sql = fake_query.last_sql.lower()
    assert "from ping_checks" in sql
    assert "laginframe" in sql
    assert "prev_success" in sql
    assert fake_query.last_params == {"hid": 7, "h": 6}
    assert len(result) == 2
    assert result[0]["success"] == 0
    assert result[1]["prev_success"] is None


async def test_get_syslog_events_for_host_requires_matcher():
    # Without host_id / host_name / host_source_ip the helper short-circuits
    # to avoid returning the entire syslog firehose.
    result = await ch.get_syslog_events_for_host(
        host_id=None, host_name="", host_source_ip="",
        since=datetime(2026, 4, 10, 0, 0),
    )
    assert result == []


async def test_get_syslog_events_for_host_builds_or_matchers(fake_query):
    fake_query.returns([
        {
            "received_at": datetime(2026, 4, 10, 12, 0),
            "severity": 3, "facility": 4,
            "hostname": "router-01", "app_name": "sshd",
            "message": "Failed password",
        },
    ])
    since = datetime(2026, 4, 10, 0, 0)
    rows = await ch.get_syslog_events_for_host(
        host_id=42,
        host_name="router-01",
        host_source_ip="10.0.0.1",
        since=since,
        max_severity=4,
        limit=50,
    )

    sql = fake_query.last_sql.lower()
    assert "from syslog_messages" in sql
    assert "severity <=" in sql
    assert "order by received_at desc" in sql
    p = fake_query.last_params
    assert p["hid"] == 42
    assert p["hname"] == "router-01"
    assert p["hsip"] == "10.0.0.1"
    assert p["max_sev"] == 4
    assert p["lim"] == 50
    assert p["since"] == since
    assert len(rows) == 1
    assert rows[0]["app_name"] == "sshd"


async def test_get_offline_hosts_short_circuits_empty():
    # No CH call should happen with empty inputs.
    result = await ch.get_offline_hosts_since([], min_failures=3)
    assert result == []
    result = await ch.get_offline_hosts_since([1, 2], min_failures=0)
    assert result == []


# ── agent_metrics helpers ───────────────────────────────────────────────────


async def test_get_latest_agent_metrics(fake_query):
    fake_query.returns([
        {
            "agent_id": 7,
            "_ts": datetime(2026, 4, 10, 12, 0),
            "agent_name": "vm-01",
            "cpu_pct": 23.5,
            "mem_pct": 67.0,
            "mem_used_mb": 4096.0,
            "mem_total_mb": 8192.0,
            "disk_pct": 45.0,
            "load_1": 0.5,
            "load_5": 0.4,
            "load_15": 0.3,
            "uptime_s": 86400,
            "rx_bytes": 12345.0,
            "tx_bytes": 6789.0,
            "data_json": "{}",
        },
    ])

    result = await ch.get_latest_agent_metrics([7])

    assert 7 in result
    assert result[7]["agent_name"] == "vm-01"
    assert result[7]["cpu_pct"] == 23.5
    sql = fake_query.last_sql.lower()
    assert "from agent_metrics" in sql
    assert "argmax" in sql
    assert "group by agent_id" in sql
    assert fake_query.last_params == {"aids": [7]}


async def test_get_agent_history_with_hours(fake_query):
    fake_query.returns([
        {"timestamp": datetime(2026, 4, 10, 12, i), "cpu_pct": 10 + i}
        for i in range(5)
    ])
    rows = await ch.get_agent_history(agent_id=3, limit=20, hours=2)

    assert len(rows) == 5
    sql = fake_query.last_sql.lower()
    assert "from agent_metrics" in sql
    assert "order by timestamp desc" in sql
    assert "tointervalhour" in sql
    assert fake_query.last_params == {"aid": 3, "lim": 20, "h": 2}


async def test_get_agent_history_without_hours(fake_query):
    fake_query.returns([])
    await ch.get_agent_history(agent_id=3, limit=60)
    assert "h" not in fake_query.last_params
    sql = fake_query.last_sql.lower()
    assert "tointervalhour" not in sql


# ── bandwidth_metrics helpers ───────────────────────────────────────────────


async def test_get_latest_bandwidth_per_iface(fake_query):
    fake_query.returns([
        {
            "source_type": "agent",
            "source_id": "1",
            "interface_name": "eth0",
            "_ts": datetime(2026, 4, 10, 12, 0),
            "source_name": "vm-01",
            "rx_bytes": 1000,
            "tx_bytes": 500,
            "rx_rate_bps": 8000,
            "tx_rate_bps": 4000,
        },
    ])

    rows = await ch.get_latest_bandwidth_per_iface(source_type="agent", since_hours=1)

    sql = fake_query.last_sql.lower()
    assert "from bandwidth_metrics" in sql
    assert "argmax" in sql
    assert "group by source_type, source_id, interface_name" in sql
    assert fake_query.last_params == {"h": 1, "st": "agent"}
    assert rows[0]["source_name"] == "vm-01"


async def test_get_bandwidth_history_ch_filters_compose(fake_query):
    fake_query.returns([])
    await ch.get_bandwidth_history_ch(
        source_type="proxmox",
        source_id="42",
        interface_name="vmbr0",
        hours=12,
        limit=500,
    )

    p = fake_query.last_params
    assert p["st"] == "proxmox"
    assert p["sid"] == "42"
    assert p["iface"] == "vmbr0"
    assert p["h"] == 12
    assert p["lim"] == 500
    sql = fake_query.last_sql.lower()
    assert "from bandwidth_metrics" in sql
    assert "order by timestamp asc" in sql


async def test_get_previous_bandwidth_sample_returns_none_when_empty(fake_query):
    fake_query.returns([])
    result = await ch.get_previous_bandwidth_sample("agent", "1", "eth0")
    assert result is None


async def test_get_previous_bandwidth_sample_returns_first(fake_query):
    fake_query.returns([
        {"timestamp": datetime(2026, 4, 10, 11, 59), "rx_bytes": 100, "tx_bytes": 50},
    ])
    result = await ch.get_previous_bandwidth_sample("agent", "1", "eth0")
    assert result is not None
    assert result["rx_bytes"] == 100
    sql = fake_query.last_sql.lower()
    assert "limit 1" in sql
    assert "order by timestamp desc" in sql
    assert fake_query.last_params == {
        "st": "agent",
        "sid": "1",
        "iface": "eth0",
    }
